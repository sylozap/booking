"""The RBAC vocabulary: what can be done, and who can do it (ADR-0006, D20).

Two rules are encoded here and nowhere else.

Access is checked against a **permission**, never against a role. Roles are a
convenience for granting; the moment code asks "is this user an ORG_ADMIN", the
role becomes the contract and adding a new role means editing every check.

A grant is scoped to a **tenant**: the same person is a client in one
organization and its administrator in another, so a role without a tenant would
have to be the union of both and is therefore meaningless. ``SUPER_ADMIN`` is
the single exception — it is a platform role and belongs to no organization.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class PermissionCode(StrEnum):
    """One capability. The value is stored in the database and never renamed.

    Renaming a code silently un-grants it for everyone who had it, since the
    seed reconciles by code. A capability that changes meaning gets a new code
    and the old one is removed in a separate step (expand/contract, D55).
    """

    MANAGE_ORGANIZATION = "manage_organization"
    MANAGE_USERS = "manage_users"
    CREATE_SERVICE = "create_service"
    UPDATE_SERVICE = "update_service"
    MANAGE_SCHEDULE = "manage_schedule"
    CREATE_BOOKING = "create_booking"
    CANCEL_BOOKING = "cancel_booking"
    VIEW_ORGANIZATION_BOOKINGS = "view_organization_bookings"


class RoleCode(StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ORG_ADMIN = "ORG_ADMIN"
    SPECIALIST = "SPECIALIST"
    CLIENT = "CLIENT"


#: Roles that exist by definition of the platform rather than by an
#: administrator's decision. They are created by the seed, and nothing outside
#: the seed may edit or delete them.
SYSTEM_ROLE_PERMISSIONS: Final[dict[RoleCode, frozenset[PermissionCode]]] = {
    # Platform operator. Holds every permission by construction rather than by
    # enumeration, so a new capability is never accidentally withheld from the
    # only role that must be able to use it.
    RoleCode.SUPER_ADMIN: frozenset(PermissionCode),
    RoleCode.ORG_ADMIN: frozenset(
        {
            PermissionCode.MANAGE_ORGANIZATION,
            PermissionCode.MANAGE_USERS,
            PermissionCode.CREATE_SERVICE,
            PermissionCode.UPDATE_SERVICE,
            PermissionCode.MANAGE_SCHEDULE,
            PermissionCode.CANCEL_BOOKING,
            PermissionCode.VIEW_ORGANIZATION_BOOKINGS,
        }
    ),
    # A specialist runs their own calendar and can cancel what is booked into
    # it. Editing the organization's services is not theirs to do.
    RoleCode.SPECIALIST: frozenset(
        {
            PermissionCode.MANAGE_SCHEDULE,
            PermissionCode.CANCEL_BOOKING,
        }
    ),
    RoleCode.CLIENT: frozenset(
        {
            PermissionCode.CREATE_BOOKING,
            PermissionCode.CANCEL_BOOKING,
        }
    ),
}

#: The one role granted platform-wide. Every other grant names an organization.
TENANTLESS_ROLES: Final[frozenset[RoleCode]] = frozenset({RoleCode.SUPER_ADMIN})


def requires_tenant(role: RoleCode) -> bool:
    return role not in TENANTLESS_ROLES
