"""The application starts and mounts its versioned surface (P1-T01)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.api.router import API_V1_PREFIX, api_v1_router
from identity.infrastructure.config import Environment, Settings
from identity.main import create_app


def test_application_builds(app: FastAPI) -> None:
    assert app.title == "identity"


def test_versioned_router_is_mounted(settings: Settings) -> None:
    """A route added to the v1 router is reachable under /api/v1 (D45).

    Endpoints arrive from P1-T09. What has to hold now is that the mount point
    works, and the only honest way to show that is to mount something through
    it — the prefix is applied by include_router, so an empty router proves
    nothing.
    """

    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    api_v1_router.add_api_route("/ping", ping, methods=["GET"])
    try:
        with TestClient(create_app(settings)) as client:
            response = client.get(f"{API_V1_PREFIX}/ping")

            assert response.status_code == 200
            assert response.json() == {"status": "ok"}
    finally:
        api_v1_router.routes.pop()


def test_lifespan_runs_and_shuts_down_cleanly(app: FastAPI) -> None:
    # Entering the context manager runs startup, leaving it runs shutdown; a
    # failure to dispose the engine surfaces here rather than as a leaked
    # connection in production.
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200


def test_docs_are_served_outside_production(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_docs_are_absent_in_production(production_settings: Settings) -> None:
    app = create_app(production_settings)

    with TestClient(app) as client:
        # In production the interactive docs are one more unauthenticated
        # surface describing every endpoint.
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        assert app.state.settings.environment is Environment.PROD
