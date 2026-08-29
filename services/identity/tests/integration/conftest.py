"""A real PostgreSQL for the tests that need one (ADR-0018).

testcontainers rather than SQLite or a mocked repository. Everything worth
testing about this schema is something only PostgreSQL does: `citext` case
folding, `NULLS NOT DISTINCT`, `ON CONFLICT` inference, cascade behaviour. A
test against a different database proves that a different database works.

The container is built once per session and migrated once. Isolation between
tests comes from an outer transaction that is rolled back afterwards, so each
test starts from the schema as the migrations left it. The session inside joins
that transaction through a savepoint, which means production code can call
``commit()`` normally and still be undone — the test observes the same commit
semantics the service does, without the cost of recreating the schema.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.community.postgres import PostgresContainer

from identity.infrastructure.db.session import SessionFactory

SERVICE_ROOT = Path(__file__).resolve().parents[2]

# Pinned to the version the local stack and the cluster run. A schema that only
# behaves on latest is a schema that breaks on the next deploy.
POSTGRES_IMAGE = "postgres:16-alpine"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A migrated, empty identity database. One container for the whole run."""
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        url = container.get_connection_url()
        _upgrade_to_head(url)
        yield url


def _upgrade_to_head(url: str) -> None:
    """Apply the real migrations, not ``Base.metadata.create_all``.

    They are what runs against production, and the difference between the two
    is exactly the class of defect these tests exist to catch: a model changed
    without a migration passes ``create_all`` and fails on deploy.
    """
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


@pytest_asyncio.fixture
async def db_engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine: AsyncEngine) -> AsyncIterator[SessionFactory]:
    """Sessions bound to one connection whose transaction is rolled back.

    Everything a test writes goes through this connection, so ``db_session``
    below and any code that opens its own unit of work see the same data — and
    all of it disappears when the test ends.
    """
    async with db_engine.connect() as connection:
        outer = await connection.begin()
        yield async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            # The session's commits become savepoint releases inside the outer
            # transaction, which the rollback below then discards. Production
            # code commits normally; the test still starts from a clean schema.
            join_transaction_mode="create_savepoint",
        )
        await outer.rollback()


@pytest_asyncio.fixture
async def db_session(session_factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """One session, for tests that are handed one rather than opening it."""
    async with session_factory() as session:
        yield session
