"""Create audit_log

Revision ID: 0005
Revises: 0004

Permission changes, recorded for later investigation (D48).

Business actions are already reconstructible from the outbox: each publishes an
event carrying who did what. A role grant publishes nothing a consumer reacts
to, and it is the first thing an investigation asks about — hence one table, for
this class of action only.

`role_code` and `tenant_id` are copied in rather than referenced: the log has to
stay readable after the role is deleted, which is exactly the change someone
will want to look into.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        # clock_timestamp(), not now(): now() is the transaction's start time,
        # so every entry written by a long transaction would claim to have
        # happened when that transaction opened.
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "action",
            sa.Enum("ROLE_GRANTED", "ROLE_REVOKED", name="audit_action", native_enum=False),
            nullable=False,
        ),
        # Nullable: a grant issued by an event — a specialist invitation
        # accepted in `catalog` — has no human behind it.
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("subject_user_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_log_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["users.id"],
            name=op.f("fk_audit_log_subject_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    # "Everything that happened to this person's access" — where an
    # investigation actually starts.
    op.create_index(
        "ix_audit_log_subject_user_id_occurred_at",
        "audit_log",
        ["subject_user_id", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_subject_user_id_occurred_at", table_name="audit_log")
    op.drop_table("audit_log")
