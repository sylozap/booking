"""Liveness and readiness answer different questions (P1-T07).

The fixtures point the service at a closed port, so the dependencies really are
unreachable and the probes really do fail — no repository of ours is faked
(CODING_STANDARDS 14).
"""

from __future__ import annotations

import asyncio
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.application.readiness import DependencyStatus


def test_liveness_is_200_while_dependencies_are_down(client: TestClient) -> None:
    # The whole point of splitting the two probes: restarting this process
    # would not bring the database back, so liveness must not fail with it.
    response = client.get("/healthz")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "alive"


def test_readiness_is_503_when_dependencies_are_down(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()["status"] == "not_ready"


def test_readiness_names_the_failing_dependency(client: TestClient) -> None:
    # An operator should not have to guess which of the two is down.
    body = client.get("/readyz").json()

    reported = {item["name"]: item for item in body["dependencies"]}
    assert set(reported) == {"postgres", "redis"}
    assert not reported["postgres"]["ready"]
    assert reported["postgres"]["detail"]


class ReadyProbe:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> DependencyStatus:
        return DependencyStatus(name=self._name, is_ready=True)


@pytest.fixture
def ready_client(app: FastAPI) -> TestClient:
    # Stand-ins for the external boundary, not for code of ours: the port is
    # what is being satisfied here.
    app.state.readiness_probes = (ReadyProbe("postgres"), ReadyProbe("redis"))
    return TestClient(app)


def test_readiness_is_200_when_every_dependency_answers(ready_client: TestClient) -> None:
    response = ready_client.get("/readyz")

    assert response.status_code == HTTPStatus.OK
    assert response.json()["status"] == "ready"


class HangingProbe:
    @property
    def name(self) -> str:
        return "slow"

    async def check(self) -> DependencyStatus:
        await asyncio.sleep(60)
        raise AssertionError("unreachable")


def test_a_hanging_dependency_fails_instead_of_blocking(app: FastAPI) -> None:
    # A probe that never answers keeps traffic flowing to a pod that cannot
    # serve it, which is worse than a probe that fails.
    app.state.readiness_probes = (HangingProbe(),)
    app.state.readiness_timeout_seconds = 0.05

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert "did not answer" in response.json()["dependencies"][0]["detail"]


def test_health_endpoints_are_not_in_the_public_schema(client: TestClient) -> None:
    # They are an operational contract with the platform, not part of the API
    # clients consume.
    paths = client.get("/openapi.json").json()["paths"]

    assert "/healthz" not in paths
    assert "/readyz" not in paths
