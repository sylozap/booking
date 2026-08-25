"""OpenTelemetry tracing, exported to the collector over OTLP (D8).

The service knows about one destination — the collector — and nothing about
Tempo, Loki or Prometheus. Swapping a backend is a change in
deploy/local/otel/collector.yaml, not in five services (ADR-0008).

Instrumentation is explicit, one call per library. Auto-instrumentation by
agent would wrap whatever happens to be installed, which is exactly the kind of
implicit behaviour the standards rule out: the spans this service produces
should be readable from this file.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Health and metrics endpoints are polled every few seconds by kubelet and
# Prometheus. Tracing them buys nothing and drowns the real traffic.
EXCLUDED_URLS = "healthz,readyz,metrics"


def configure_tracing(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    otlp_endpoint: str,
) -> TracerProvider:
    """Install the global tracer provider.

    Resource attributes are the join key for the whole stack: ``service.name``
    is what Grafana matches on when jumping from a trace to that service's logs
    and metrics, so it must be identical in all three signals.
    """
    installed = trace.get_tracer_provider()
    if isinstance(installed, TracerProvider):
        # Already configured. OpenTelemetry refuses to replace a provider and
        # only logs a warning, so a second create_app in one process would
        # otherwise silently keep the first configuration while looking as
        # though it had applied a new one. Tests rely on this to install an
        # in-memory exporter before building the application.
        logger.debug("tracer provider already installed; leaving it in place")
        return installed

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )

    provider = TracerProvider(resource=resource)
    # Batched, not simple: a span export on the request path would put the
    # collector's latency into every response, and its outage into every error
    # rate.
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)

    logger.info("tracing configured", extra={"otlp_endpoint": otlp_endpoint})
    return provider


def instrument_app(app: FastAPI) -> None:
    FastAPIInstrumentor.instrument_app(app, excluded_urls=EXCLUDED_URLS)


def instrument_database(engine: AsyncEngine) -> None:
    """Instrument the engine so a query span nests inside the request span.

    ``enable_commenter`` writes the trace id into the SQL as a comment, which
    is what lets a slow query found in pg_stat_statements be traced back to the
    request that issued it.

    Guarded because an instrumentor is a per-class singleton: calling it twice
    warns and does nothing. One process owns one engine, so this is the whole
    of it — a second engine in the same process would go untraced, and that is
    a shape this service does not have.
    """
    instrumentor = SQLAlchemyInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        return
    instrumentor.instrument(
        engine=engine.sync_engine,
        enable_commenter=True,
        commenter_options={"opentelemetry_values": True},
    )


def instrument_redis() -> None:
    """Patch the Redis client library. Global, and therefore done once."""
    instrumentor = RedisInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument()
