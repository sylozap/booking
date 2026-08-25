"""Async SQLAlchemy engine and its readiness probe.

Only the connection lives here for now; sessions, models and migrations arrive
with P1-T08. What matters at this point is that the pool is created once at
startup and disposed at shutdown, and that something can ask it whether the
database is reachable.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from identity.application.readiness import DependencyStatus

# Cheapest statement that proves a usable connection: it round-trips through
# the pool and the wire without touching a table, so it stays valid before the
# first migration has ever run.
LIVENESS_STATEMENT: Final = text("SELECT 1")


def create_engine(
    dsn: str,
    *,
    pool_size: int,
    max_overflow: int,
) -> AsyncEngine:
    return create_async_engine(
        dsn,
        pool_size=pool_size,
        max_overflow=max_overflow,
        # Recycle below any sensible idle timeout on the server side: a
        # connection the database closed and the pool still believes in fails
        # the next request instead of this check.
        pool_recycle=1800,
        pool_pre_ping=True,
        echo=False,
    )


class DatabaseProbe:
    """Readiness probe for PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "postgres"

    async def check(self) -> DependencyStatus:
        async with self._engine.connect() as connection:
            await connection.execute(LIVENESS_STATEMENT)
        return DependencyStatus(name=self.name, is_ready=True)
