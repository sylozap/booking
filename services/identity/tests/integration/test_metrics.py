"""RED metrics are exposed with labels Prometheus can group by (P1-T06)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.api.router import API_V1_PREFIX, api_v1_router
from identity.infrastructure.config import Settings
from identity.main import create_app


@pytest.fixture
def app_with_parameterised_route(settings: Settings) -> Iterator[FastAPI]:
    async def show(user_id: str) -> dict[str, str]:
        return {"user_id": user_id}

    api_v1_router.add_api_route("/users/{user_id}", show, methods=["GET"])
    try:
        yield create_app(settings)
    finally:
        api_v1_router.routes.pop()


def test_metrics_endpoint_answers(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_request_is_counted_with_method_route_and_status(client: TestClient) -> None:
    client.get("/healthz")

    exposition = client.get("/metrics").text

    assert 'identity_http_requests_total{method="GET",route="/healthz",status="200"}' in exposition


def test_duration_is_observed_per_route(client: TestClient) -> None:
    client.get("/healthz")

    exposition = client.get("/metrics").text

    assert (
        'identity_http_request_duration_seconds_count{method="GET",route="/healthz"}' in exposition
    )
    assert "identity_http_request_duration_seconds_bucket" in exposition


def test_route_label_is_the_template_not_the_path(
    app_with_parameterised_route: FastAPI,
) -> None:
    # One series per user would take the Prometheus instance down within a day.
    with TestClient(app_with_parameterised_route) as client:
        client.get(f"{API_V1_PREFIX}/users/11111111-1111-1111-1111-111111111111")
        client.get(f"{API_V1_PREFIX}/users/22222222-2222-2222-2222-222222222222")

        exposition = client.get("/metrics").text

    assert f'route="{API_V1_PREFIX}/users/{{user_id}}"' in exposition
    assert "11111111-1111-1111-1111-111111111111" not in exposition


def test_operational_endpoints_are_labelled_by_their_own_route(client: TestClient) -> None:
    # Every route the service actually serves must name itself; anything landing
    # in "unmatched" is a route the middleware could not see.
    client.get("/metrics")

    exposition = client.get("/metrics").text

    assert 'route="/metrics"' in exposition


def test_unmatched_paths_share_one_series(client: TestClient) -> None:
    # A scanner probing random URLs must not be able to create a series each.
    client.get("/no-such-path-a")
    client.get("/no-such-path-b")

    exposition = client.get("/metrics").text

    assert 'route="unmatched"' in exposition
    assert "no-such-path-a" not in exposition


def test_error_status_is_labelled_separately(client: TestClient) -> None:
    client.get("/readyz")  # 503: dependencies are unreachable in these tests.

    exposition = client.get("/metrics").text

    assert 'route="/readyz",status="503"' in exposition


def test_database_pool_is_exposed(client: TestClient) -> None:
    # Requests queue on a pool that is too small long before the database
    # itself is loaded; without these gauges that looks like an application bug.
    exposition = client.get("/metrics").text

    assert "identity_db_pool_connections_in_use" in exposition
    assert "identity_db_pool_connections_available" in exposition
    assert "identity_db_pool_overflow" in exposition


def test_pool_overflow_is_never_negative(client: TestClient) -> None:
    # SQLAlchemy counts this one from -pool_size upwards; exposed verbatim it
    # would read -9 for an idle healthy pool.
    exposition = client.get("/metrics").text

    reported = next(
        line for line in exposition.splitlines() if line.startswith("identity_db_pool_overflow ")
    )

    assert float(reported.split()[1]) >= 0


def test_metrics_endpoint_is_not_in_the_public_schema(client: TestClient) -> None:
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]
