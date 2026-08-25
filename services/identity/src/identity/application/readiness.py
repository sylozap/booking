"""The port the readiness probe answers through.

Declared here rather than in ``api`` because readiness is a property of the
service, not of its HTTP surface: the same checks will gate a consumer that
serves no HTTP at all. The router depends on this Protocol; the concrete
Postgres and Redis probes live in infrastructure and are injected at startup,
which is what keeps ``api`` from importing ``infrastructure``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    name: str
    is_ready: bool
    detail: str | None = None


@runtime_checkable
class DependencyProbe(Protocol):
    """One external dependency, asked whether it can be used right now."""

    @property
    def name(self) -> str: ...

    async def check(self) -> DependencyStatus: ...


async def gather_readiness(
    probes: tuple[DependencyProbe, ...], *, timeout_seconds: float
) -> tuple[DependencyStatus, ...]:
    """Run every probe concurrently under one deadline.

    Concurrently, because the probe budget is a single kubelet timeout and
    checks must not queue behind each other. Under a deadline, because a probe
    that hangs is worse than one that fails: Kubernetes keeps routing traffic
    to a pod whose readiness never answers.
    """

    async def run(probe: DependencyProbe) -> DependencyStatus:
        try:
            async with asyncio.timeout(timeout_seconds):
                return await probe.check()
        except TimeoutError:
            return DependencyStatus(
                name=probe.name,
                is_ready=False,
                detail=f"did not answer within {timeout_seconds}s",
            )
        except Exception as error:  # noqa: BLE001  # A probe reports failure; it never propagates it.
            # Not re-raised on purpose, and this is the one place that is
            # correct: an unreachable dependency is the answer the probe exists
            # to give, and letting it escape would turn a 503 into a 500.
            logger.warning(
                "readiness probe failed",
                extra={"probe": probe.name, "error_type": type(error).__name__},
            )
            return DependencyStatus(name=probe.name, is_ready=False, detail=str(error))

    return tuple(await asyncio.gather(*(run(probe) for probe in probes)))
