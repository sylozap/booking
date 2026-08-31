"""The RBAC catalogue is coherent on its own terms (P1-T10, D20).

Pure domain: no database, no session, no container. If one of these needed
infrastructure to check, the rule would be living in the wrong layer
(CODING_STANDARDS 6).
"""

from __future__ import annotations

import pytest

from identity.domain.access import (
    SYSTEM_ROLE_PERMISSIONS,
    TENANTLESS_ROLES,
    PermissionCode,
    RoleCode,
    requires_tenant,
)


def test_every_role_in_the_catalogue_is_a_known_role() -> None:
    assert set(SYSTEM_ROLE_PERMISSIONS) == set(RoleCode)


def test_every_granted_permission_is_a_known_permission() -> None:
    """A typo in a role's permission set is a capability nobody ever gets."""
    granted = {
        permission for permissions in SYSTEM_ROLE_PERMISSIONS.values() for permission in permissions
    }

    assert granted <= set(PermissionCode)


def test_every_permission_is_granted_to_someone() -> None:
    """A permission no role carries is a check that can never pass."""
    granted = {
        permission for permissions in SYSTEM_ROLE_PERMISSIONS.values() for permission in permissions
    }

    assert set(PermissionCode) - granted == set()


def test_super_admin_holds_every_permission() -> None:
    """By construction, so a new capability is never withheld from it."""
    assert SYSTEM_ROLE_PERMISSIONS[RoleCode.SUPER_ADMIN] == frozenset(PermissionCode)


def test_super_admin_is_the_only_platform_wide_role() -> None:
    """Everything else is a role inside one organization (ADR-0006)."""
    assert set(TENANTLESS_ROLES) == {RoleCode.SUPER_ADMIN}


@pytest.mark.parametrize(
    "role",
    [RoleCode.ORG_ADMIN, RoleCode.SPECIALIST, RoleCode.CLIENT],
)
def test_organization_roles_require_a_tenant(role: RoleCode) -> None:
    assert requires_tenant(role) is True


def test_super_admin_requires_no_tenant() -> None:
    assert requires_tenant(RoleCode.SUPER_ADMIN) is False


def test_a_client_cannot_administer_an_organization() -> None:
    """The separation the whole model exists for, stated once as an assertion."""
    client = SYSTEM_ROLE_PERMISSIONS[RoleCode.CLIENT]

    assert PermissionCode.MANAGE_ORGANIZATION not in client
    assert PermissionCode.MANAGE_USERS not in client


def test_a_specialist_manages_a_schedule_but_not_the_catalogue() -> None:
    specialist = SYSTEM_ROLE_PERMISSIONS[RoleCode.SPECIALIST]

    assert PermissionCode.MANAGE_SCHEDULE in specialist
    assert PermissionCode.CREATE_SERVICE not in specialist
    assert PermissionCode.UPDATE_SERVICE not in specialist


def test_permission_codes_are_stable_strings() -> None:
    """The value is stored in the database and named in the API contract.

    Renaming one silently un-grants it for everyone who had it, because the
    seed reconciles by code. This test is here to make that rename loud.
    """
    assert PermissionCode.CREATE_BOOKING.value == "create_booking"
    assert RoleCode.ORG_ADMIN.value == "ORG_ADMIN"
