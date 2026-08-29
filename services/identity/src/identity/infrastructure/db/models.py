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
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from chassis import uuid7
from identity.domain.user import OAuthProvider, UserStatus
from identity.infrastructure.db.base import Base

# Length of a SHA-256 digest in hexadecimal. Named because the number appears in
# a column definition, where "64" alone says nothing about what is stored.
SHA256_HEX_LENGTH: Final = 64


def _enum(enum_type: type[UserStatus | OAuthProvider], name: str) -> Enum:
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
