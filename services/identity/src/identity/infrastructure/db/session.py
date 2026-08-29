"""Sessions and the unit of work.

A session is one unit of work with one transaction, opened by the scenario that
needs it and closed when that scenario ends (CODING_STANDARDS 7). Neither the
repository nor the router decides where a transaction starts: a repository that
commits cannot be composed with another repository in the same change, and a
router that commits puts the boundary in the one layer that must not know about
persistence at all.

Autocommit is off, autoflush is off, and both are deliberate. Autoflush sends
pending changes at unexpected moments — typically in the middle of a read, where
an integrity error then surfaces attributed to a ``SELECT``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

SessionFactory = async_sessionmaker[AsyncSession]


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(
        engine,
        # Attributes stay readable after commit. With expiry on, touching any
        # field of a returned object emits a lazy refresh — which, under
        # lazy="raise", is an error rather than a hidden query, but an error at
        # the point where the object is being turned into a response.
        expire_on_commit=False,
        autoflush=False,
        autobegin=True,
    )


@asynccontextmanager
async def transaction(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """One transaction: commits on success, rolls back on any exception.

    The rollback is what makes "the state change and its outbox record are
    written together, or neither is" true (ADR-0005). It must cover the whole
    scenario, so this context manager wraps the scenario, not a single query.
    """
    async with factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        await session.commit()
