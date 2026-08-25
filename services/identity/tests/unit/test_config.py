"""Configuration fails at startup or not at all (P1-T02, D54)."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from identity.infrastructure.config import (
    ENV_PREFIX,
    Environment,
    LogLevel,
    Settings,
    get_settings,
)

COMPLETE_ENVIRONMENT = {
    "IDENTITY_ENVIRONMENT": "local",
    "IDENTITY_DATABASE_DSN": "postgresql+asyncpg://identity:secret@db:5432/identity",
    "IDENTITY_REDIS_DSN": "redis://cache:6379/0",
    "IDENTITY_OTLP_ENDPOINT": "http://collector:4317",
}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Any IDENTITY_* variable set in the developer's shell would otherwise leak
    # into these assertions and make the suite pass or fail by accident.
    for name in [key for key in os.environ if key.upper().startswith(ENV_PREFIX)]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    yield

    get_settings.cache_clear()


def _set(monkeypatch: pytest.MonkeyPatch, values: dict[str, str]) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_complete_environment_produces_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, COMPLETE_ENVIRONMENT)

    settings = get_settings()

    assert settings.environment is Environment.LOCAL
    assert settings.service_name == "identity"
    assert settings.log_level is LogLevel.INFO


@pytest.mark.parametrize(
    "missing_variable",
    [
        "IDENTITY_ENVIRONMENT",
        "IDENTITY_DATABASE_DSN",
        "IDENTITY_REDIS_DSN",
        "IDENTITY_OTLP_ENDPOINT",
    ],
)
def test_missing_required_variable_terminates_the_process(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_variable: str,
) -> None:
    incomplete = {k: v for k, v in COMPLETE_ENVIRONMENT.items() if k != missing_variable}
    _set(monkeypatch, incomplete)
    monkeypatch.delenv(missing_variable, raising=False)

    with pytest.raises(SystemExit) as exit_info:
        get_settings()

    # EX_CONFIG, not a bare 1: an orchestrator can tell a misconfiguration from
    # a crash without parsing the log.
    assert exit_info.value.code == 78
    report = capsys.readouterr().err
    assert missing_variable in report


def test_invalid_type_terminates_the_process(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set(monkeypatch, {**COMPLETE_ENVIRONMENT, "IDENTITY_HTTP_PORT": "not-a-number"})

    with pytest.raises(SystemExit):
        get_settings()

    assert "IDENTITY_HTTP_PORT" in capsys.readouterr().err


def test_out_of_range_value_terminates_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, {**COMPLETE_ENVIRONMENT, "IDENTITY_HTTP_PORT": "70000"})

    with pytest.raises(SystemExit):
        get_settings()


def test_misspelled_variable_is_rejected_rather_than_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pydantic-settings never reads a variable it does not know, so without an
    # explicit check the typo is invisible and the report names the correctly
    # spelled variable as missing.
    _set(monkeypatch, {**COMPLETE_ENVIRONMENT, "IDENTITY_LOG_LEVELL": "DEBUG"})

    with pytest.raises(SystemExit):
        get_settings()


def test_settings_are_immutable() -> None:
    # Configuration that can be changed at runtime is configuration that will
    # be changed at runtime, and then no log line explains the behaviour.
    settings = Settings(
        environment=Environment.LOCAL,
        database_dsn="postgresql+asyncpg://identity:secret@db:5432/identity",  # type: ignore[arg-type]
        redis_dsn="redis://cache:6379/0",  # type: ignore[arg-type]
        otlp_endpoint="http://collector:4317",
    )

    with pytest.raises(ValidationError):
        settings.service_name = "other"


def test_settings_are_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, COMPLETE_ENVIRONMENT)

    first = get_settings()
    second = get_settings()

    assert first is second
