"""RED metrics for HTTP, plus the database pool (D8).

Rate, errors and duration for every route, and the pool gauges that explain
most latency incidents that are not the database's fault: requests queue on a
pool that is too small long before the database itself is loaded.

Prometheus pulls from ``/metrics``; traces and logs are pushed to the
collector. Metrics are the one signal where pull is worth it — a target that
stops answering is itself the signal, whereas a service that stops pushing is
indistinguishable from one with nothing to say.

Every metric here answers a question the standards demand be answerable before
it is created (CODING_STANDARDS 11): is the service being used, is it failing,
is it slow, and is the pool the reason.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import TYPE_CHECKING, Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

METRICS_PATH: Final = "/metrics"

# Buckets chosen against the latency targets in ADR-0012 rather than left at
# the library default, so the p95 that matters falls between two buckets and
# not inside the widest one.
LATENCY_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class HttpMetrics:
    """The three RED series, on one registry.

    A registry per application instead of the library global: two tests that
    both build an app would otherwise collide on duplicate metric names, and
    the failure reads as an unrelated import error.
    """

    def __init__(self, registry: CollectorRegistry, *, service_name: str) -> None:
        self.requests_total = Counter(
            f"{service_name}_http_requests_total",
            "HTTP requests by method, route and status.",
            labelnames=("method", "route", "status"),
            registry=registry,
        )
        self.request_duration_seconds = Histogram(
            f"{service_name}_http_request_duration_seconds",
            "HTTP request duration in seconds.",
            labelnames=("method", "route"),
            buckets=LATENCY_BUCKETS,
            registry=registry,
        )
        self.requests_in_flight = Gauge(
            f"{service_name}_http_requests_in_flight",
            "HTTP requests currently being served.",
            registry=registry,
        )


class MetricsMiddleware:
    """Records one observation per request.

    Pure ASGI rather than BaseHTTPMiddleware: the latter wraps the response in
    an extra task, which distorts the very duration this middleware exists to
    measure.
    """

    def __init__(self, app: ASGIApp, metrics: HttpMetrics) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(
        self,
        scope: dict[str, object],
        receive: Callable[[], Awaitable[dict[str, object]]],
        send: Callable[[dict[str, object]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)  # type: ignore[arg-type]  # ASGI scope is untyped by design.
            return

        method = str(scope.get("method", "UNKNOWN"))
        status_holder: list[int] = []

        async def capture(message: dict[str, object]) -> None:
            if message.get("type") == "http.response.start":
                status = message.get("status")
                if isinstance(status, int):
                    status_holder.append(status)
            await send(message)

        self._metrics.requests_in_flight.inc()
        started = perf_counter()
        try:
            await self._app(scope, receive, capture)  # type: ignore[arg-type]  # ASGI scope is untyped by design.
        finally:
            elapsed = perf_counter() - started
            self._metrics.requests_in_flight.dec()

            # The route template, never the raw path: /users/{id} is one series,
            # while /users/<uuid> would be one series per user and would take
            # the Prometheus instance down within a day.
            route = _route_template(scope)
            status = status_holder[0] if status_holder else 500

            self._metrics.requests_total.labels(
                method=method, route=route, status=str(status)
            ).inc()
            self._metrics.request_duration_seconds.labels(method=method, route=route).observe(
                elapsed
            )


def _route_template(scope: dict[str, object]) -> str:
    route = scope.get("route")
    path_format = getattr(route, "path_format", None)
    if isinstance(path_format, str):
        return path_format
    # No route matched: a 404 on an arbitrary path. Bucketing them all together
    # keeps a scanner from creating a series per probed URL.
    return "unmatched"


def register_pool_metrics(
    registry: CollectorRegistry, engine: AsyncEngine, *, service_name: str
) -> None:
    """Expose the connection pool as gauges read at scrape time.

    Callbacks rather than periodic sampling: the pool already knows these
    numbers, and a background task to copy them into a gauge would be one more
    thing that can silently stop.
    """
    checked_out = Gauge(
        f"{service_name}_db_pool_connections_in_use",
        "Connections currently checked out of the pool.",
        registry=registry,
    )
    available = Gauge(
        f"{service_name}_db_pool_connections_available",
        "Connections open and idle in the pool.",
        registry=registry,
    )
    overflow = Gauge(
        f"{service_name}_db_pool_overflow",
        "Connections open beyond pool_size; sustained non-zero means the pool is too small.",
        registry=registry,
    )

    pool = engine.pool

    def read(name: str) -> float:
        return float(getattr(pool, name, lambda: 0)())

    checked_out.set_function(lambda: read("checkedout"))
    available.set_function(lambda: read("checkedin"))
    # SQLAlchemy counts overflow from -pool_size upwards, so the raw value is
    # negative for as long as the pool is not full. Reporting that verbatim
    # would make the gauge read -9 for a healthy pool and contradict its own
    # help text; what an operator needs is how far past pool_size we are.
    overflow.set_function(lambda: max(read("overflow"), 0.0))


def render_metrics(request: Request) -> Response:
    registry = request.app.state.metrics_registry
    assert isinstance(registry, CollectorRegistry)  # noqa: S101  # Set in the composition root.

    # The content type must match the body: generate_latest emits the classic
    # text exposition format, and announcing OpenMetrics instead would make a
    # strict scraper misparse it.
    return Response(content=generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
