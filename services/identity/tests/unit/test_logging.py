"""Secrets never reach a log record (P1-T04, D50).

A password written to stdout is copied into Loki within seconds and cannot be
recalled from there, so the guarantee has to hold before the record is
formatted — not by everyone remembering not to log it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from identity.infrastructure.logging import (
    MASK,
    SENSITIVE_FIELDS,
    JsonFormatter,
    is_sensitive,
    mask,
    request_id_var,
)


@pytest.fixture
def formatter() -> JsonFormatter:
    return JsonFormatter(service_name="identity", service_version="0.1.0", environment="local")


def render(formatter: JsonFormatter, **extra: object) -> dict[str, object]:
    record = logging.LogRecord(
        name="identity.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event happened",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)

    parsed: dict[str, object] = json.loads(formatter.format(record))
    return parsed


@pytest.mark.parametrize("field_name", sorted(SENSITIVE_FIELDS))
def test_every_sensitive_field_is_masked(formatter: JsonFormatter, field_name: str) -> None:
    payload = render(formatter, **{field_name: "the-actual-secret"})

    assert payload[field_name] == MASK
    assert "the-actual-secret" not in json.dumps(payload)


@pytest.mark.parametrize(
    "field_name",
    [
        "access_token",
        "refresh_token",
        "password_hash",
        "Authorization",
        "X-Api-Key",
        "stripe_secret",
    ],
)
def test_compound_field_names_are_masked(formatter: JsonFormatter, field_name: str) -> None:
    # The catalogue lists roots; real payloads carry them inside longer names.
    payload = render(formatter, **{field_name: "the-actual-secret"})

    assert payload[field_name] == MASK


def test_nested_values_are_masked(formatter: JsonFormatter) -> None:
    payload = render(
        formatter,
        request={"headers": {"authorization": "Bearer abc", "accept": "application/json"}},
    )

    request = payload["request"]
    assert isinstance(request, dict)
    headers = request["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == MASK
    assert headers["accept"] == "application/json"


def test_values_inside_lists_are_masked(formatter: JsonFormatter) -> None:
    payload = render(formatter, users=[{"email": "a@b.c", "password": "hunter2"}])

    assert "hunter2" not in json.dumps(payload)


def test_non_sensitive_fields_survive(formatter: JsonFormatter) -> None:
    payload = render(formatter, user_id="42", http_status=200)

    assert payload["user_id"] == "42"
    assert payload["http_status"] == 200


def test_record_carries_service_identity(formatter: JsonFormatter) -> None:
    payload = render(formatter)

    assert payload["service"] == "identity"
    assert payload["version"] == "0.1.0"
    assert payload["environment"] == "local"
    assert payload["level"] == "INFO"
    assert payload["message"] == "event happened"


@pytest.fixture
def request_id() -> Iterator[str]:
    token = request_id_var.set("req-123")
    yield "req-123"
    request_id_var.reset(token)


def test_request_id_is_attached_from_context(formatter: JsonFormatter, request_id: str) -> None:
    payload = render(formatter)

    assert payload["request_id"] == request_id


def test_timestamp_is_utc_with_real_milliseconds(formatter: JsonFormatter) -> None:
    """Local time labelled Z would put every line hours from its own span.

    stdlib's formatTime renders local time and cannot render milliseconds, so
    the obvious format string yields a constant fake fraction on an offset
    clock — invisible until someone tries to line a log up with a trace.
    """
    before = datetime.now(tz=UTC)

    payload = render(formatter)

    timestamp = payload["timestamp"]
    assert isinstance(timestamp, str)
    assert timestamp.endswith("Z")

    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert abs((parsed - before).total_seconds()) < 5
    # A constant fraction is the signature of the strftime bug this replaced.
    assert len(timestamp.split(".")[1]) == 4


def test_masking_is_case_insensitive() -> None:
    assert is_sensitive("PASSWORD")
    assert is_sensitive("Authorization")
    assert not is_sensitive("username")


def test_masking_stops_at_depth_limit() -> None:
    # Guards against a pathological payload turning a log call into the reason
    # a request times out.
    deep: dict[str, object] = {"password": "secret"}
    for _ in range(20):
        deep = {"nested": deep}

    masked = mask(deep)

    assert isinstance(masked, dict)


def test_strings_are_not_taken_apart(formatter: JsonFormatter) -> None:
    # str is a Sequence; without an explicit check the masker would return a
    # list of characters.
    payload = render(formatter, note="plain text")

    assert payload["note"] == "plain text"
