"""``python -m identity.cli <command>``.

Kept to argv parsing and wiring. What a command actually does lives in the layer
it belongs to, so that it is testable without a process: the seed is a function
taking a session, and this file only decides which session it gets and where
the transaction begins (CODING_STANDARDS 7).

Exit codes: 0 on success, 2 on a usage error, 78 on a bad configuration — the
value ``get_settings`` terminates with, so a Job that fails to start is
distinguishable from one that ran and failed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Final

from identity.infrastructure.config import get_settings
from identity.infrastructure.db.engine import create_engine
from identity.infrastructure.db.seed import SeedReport, seed_system_roles
from identity.infrastructure.db.session import create_session_factory, transaction
from identity.infrastructure.logging import configure_logging

logger = logging.getLogger(__name__)

USAGE: Final = "usage: python -m identity.cli seed"
EXIT_USAGE: Final = 2


async def run_seed() -> SeedReport:
    """Reconcile system roles and permissions against the catalogue (P1-T10).

    Safe to run on every deploy: the reconciliation is convergent, so a second
    run reports no changes rather than duplicating the first.
    """
    settings = get_settings()
    engine = create_engine(
        str(settings.database_dsn),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
    )
    try:
        factory = create_session_factory(engine)
        async with transaction(factory) as session:
            return await seed_system_roles(session)
    finally:
        # A command that leaves connections open delays its own exit, and a
        # migration Job that does not exit blocks the sync that spawned it.
        await engine.dispose()


def _report(report: SeedReport) -> None:
    logger.info(
        "seed finished",
        extra={
            "changed": report.has_changes,
            "permissions_created": list(report.permissions_created),
            "roles_created": list(report.roles_created),
            "grants_added": [f"{role}:{permission}" for role, permission in report.grants_added],
            "grants_removed": [
                f"{role}:{permission}" for role, permission in report.grants_removed
            ],
            "unknown_permissions": list(report.unknown_permissions),
        },
    )


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] != "seed":
        logger.error("bad usage", extra={"argv": argv, "usage": USAGE})
        return EXIT_USAGE

    _report(asyncio.run(run_seed()))
    return 0


if __name__ == "__main__":
    _settings = get_settings()
    configure_logging(
        service_name=_settings.service_name,
        service_version=_settings.service_version,
        environment=_settings.environment.value,
        level=_settings.log_level.value,
    )
    raise SystemExit(main(sys.argv[1:]))
