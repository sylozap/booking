"""UUID v7 layout and ordering.

The value of a v7 key is that it sorts by creation time; if that stops being
true, index locality is lost silently and nothing else in the system notices.
"""

from __future__ import annotations

import time
from uuid import UUID

import pytest

from chassis.identifiers import uuid7, uuid7_timestamp_ms


def test_uuid7_reports_version_7_and_the_rfc_variant() -> None:
    identifier = uuid7()

    assert identifier.version == 7
    assert identifier.variant == "specified in RFC 4122"


def test_uuid7_values_are_unique() -> None:
    identifiers = {uuid7() for _ in range(10_000)}

    assert len(identifiers) == 10_000


def test_uuid7_sorts_by_creation_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # The clock is substituted rather than waited on (CODING_STANDARDS 14).
    # Within one millisecond the order is undefined by design, so a real sleep
    # would be both slow and testing the random tail.
    clock_ns = 1_735_689_600_000_000_000
    monkeypatch.setattr("chassis.identifiers.time.time_ns", lambda: clock_ns)
    before = uuid7()

    monkeypatch.setattr("chassis.identifiers.time.time_ns", lambda: clock_ns + 1_000_000)
    after = uuid7()

    assert before < after


def test_uuid7_carries_the_wall_clock_millisecond() -> None:
    expected_ms = time.time_ns() // 1_000_000

    actual_ms = uuid7_timestamp_ms(uuid7())

    assert abs(actual_ms - expected_ms) < 1000


def test_uuid7_timestamp_ms_rejects_another_uuid_version() -> None:
    v4 = UUID("f81d4fae-7dec-41d0-a765-00a0c91e6bf6")

    with pytest.raises(ValueError, match="not a UUID version 7"):
        uuid7_timestamp_ms(v4)
