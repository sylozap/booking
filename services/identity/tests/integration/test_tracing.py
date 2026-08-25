"""A request produces a span, and its trace id reaches the log (P1-T05, D8).

This is the join that makes the stack usable: a span found in Tempo and the log
lines it produced in Loki are connected by one identifier, and neither side has
to pass it around by hand.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import override

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

from identity.infrastructure.config import Settings
from identity.infrastructure.logging import JsonFormatter
from identity.main import create_app

ROUTE = "/traced"

logger = logging.getLogger("identity.test.traced")


class CapturingHandler(logging.Handler):
    """Formats records with the real formatter and keeps the output."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def documents(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.lines]


@pytest.fixture
def traced_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get(ROUTE)
    async def traced() -> dict[str, str]:
        logger.info("handling a traced request", extra={"user_id": "42"})
        return {"status": "ok"}

    return app


@pytest.fixture
def captured_logs() -> Iterator[CapturingHandler]:
    handler = CapturingHandler()
    handler.setFormatter(
        JsonFormatter(service_name="identity", service_version="0.1.0", environment="local")
    )
    root = logging.getLogger()
    root.addHandler(handler)

    yield handler

    root.removeHandler(handler)


def test_request_produces_a_server_span(
    traced_app: FastAPI, captured_spans: InMemorySpanExporter
) -> None:
    with TestClient(traced_app) as client:
        client.get(ROUTE)

    span_names = [span.name for span in captured_spans.get_finished_spans()]

    assert any(ROUTE in name for name in span_names)


def test_server_span_carries_the_route_template_and_status(
    traced_app: FastAPI, captured_spans: InMemorySpanExporter
) -> None:
    """The span is labelled by route, not by path.

    Attribute names follow the semantic conventions the installed
    instrumentation emits; a semconv migration renames them, and this test is
    where that shows up rather than in a silently empty Grafana panel.
    """
    with TestClient(traced_app) as client:
        client.get(ROUTE)

    server_span = next(
        span
        for span in captured_spans.get_finished_spans()
        if span.kind is SpanKind.SERVER and ROUTE in span.name
    )

    assert server_span.attributes is not None
    assert server_span.attributes["http.route"] == ROUTE
    assert server_span.attributes["http.status_code"] == 200
    assert server_span.attributes["http.method"] == "GET"


def test_log_written_during_a_request_carries_the_trace_id(
    traced_app: FastAPI,
    captured_spans: InMemorySpanExporter,
    captured_logs: CapturingHandler,
) -> None:
    with TestClient(traced_app) as client:
        client.get(ROUTE)

    handler_log = next(
        document
        for document in captured_logs.documents()
        if document.get("message") == "handling a traced request"
    )
    span = next(span for span in captured_spans.get_finished_spans() if ROUTE in span.name)

    assert handler_log["trace_id"] == format(span.context.trace_id, "032x")
    assert handler_log["span_id"]


def test_logs_outside_a_request_have_no_trace_id(captured_logs: CapturingHandler) -> None:
    logger.info("background work")

    document = captured_logs.documents()[-1]

    assert "trace_id" not in document


def test_operational_endpoints_are_not_traced(
    traced_app: FastAPI, captured_spans: InMemorySpanExporter
) -> None:
    # kubelet and Prometheus poll these every few seconds; tracing them would
    # bury the real traffic.
    with TestClient(traced_app) as client:
        client.get("/healthz")
        client.get("/metrics")

    assert captured_spans.get_finished_spans() == ()
