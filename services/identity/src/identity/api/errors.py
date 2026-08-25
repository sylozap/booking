"""Every error leaves this service as RFC 9457 problem+json (D43).

One shape for all four sources of failure — a violated domain rule, a malformed
request, an HTTP-level refusal, and a bug — so a client parses one structure
and branches on one field. That field is ``code``: the HTTP status says what
class of thing went wrong, ``code`` says which thing exactly, and only ``code``
is stable enough to branch on.

``trace_id`` is included in every response on purpose. It turns a user's
screenshot into a query: paste it into Tempo and the whole request is there.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Final

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from opentelemetry import trace
from starlette.exceptions import HTTPException as StarletteHTTPException

from identity.domain.exceptions import DomainError

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE: Final = "application/problem+json"

# RFC 9457 allows "about:blank" when there is no documentation page for the
# type. Until there is one, a stable relative URI per code is more useful than
# about:blank repeated everywhere.
PROBLEM_TYPE_BASE: Final = "/problems"

VALIDATION_FAILED: Final = "validation_failed"
INTERNAL_ERROR: Final = "internal_error"


def current_trace_id() -> str | None:
    """Hex trace id of the active span, or None outside a trace.

    Read from the OpenTelemetry context rather than from a header: by the time
    a handler runs, the id in context is the one the exporter will report, and
    a forged inbound header cannot change it.
    """
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x")


def problem_response(
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "type": f"{PROBLEM_TYPE_BASE}/{code}",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail is not None:
        body["detail"] = detail
    trace_id = current_trace_id()
    if trace_id is not None:
        body["trace_id"] = trace_id
    if extra:
        body.update(extra)

    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CONTENT_TYPE)


async def handle_domain_error(request: Request, exc: Exception) -> Response:
    """A rule said no. Expected, and therefore not an ERROR in the logs."""
    del request
    assert isinstance(exc, DomainError)  # noqa: S101  # Registered for this type only.

    logger.info(
        "domain rule rejected the request",
        extra={"error_code": exc.code, "error_status": exc.http_status},
    )
    return problem_response(
        status=exc.http_status,
        code=exc.code,
        title=exc.title,
        detail=str(exc) or None,
    )


async def handle_validation_error(request: Request, exc: Exception) -> Response:
    """The request did not match the schema.

    pydantic's per-field report is kept under ``errors``: it is what makes the
    response actionable, and it describes the caller's own input, so it leaks
    nothing the caller did not send.
    """
    del request
    assert isinstance(exc, RequestValidationError)  # noqa: S101

    errors: list[dict[str, object]] = [
        {
            "location": ".".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in exc.errors()
    ]
    return problem_response(
        status=HTTPStatus.UNPROCESSABLE_ENTITY,
        code=VALIDATION_FAILED,
        title="Request validation failed",
        detail=f"{len(errors)} field(s) did not pass validation",
        extra={"errors": errors},
    )


async def handle_http_exception(request: Request, exc: Exception) -> Response:
    """404s, 405s and anything raised as HTTPException, in the same shape."""
    del request
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101

    status = HTTPStatus(exc.status_code)
    return problem_response(
        status=exc.status_code,
        # Derived from the status so that every HTTP-level refusal has a code
        # too: a client should never have to special-case "this one has no
        # code because nobody wrote a domain exception for it".
        code=status.name.lower(),
        title=status.phrase,
        detail=str(exc.detail) if exc.detail else None,
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    """A bug. The client learns nothing beyond the trace id.

    Exception text can carry a connection string, a row, or a fragment of
    someone else's data; the stack trace goes to the log, where it belongs, and
    the trace id ties the two together.
    """
    logger.error(
        "unhandled exception",
        # Explicitly the exception passed in, not whatever sys.exc_info happens
        # to hold: inside a task group those are not always the same.
        exc_info=exc,
        extra={"http_method": request.method, "http_route": _route_of(request)},
    )
    return problem_response(
        status=HTTPStatus.INTERNAL_SERVER_ERROR,
        code=INTERNAL_ERROR,
        title="Internal server error",
        detail="The request could not be completed. Quote trace_id when reporting it.",
    )


def _route_of(request: Request) -> str:
    route = request.scope.get("route")
    path_format = getattr(route, "path_format", None)
    return path_format if isinstance(path_format, str) else request.url.path


Handler = Callable[[Request, Exception], Awaitable[Response]]


def register_error_handlers(app: FastAPI) -> None:
    """Wire the four handlers. Order does not matter; specificity does."""
    handlers: dict[type[Exception] | int, Handler] = {
        DomainError: handle_domain_error,
        RequestValidationError: handle_validation_error,
        StarletteHTTPException: handle_http_exception,
        Exception: handle_unexpected_error,
    }
    for exception_type, handler in handlers.items():
        app.add_exception_handler(exception_type, handler)
