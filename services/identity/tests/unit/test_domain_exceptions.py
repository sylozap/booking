"""The error catalogue is a contract (P1-T03, D43).

Clients branch on ``code``, so a duplicate or a drifting status is a breaking
change to the API, not an internal detail.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from identity.domain import exceptions as catalogue
from identity.domain.exceptions import DomainError, InvalidCredentials, TenantMismatch


def all_domain_errors() -> list[type[DomainError]]:
    return [
        value
        for value in vars(catalogue).values()
        if isinstance(value, type) and issubclass(value, DomainError) and value is not DomainError
    ]


def test_catalogue_is_not_empty() -> None:
    assert all_domain_errors()


@pytest.mark.parametrize("error_type", all_domain_errors(), ids=lambda t: t.__name__)
def test_every_error_declares_a_code_and_a_status(error_type: type[DomainError]) -> None:
    assert error_type.code
    assert error_type.code != DomainError.code, "must not inherit the placeholder code"
    assert error_type.title


@pytest.mark.parametrize("error_type", all_domain_errors(), ids=lambda t: t.__name__)
def test_status_is_a_client_error(error_type: type[DomainError]) -> None:
    # A domain rule rejecting a request is the client's problem by definition.
    # A 5xx here would page someone for a user typing the wrong password.
    assert 400 <= error_type.http_status < 500


@pytest.mark.parametrize("error_type", all_domain_errors(), ids=lambda t: t.__name__)
def test_code_is_snake_case(error_type: type[DomainError]) -> None:
    assert error_type.code == error_type.code.lower()
    assert " " not in error_type.code


def test_codes_are_unique() -> None:
    codes = [error_type.code for error_type in all_domain_errors()]

    assert len(codes) == len(set(codes))


def test_invalid_credentials_does_not_distinguish_cause() -> None:
    # Splitting this into "no such user" and "wrong password" would turn login
    # into an oracle for which addresses have accounts.
    assert InvalidCredentials.http_status == HTTPStatus.UNAUTHORIZED
    assert "email" not in InvalidCredentials.title.lower()


def test_cross_tenant_access_looks_like_absence() -> None:
    # 403 would confirm the resource exists in another tenant (D40).
    assert TenantMismatch.http_status == HTTPStatus.NOT_FOUND
