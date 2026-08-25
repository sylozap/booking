"""Structured JSON logging with correlation and masking (D8, D50).

Three properties matter here.

*Structured.* Loki indexes labels, not message text (ADR-0008), so a log line
is a JSON object with fields, never a sentence with values interpolated into
it. ``logger.info("user %s failed", email)`` produces a string nobody can query.

*Correlated.* Every line carries the ``trace_id`` of the request that produced
it, taken from the OpenTelemetry context, plus a ``request_id`` for the single
HTTP exchange. That is what makes the jump from a span in Tempo to the lines it
produced in Loki work, and it happens automatically — no handler passes ids
around by hand.

*Masked.* Secrets are removed before the record reaches a handler, not by
remembering not to log them. A password reaching disk cannot be unreached, and
the log pipeline copies it to a second system within seconds.

stdlib ``logging`` rather than a structured-logging library: this is eighty
lines, it is fully typed under a configuration that bans ``Any``, and every
third-party library already logs through stdlib, so their records get the same
treatment for free.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable, Mapping, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Final, override

from opentelemetry import trace

# Field names whose values never reach a log, at any level, in any service.
# Matched as a substring, so the compound names that keep appearing are covered
# by their root: access_token, refresh_token, password_hash. Case and separator
# are normalised first, because the same field arrives as X-Api-Key in a header
# and api_key in a payload (D50, CODING_STANDARDS 11).
SENSITIVE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "api_key",
        "card",
        "client_secret",
        "code_verifier",
        "cookie",
        "credentials",
        "csrf",
        "id_token",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "session",
        "signature",
        "token",
        "webhook_secret",
    }
)

MASK: Final = "***"
MAX_MASK_DEPTH: Final = 6

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord carries. Anything else on the record was put there
# by the caller through `extra=` and belongs in the payload.
_STANDARD_RECORD_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _normalise(field_name: str) -> str:
    return field_name.lower().replace("-", "_")


def is_sensitive(field_name: str) -> bool:
    normalised = _normalise(field_name)
    return any(marker in normalised for marker in SENSITIVE_FIELDS)


def mask(value: object, *, depth: int = 0) -> object:
    """Replace the value of every sensitive key, however deeply nested.

    Depth-limited rather than cycle-tracked: log payloads are small and flat in
    practice, and a bounded walk cannot become the reason a request hangs.
    """
    if depth >= MAX_MASK_DEPTH:
        return value

    if isinstance(value, Mapping):
        return {
            key: MASK if is_sensitive(str(key)) else mask(item, depth=depth + 1)
            for key, item in value.items()
        }

    # str is a Sequence; checking it first keeps a string from being taken apart
    # character by character.
    if isinstance(value, str | bytes):
        return value

    if isinstance(value, Sequence):
        return [mask(item, depth=depth + 1) for item in value]

    if isinstance(value, set | frozenset):
        return sorted(str(item) for item in value)

    return value


class JsonFormatter(logging.Formatter):
    """Renders a record as one JSON object on one line."""

    def __init__(self, *, service_name: str, service_version: str, environment: str) -> None:
        super().__init__()
        self._base: Final[dict[str, str]] = {
            "service": service_name,
            "version": service_version,
            "environment": environment,
        }

    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": _timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **self._base,
        }

        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id

        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            payload["trace_id"] = format(span_context.trace_id, "032x")
            payload["span_id"] = format(span_context.span_id, "016x")

        for key, value in _extra_fields(record):
            payload[key] = MASK if is_sensitive(key) else mask(value)

        # exc_info is a tuple when there is an exception, but a caller may pass
        # exc_info=True with none active, and stdlib leaves the bool in place.
        if isinstance(record.exc_info, tuple):
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)

        # default=str so that a UUID or a datetime in the payload cannot make
        # the logging call itself raise.
        return json.dumps(payload, default=str, ensure_ascii=False)


def _timestamp(created: float) -> str:
    """RFC 3339 in UTC, with real milliseconds.

    Not logging.Formatter.formatTime: it renders *local* time, and strftime has
    no millisecond directive, so the obvious "%H:%M:%S.%03dZ" produces a
    constant fake fraction on a timestamp that is silently offset by the host's
    zone. A log seven hours away from the span it belongs to breaks the one
    thing this stack exists for.
    """
    return (
        datetime.fromtimestamp(created, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _extra_fields(record: logging.LogRecord) -> Iterable[tuple[str, object]]:
    for key, value in record.__dict__.items():
        if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
            yield key, value


def configure_logging(
    *,
    service_name: str,
    service_version: str,
    environment: str,
    level: str,
) -> None:
    """Install the JSON formatter as the only handler on the root logger.

    Replaces existing handlers rather than adding to them: uvicorn installs its
    own, and leaving both means every line is emitted twice, once structured
    and once not.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            service_name=service_name,
            service_version=service_version,
            environment=environment,
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # uvicorn's access log duplicates what the metrics middleware records, in a
    # format that is not JSON and cannot be queried. The error logger stays.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("uvicorn", "uvicorn.error"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True
