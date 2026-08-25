"""Every failure leaves as problem+json (P1-T03, D43).

Four sources of error, one shape: a violated domain rule, a malformed request,
an HTTP-level refusal, and a bug.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from identity.api.errors import PROBLEM_CONTENT_TYPE
from identity.domain.exceptions import EmailAlreadyRegistered


class Registration(BaseModel):  # type: ignore[explicit-any]  # pydantic's base declares **data: Any; none is written here.
    email: str
    age: int


@pytest.fixture
def app_with_failing_routes(app: FastAPI) -> FastAPI:
    @app.post("/test/validated")
    async def validated(payload: Registration) -> dict[str, str]:
        return {"email": payload.email}

    @app.get("/test/domain-error")
    async def domain_error() -> None:
        raise EmailAlreadyRegistered("someone@example.com is already registered")

    @app.get("/test/bug")
    async def bug() -> None:
        # Stands in for any unhandled failure; the message is what must not
        # reach the client.
        raise RuntimeError("connection string postgresql://user:hunter2@db/identity")

    return app


@pytest.fixture
def failing_client(app_with_failing_routes: FastAPI) -> TestClient:
    # raise_server_exceptions=False so the 500 handler runs, as it does in
    # production, instead of the exception propagating into the test.
    return TestClient(app_with_failing_routes, raise_server_exceptions=False)


def test_domain_error_returns_its_own_code_and_status(failing_client: TestClient) -> None:
    response = failing_client.get("/test/domain-error")

    assert response.status_code == HTTPStatus.CONFLICT
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    body = response.json()
    assert body["code"] == "email_already_registered"
    assert body["status"] == HTTPStatus.CONFLICT
    assert body["title"] == "Email already registered"


def test_validation_error_reports_every_field(failing_client: TestClient) -> None:
    response = failing_client.post("/test/validated", json={"age": "not-a-number"})

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    body = response.json()
    assert body["code"] == "validation_failed"
    locations = {item["location"] for item in body["errors"]}
    assert any("email" in location for location in locations)
    assert any("age" in location for location in locations)


def test_unknown_path_is_a_problem_document_too(failing_client: TestClient) -> None:
    response = failing_client.get("/no-such-path")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert response.json()["code"] == "not_found"


def test_unhandled_exception_leaks_nothing(failing_client: TestClient) -> None:
    response = failing_client.get("/test/bug")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    body = response.json()
    assert body["code"] == "internal_error"
    # The exception text carried a password. It stays in the log.
    assert "hunter2" not in response.text
    assert "postgresql" not in response.text


@pytest.mark.parametrize(
    "path",
    ["/test/domain-error", "/no-such-path", "/test/bug"],
)
def test_every_problem_document_has_the_required_members(
    failing_client: TestClient, path: str
) -> None:
    body = failing_client.get(path).json()

    assert set(body) >= {"type", "title", "status", "code"}


def test_problem_document_carries_the_trace_id(failing_client: TestClient) -> None:
    # This is what turns a user's screenshot into a Tempo query.
    body = failing_client.get("/test/domain-error").json()

    assert len(str(body["trace_id"])) == 32
