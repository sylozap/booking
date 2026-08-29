"""Enable the PostgreSQL extensions this schema depends on

Revision ID: 0001
Revises:

The first revision creates no table. It makes the database capable of holding
the ones that follow: `users.email` is `citext`, and the type does not exist
until the extension is installed.

The extension is also created by the local compose init script, so this appears
redundant there — and is not. That script never runs anywhere else; in the
cluster the database is provisioned empty and this revision is the only thing
that installs the type. Making the two converge is the point: the schema a
migration produces must not depend on how the database was created.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # citext is a trusted extension since PostgreSQL 13, so the database owner
    # can install it without superuser rights — which matters, because the
    # migration Job connects as the service role, not as postgres.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")


def downgrade() -> None:
    # RESTRICT, not CASCADE: if a column still uses the type, that is a
    # migration applied out of order, and dropping the column with it would
    # turn a loud failure into silent data loss.
    op.execute("DROP EXTENSION IF EXISTS citext RESTRICT")
