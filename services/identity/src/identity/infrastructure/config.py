"""Process configuration, read from the environment once at startup (D54).

Two rules shape this module. Configuration comes only from environment
variables, because a config file baked into an image stops the image from being
portable between environments. And an incomplete or malformed configuration
kills the process at startup rather than surfacing as a failed request an hour
later, when the connection to a mistyped host is first attempted.

Defaults exist only where a wrong value is harmless. Addresses of databases,
brokers and external services have none: a default there silently connects a
misconfigured production process to something, and the something is usually
localhost.
"""

from __future__ import annotations

import os
import sys
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Final, NoReturn

from pydantic import Field, PostgresDsn, RedisDsn, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX: Final = "IDENTITY_"


class Environment(StrEnum):
    """Deployment environment. Drives log format and error verbosity."""

    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):  # type: ignore[explicit-any]  # pydantic's base declares **data: Any; none is written here.
    """Everything the process needs to know, validated before it serves traffic."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        extra="forbid",
        frozen=True,
        case_sensitive=False,
    )

    environment: Environment
    service_name: str = "identity"
    service_version: str = "0.1.0"

    # No defaults: see the module docstring.
    database_dsn: PostgresDsn
    redis_dsn: RedisDsn
    otlp_endpoint: str = Field(min_length=1)

    log_level: LogLevel = LogLevel.INFO

    http_host: str = "0.0.0.0"  # noqa: S104  # In a container, binding to the interface is the point.
    http_port: Annotated[int, Field(ge=1, le=65535)] = 8000

    database_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    database_pool_max_overflow: Annotated[int, Field(ge=0, le=100)] = 5
    # A readiness probe that hangs is worse than one that fails: Kubernetes
    # keeps sending traffic to a pod whose probe never answers.
    readiness_timeout_seconds: Annotated[float, Field(gt=0, le=10)] = 2.0

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PROD


def _unknown_variables() -> list[str]:
    """Prefixed environment variables that match no field.

    pydantic-settings only looks for the variables it knows about, so a typo
    is invisible to it: IDENTITY_DATABSE_DSN is never read, and the report
    names IDENTITY_DATABASE_DSN as missing — sending the reader to look at the
    one line that is spelled correctly.

    This is the one place allowed to touch os.environ (CODING_STANDARDS 12):
    it reads no configuration value, only the set of names.
    """
    known = {f"{ENV_PREFIX}{name}".upper() for name in Settings.model_fields}
    return sorted(
        name
        for name in os.environ
        if name.upper().startswith(ENV_PREFIX) and name.upper() not in known
    )


def _describe(error: ValidationError) -> str:
    """Turn pydantic's report into something readable at 3 a.m.

    The default rendering names fields as pydantic sees them; an operator needs
    the environment variable they actually have to set.
    """
    lines = [f"invalid configuration: {error.error_count()} problem(s)", ""]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        variable = f"{ENV_PREFIX}{location.upper()}"
        lines.append(f"  {variable}: {item['msg']}")
    lines.extend(("", "See services/identity/.env.example for the full list."))
    return "\n".join(lines)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load and validate the configuration, or terminate the process.

    Cached: the environment is read once, and every caller sees the same
    object. Tests clear the cache rather than mutating it.
    """
    unknown = _unknown_variables()
    if unknown:
        _fail(
            "unknown configuration variable(s):\n\n"
            + "\n".join(f"  {name}" for name in unknown)
            + "\n\nA misspelled variable is never read. Check the spelling against"
            "\nservices/identity/.env.example."
        )

    try:
        return Settings()  # type: ignore[call-arg]  # pydantic-settings fills fields from the environment.
    except ValidationError as error:
        _fail(_describe(error), cause=error)


def _fail(report: str, *, cause: Exception | None = None) -> NoReturn:
    """Terminate with EX_CONFIG and a report an operator can act on.

    Deliberately not a logger call: logging is configured from these very
    settings, so at this point it does not exist yet.
    """
    print(report, file=sys.stderr)  # noqa: T201
    raise SystemExit(78) from cause
