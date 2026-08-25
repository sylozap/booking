"""Liveness and readiness. Two endpoints, two different questions.

``/healthz`` asks whether the process is sane. It touches nothing external,
because the only action Kubernetes takes on a failed liveness probe is a
restart, and restarting a healthy process because its database is down turns
one outage into a crash loop.

``/readyz`` asks whether the service can serve a request right now, which
includes its dependencies. Failing it removes the pod from the load balancer
and leaves it running — the correct response to a dependency that will come
back.

Neither path is versioned: they are an operational contract with the platform,
not part of the API that clients consume (D45 governs ``/api/v1``, not these).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from identity.application.readiness import DependencyProbe, DependencyStatus, gather_readiness

router = APIRouter(tags=["operations"])

LIVENESS_PATH: Final = "/healthz"
READINESS_PATH: Final = "/readyz"


@router.get(LIVENESS_PATH, include_in_schema=False)
async def liveness() -> Response:
    """200 for as long as the event loop can answer at all."""
    return JSONResponse(status_code=HTTPStatus.OK, content={"status": "alive"})


@router.get(READINESS_PATH, include_in_schema=False)
async def readiness(request: Request) -> Response:
    """200 when every dependency answers, 503 when any does not."""
    probes = request.app.state.readiness_probes
    timeout_seconds = request.app.state.readiness_timeout_seconds

    statuses = await gather_readiness(
        tuple(probe for probe in probes if isinstance(probe, DependencyProbe)),
        timeout_seconds=float(timeout_seconds),
    )
    is_ready = all(status.is_ready for status in statuses)

    return JSONResponse(
        status_code=HTTPStatus.OK if is_ready else HTTPStatus.SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if is_ready else "not_ready",
            "dependencies": [_render(status) for status in statuses],
        },
    )


def _render(status: DependencyStatus) -> dict[str, object]:
    rendered: dict[str, object] = {"name": status.name, "ready": status.is_ready}
    if status.detail is not None:
        rendered["detail"] = status.detail
    return rendered
