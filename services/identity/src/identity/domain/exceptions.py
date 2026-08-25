"""Domain error catalogue.

Every failure the domain can express carries a stable machine-readable ``code``
and the HTTP status it maps to. The mapping lives here, next to the rule that
raises it, rather than in the router: a rule and the meaning of its violation
belong together, and putting the status in the API layer means the same
violation gets a different status in the next endpoint someone writes.

``code`` is part of the public API contract (D43). Clients branch on it, so
renaming one is a breaking change and requires a new API version.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import ClassVar


class DomainError(Exception):
    """Base for every violation of a domain rule.

    Not for programming errors and not for infrastructure failures: a lost
    database connection is not a domain error, and dressing it as one hides an
    outage behind a 4xx.
    """

    code: ClassVar[str] = "domain_error"
    http_status: ClassVar[int] = HTTPStatus.CONFLICT
    title: ClassVar[str] = "Domain rule violated"


class EmailAlreadyRegistered(DomainError):
    code = "email_already_registered"
    http_status = HTTPStatus.CONFLICT
    title = "Email already registered"


class InvalidCredentials(DomainError):
    """Wrong email or wrong password — deliberately indistinguishable.

    Telling the two apart turns the login endpoint into an oracle for which
    email addresses have accounts.
    """

    code = "invalid_credentials"
    http_status = HTTPStatus.UNAUTHORIZED
    title = "Invalid credentials"


class UserNotFound(DomainError):
    code = "user_not_found"
    http_status = HTTPStatus.NOT_FOUND
    title = "User not found"


class UserInactive(DomainError):
    code = "user_inactive"
    http_status = HTTPStatus.FORBIDDEN
    title = "User is not active"


class PermissionDenied(DomainError):
    code = "permission_denied"
    http_status = HTTPStatus.FORBIDDEN
    title = "Permission denied"


class RefreshTokenReused(DomainError):
    """A refresh token was presented twice.

    Treated as theft rather than as a mistake: the legitimate client rotates
    its token on every use, so a second use means someone else holds a copy.
    """

    code = "refresh_token_reused"
    http_status = HTTPStatus.UNAUTHORIZED
    title = "Refresh token reused"


class TokenExpired(DomainError):
    code = "token_expired"
    http_status = HTTPStatus.UNAUTHORIZED
    title = "Token expired"


class TenantMismatch(DomainError):
    """A resource was addressed from outside the tenant that owns it (D40).

    Answered as 404 rather than 403: confirming that an object exists in
    another tenant is itself a leak.
    """

    code = "tenant_mismatch"
    http_status = HTTPStatus.NOT_FOUND
    title = "Resource not found"
