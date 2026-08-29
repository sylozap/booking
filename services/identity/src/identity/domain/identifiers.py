"""Distinct types for the identifiers this service handles.

Everything is a UUID, which means the type checker cannot tell a user from a
role from a tenant — and swapping two arguments of the same type is the single
most common defect in a system built this way. ``NewType`` costs nothing at
runtime and makes the swap a type error (CODING_STANDARDS 4).

``TenantId`` is an organization identifier owned by ``catalog``. It appears here
as an opaque value: ``identity`` stores it, filters by it and never resolves it,
because resolving it would mean a synchronous call into another service.
"""

from __future__ import annotations

from typing import NewType
from uuid import UUID

UserId = NewType("UserId", UUID)
OAuthAccountId = NewType("OAuthAccountId", UUID)
RoleId = NewType("RoleId", UUID)
PermissionId = NewType("PermissionId", UUID)
RoleAssignmentId = NewType("RoleAssignmentId", UUID)
RefreshTokenId = NewType("RefreshTokenId", UUID)
TokenFamilyId = NewType("TokenFamilyId", UUID)
AuditEntryId = NewType("AuditEntryId", UUID)
TenantId = NewType("TenantId", UUID)
