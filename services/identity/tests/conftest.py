"""Fixtures shared by the identity test suite.

The application is built against dependencies that are deliberately
unreachable: port 1 refuses a connection immediately, which is the fastest
honest way to have a real DatabaseProbe report a real failure. Tests that need
a working database arrive with P1-T08 and testcontainers; nothing here fakes a
repository of our own (CODING_STANDARDS 14).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from identity.infrastructure.config import Environment, Settings
from identity.main import create_app

# A closed port: connecting fails in microseconds and needs no container.
UNREACHABLE_POSTGRES_DSN = "postgresql+asyncpg://identity:secret@127.0.0.1:1/identity"
UNREACHABLE_REDIS_DSN = "redis://127.0.0.1:1/0"


def build_settings(**overrides: object) -> Settings:
    """Settings for a test, with every required field filled in."""
    values: dict[str, object] = {
        "environment": Environment.LOCAL,
        "database_dsn": UNREACHABLE_POSTGRES_DSN,
        "redis_dsn": UNREACHABLE_REDIS_DSN,
        "otlp_endpoint": "http://127.0.0.1:4317",
        "readiness_timeout_seconds": 1.0,
        **overrides,
    }
    return Settings(**values)  # type: ignore[arg-type]  # Keyword arguments are validated by pydantic.


@pytest.fixture(autouse=True)
def ignore_developer_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep services/identity/.env out of the test suite.

    The file is how a developer configures a local run, and it is exactly what
    must not decide whether a test passes: without this, a machine that has one
    would see the "missing variable terminates the process" tests succeed
    because the variable is not missing at all.
    """
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(scope="session", autouse=True)
def span_exporter() -> InMemorySpanExporter:
    """Capture spans in memory instead of shipping them at a collector.

    Installed once for the session and before any application is built:
    configure_tracing leaves an existing provider alone, so this is what the
    service under test writes into. Without it every test would retry an OTLP
    export against a closed port and drown the output in gRPC errors.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture
def captured_spans(span_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    span_exporter.clear()
    yield span_exporter
    span_exporter.clear()


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
def production_settings() -> Settings:
    """Settings as they are in prod, where the interactive docs are off.

    A fixture rather than an importable helper: conftest fixtures reach every
    test without an import, and the test directories are deliberately not
    packages — an __init__.py here would name every service's suite ``tests``.
    """
    return build_settings(environment=Environment.PROD)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # The context manager runs the lifespan, so the engine and the Redis client
    # are disposed the same way they are in production.
    with TestClient(app) as test_client:
        yield test_client
