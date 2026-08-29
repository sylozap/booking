"""Composition root: builds the application and wires the layers together.

This is the one module allowed to import every layer. Dependencies are created
here and handed to the code that uses them, which is what lets ``api`` depend
on a Protocol in ``application`` while the Postgres and Redis adapters that
satisfy it live in ``infrastructure`` (CODING_STANDARDS 2.2, 7).

Order matters at startup and is not arbitrary:

1. configuration — everything else is derived from it, and an invalid
   configuration must kill the process before it opens a socket;
2. logging — so that any later failure is reported in a queryable form;
3. tracing — the provider must be global before FastAPI is instrumented;
4. clients and the application itself.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI
from prometheus_client import CollectorRegistry

from identity.api.errors import register_error_handlers
from identity.api.health import router as health_router
from identity.api.router import api_v1_router
from identity.infrastructure.cache import CacheProbe, create_client
from identity.infrastructure.config import Settings, get_settings
from identity.infrastructure.db.engine import DatabaseProbe, create_engine
from identity.infrastructure.db.session import create_session_factory
from identity.infrastructure.logging import configure_logging
from identity.infrastructure.metrics import (
    METRICS_PATH,
    HttpMetrics,
    MetricsMiddleware,
    register_pool_metrics,
    render_metrics,
)
from identity.infrastructure.tracing import (
    configure_tracing,
    instrument_app,
    instrument_database,
    instrument_redis,
)

logger = logging.getLogger(__name__)

TITLE: Final = "identity"
DESCRIPTION: Final = "Users, OAuth, JWT issuing and RBAC."


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Takes settings as an argument so that a test can construct an app without
    the process environment being right; production passes nothing and gets the
    validated singleton.
    """
    settings = settings or get_settings()

    configure_logging(
        service_name=settings.service_name,
        service_version=settings.service_version,
        environment=settings.environment.value,
        level=settings.log_level.value,
    )
    configure_tracing(
        service_name=settings.service_name,
        service_version=settings.service_version,
        environment=settings.environment.value,
        otlp_endpoint=settings.otlp_endpoint,
    )

    engine = create_engine(
        str(settings.database_dsn),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
    )
    redis_client = create_client(str(settings.redis_dsn))

    instrument_database(engine)
    instrument_redis()

    registry = CollectorRegistry()
    http_metrics = HttpMetrics(registry, service_name=settings.service_name)
    register_pool_metrics(registry, engine, service_name=settings.service_name)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app  # Required by the lifespan protocol; the closure has what it needs.
        logger.info(
            "service starting",
            extra={
                "environment": settings.environment.value,
                "version": settings.service_version,
            },
        )
        yield
        # Disposal is not optional: without it the process can exit holding
        # server-side connections open until the database times them out.
        await engine.dispose()
        await redis_client.aclose()
        logger.info("service stopped")

    app = FastAPI(
        title=TITLE,
        description=DESCRIPTION,
        version=settings.service_version,
        lifespan=lifespan,
        # The interactive docs are the only place the schema is served from;
        # in production it is one more unauthenticated surface.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.state.settings = settings
    # Scenarios open their own unit of work from this factory; the engine and
    # its pool stay owned by the application (CODING_STANDARDS 7).
    app.state.session_factory = create_session_factory(engine)
    app.state.metrics_registry = registry
    app.state.readiness_probes = (DatabaseProbe(engine), CacheProbe(redis_client))
    app.state.readiness_timeout_seconds = settings.readiness_timeout_seconds

    app.add_middleware(MetricsMiddleware, metrics=http_metrics)

    register_error_handlers(app)

    app.include_router(health_router)
    # add_api_route, not add_route: only FastAPI's route puts itself into the
    # ASGI scope, and without it the metrics middleware cannot name the route
    # it is measuring — /metrics would be reported as "unmatched".
    app.add_api_route(METRICS_PATH, render_metrics, methods=["GET"], include_in_schema=False)
    app.include_router(api_v1_router)

    # After the routes: the instrumentor reads them to build span names.
    instrument_app(app)

    return app


def run() -> None:
    """Entry point for ``python -m identity``."""
    import uvicorn  # noqa: PLC0415  # Importing the app must not drag in a server.

    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.http_host,
        port=settings.http_port,
        # Logging is configured in create_app; letting uvicorn install its own
        # would replace the JSON handler with a plain-text one.
        log_config=None,
        access_log=False,
    )
