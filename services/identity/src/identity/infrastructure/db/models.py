"""ORM models: the database schema as SQLAlchemy sees it.

These are one of the three representations a thing has (CODING_STANDARDS 5) and
the only one that knows about columns. They never leave this layer: a router
that returns an ORM model has published the schema of the database as its API
contract, and the next migration becomes a breaking change for clients.

Two conventions hold throughout.

**Constraints belong in the database.** Uniqueness of an email, the shape of a
status value, the impossibility of a role assignment referencing a deleted user
— each is expressed as a constraint, not as a check in Python. Application code
runs in several replicas concurrently; a ``SELECT`` followed by an ``INSERT``
does not exclude anything.

**No lazy loading.** Every relationship is ``lazy="raise"``. An N+1 becomes an
exception at the first access instead of an invisible query per row, and the
fix — an explicit ``selectinload`` — is written where the data is needed.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Final

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chassis import uuid7
from identity.domain.audit import AuditAction
from identity.domain.user import OAuthProvider, UserStatus
from identity.infrastructure.db.base import Base

# Length of a SHA-256 digest in hexadecimal. Named because the number appears in
# a column definition, where "64" alone says nothing about what is stored.
SHA256_HEX_LENGTH: Final = 64


def _enum(enum_type: type[UserStatus | OAuthProvider | AuditAction], name: str) -> Enum:
    """A CHECK-constrained VARCHAR rather than a native PostgreSQL enum.

    Adding a value to a native enum requires ALTER TYPE, which until
    PostgreSQL 12 could not run in a transaction and still cannot be reverted;
    removing one requires recreating the type and every column that uses it.
    Under expand/contract (D55) the value set changes more often than the
    columns do, and a CHECK constraint is an ordinary, reversible migration.
    """
    return Enum(enum_type, name=name, native_enum=False, validate_strings=True)


class User(Base):
    """A person. One row per human, shared by every organization they touch.

    ``password_hash`` is nullable because an account created through Google has
    no password at all — not an empty one. Requiring a placeholder would make
    "has a usable password" unrepresentable, and the login path would have to
    guess.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # citext, so that two addresses differing only in case are the same address
    # to the unique index. Doing it with lower() in every query means the one
    # query that forgets creates the duplicate account.
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, default=None)
    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32), default=None)
    avatar_url: Mapped[str | None] = mapped_column(Text, default=None)
    # An IANA zone name, not an offset: an offset is a property of a date, and
    # storing it breaks every schedule the next time the clocks change (D13).
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[UserStatus] = mapped_column(
        _enum(UserStatus, "user_status"), default=UserStatus.ACTIVE
    )
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    # A user with neither a password nor a linked provider cannot be signed in
    # to by anyone. That invariant spans two tables, and a CHECK constraint
    # cannot: PostgreSQL forbids subqueries in one. It is therefore upheld by
    # the registration and OAuth scenarios, which is the one place in this
    # schema where a rule is not backed by the database.
    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        back_populates="user", lazy="raise", cascade="all, delete-orphan"
    )


class OAuthAccount(Base):
    """A link between a user and their account at an external provider.

    Separate from ``users`` rather than a pair of columns on it, because one
    person can link several providers, and because the pair
    (provider, provider_user_id) needs its own uniqueness — the guarantee that
    one Google account cannot end up attached to two local users.
    """

    __tablename__ = "oauth_accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[OAuthProvider] = mapped_column(_enum(OAuthProvider, "oauth_provider"))
    # The provider's own identifier for the person ("sub" in an OIDC token).
    # Not the email: an email at the provider can be changed and reassigned,
    # the subject identifier cannot.
    provider_user_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

    user: Mapped[User] = relationship(back_populates="oauth_accounts", lazy="raise")

    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)


class Permission(Base):
    """One capability. Access is always checked against these, never a role."""

    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # A plain string, not a CHECK-constrained enum: the catalogue of codes lives
    # in identity.domain.access and is reconciled into this table by the seed.
    # Encoding it twice would mean a schema migration for every new capability.
    code: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")


class Role(Base):
    """A named bundle of permissions.

    ``is_system`` marks the roles the platform defines for itself. The seed owns
    them and reconciles their permissions on every run; an administrator may
    neither edit nor delete one, because the code assumes CLIENT means what it
    means.
    """

    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Open on purpose. System roles come from the domain catalogue; an
    # organization-defined role would be a row here with is_system false, and a
    # CHECK constraint over the enum would make that unrepresentable.
    code: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_system: Mapped[bool] = mapped_column(default=False)

    permissions: Mapped[list[Permission]] = relationship(secondary="role_permissions", lazy="raise")


class RolePermission(Base):
    """Which permissions a role carries. A plain many-to-many."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )


class UserRole(Base):
    """A grant: this user holds this role in this organization (D20).

    ``tenant_id`` is the organization the grant applies to, and it is what makes
    the model multi-tenant: the same person is a CLIENT in one organization and
    an ORG_ADMIN in another, and neither grant implies anything about the other.
    It is NULL only for platform-wide roles — today, SUPER_ADMIN alone.

    The uniqueness of (user, role, tenant) cannot be a primary key, because a
    primary key column is NOT NULL and this one is nullable by design. It is a
    unique constraint instead — declared NULLS NOT DISTINCT, without which
    PostgreSQL treats every NULL as its own value and the same platform-wide
    grant could be inserted any number of times.
    """

    __tablename__ = "user_roles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    granted_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    # Who granted it. NULL when the grant came from an event rather than from a
    # person — a specialist invitation accepted in `catalog`, for instance.
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            "tenant_id",
            postgresql_nulls_not_distinct=True,
        ),
        # The hot path: "what may this user do in this organization", asked on
        # every token issue.
        Index("ix_user_roles_user_id_tenant_id", "user_id", "tenant_id"),
    )


class RefreshToken(Base):
    """One issued refresh token, stored as a hash (D21).

    The plaintext token exists only in the response that carried it and in the
    client. What is kept here is a SHA-256 digest: enough to recognise a token
    that comes back, useless to anyone who reads the table. SHA-256 rather than
    argon2 precisely because this is not a password — the token is
    high-entropy random, so there is no dictionary to run against the digest,
    and latency on every renewal would pay for nothing.

    ``family_id`` links every token descended from one login. Rotation replaces
    a token and records ``replaced_by_id``; presenting an already-replaced
    token means two parties hold it, and the entire family is revoked rather
    than just that token — the thief would otherwise keep the newest one.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(SHA256_HEX_LENGTH), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), default=None
    )
    issued_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[dt.datetime] = mapped_column()
    revoked_at: Mapped[dt.datetime | None] = mapped_column(default=None)
    # Shown to the user when listing their sessions, and the only thing that
    # distinguishes one device from another after the fact.
    user_agent: Mapped[str | None] = mapped_column(String(400), default=None)
    ip: Mapped[str | None] = mapped_column(INET, default=None)

    __table_args__ = (
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        # Read only by the sweeper that removes expired tokens.
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )


class AuditEntry(Base):
    """One permission change, recorded for later investigation (D48).

    Append-only by convention and by the absence of anything that updates it.
    The actor may be NULL — a grant issued by an event has no human behind it —
    but the subject and the moment never are: an entry that cannot answer "who
    was affected, and when" answers nothing.

    ``role_code`` and ``tenant_id`` are copied in rather than referenced. The
    log has to stay readable after the role is deleted, which is exactly the
    change someone will want to investigate.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # clock_timestamp(), not now(): PostgreSQL's now() is the moment the
    # transaction began, so a long-running one — a batch role sync — would
    # stamp every entry with the batch's start time rather than the action's.
    # For a log whose whole purpose is answering "when did this happen", the
    # wall clock at the statement is the honest answer.
    occurred_at: Mapped[dt.datetime] = mapped_column(server_default=func.clock_timestamp())
    action: Mapped[AuditAction] = mapped_column(_enum(AuditAction, "audit_action"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    subject_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_code: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    # Free-form context for the action. Never personal data: the log is read by
    # operators, and ADR-0017 keeps PII out of everything they read.
    detail: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        # "Everything that happened to this person's access", the question an
        # investigation actually starts from.
        Index("ix_audit_log_subject_user_id_occurred_at", "subject_user_id", "occurred_at"),
    )
