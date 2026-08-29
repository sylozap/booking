"""The migration chain applies, reverts and matches the models (P1-T08).

The chain is what runs against production; the models are what the code writes
through. When the two drift, nothing fails until a deploy does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from identity.infrastructure.db.base import Base

SERVICE_ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:16-alpine"

#: Alembic's own bookkeeping. Present even when the schema is empty.
VERSION_TABLE = "alembic_version"

EXPECTED_TABLES: frozenset[str] = frozenset()


def _table_names(url: str) -> frozenset[str]:
    """The tables that exist right now, read over a connection of its own.

    ``asyncio.run`` per call rather than one async test: Alembic's env.py opens
    its own event loop, so the migrations cannot be driven from inside a
    running one. Only the async driver is installed, on purpose — a synchronous
    one would exist solely to be used here and nowhere in the service.
    """

    async def read() -> frozenset[str]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync: frozenset(inspect(sync).get_table_names())
                )
        finally:
            await engine.dispose()

    return asyncio.run(read())


def alembic_config(url: str) -> Config:
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.mark.asyncio
async def test_connection_reaches_a_migrated_database(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as connection:
        answer = await connection.scalar(text("SELECT 1"))

    assert answer == 1


@pytest.mark.asyncio
async def test_upgrade_creates_every_table(db_engine: AsyncEngine) -> None:
    async with db_engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: frozenset(inspect(sync).get_table_names()))

    assert tables >= EXPECTED_TABLES


@pytest.mark.asyncio
async def test_schema_matches_the_models(db_engine: AsyncEngine) -> None:
    """Autogenerate finds nothing: no model changed without a migration."""

    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "compare_server_default": True}
        )
        return list(compare_metadata(context, Base.metadata))

    async with db_engine.connect() as connection:
        differences = await connection.run_sync(diff)

    assert differences == []


def test_history_is_linear() -> None:
    """One writer per service means one line of history and no merge points."""
    script = ScriptDirectory.from_config(alembic_config("postgresql://unused"))

    heads = script.get_heads()
    branch_points = [
        revision.revision for revision in script.walk_revisions() if revision.is_branch_point
    ]

    assert len(heads) == 1
    assert branch_points == []


def test_every_revision_is_reversible() -> None:
    """Downgrade to base leaves nothing behind, and upgrade rebuilds it.

    Its own container: the round trip destroys the schema, and the shared one
    is what every other test in this suite reads. Slower than reusing it, and
    the only honest way to assert that `downgrade` was ever run.
    """
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as container:
        url = container.get_connection_url()
        config = alembic_config(url)

        command.upgrade(config, "head")
        after_upgrade = _table_names(url)

        command.downgrade(config, "base")
        after_downgrade = _table_names(url)

        command.upgrade(config, "head")
        after_second_upgrade = _table_names(url)

    assert after_upgrade >= EXPECTED_TABLES
    assert after_downgrade == frozenset({VERSION_TABLE})
    assert after_second_upgrade == after_upgrade
