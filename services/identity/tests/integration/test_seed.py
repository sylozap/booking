"""The seed converges on the catalogue and stays there (P1-T10).

It runs on every deploy, so "does not duplicate what it created last time" is
the weakest useful property. The stronger one — the one these tests assert — is
convergence: whatever state the tables are in, one run makes them match
``identity.domain.access``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chassis import uuid7
from identity.domain.access import SYSTEM_ROLE_PERMISSIONS, PermissionCode, RoleCode
from identity.infrastructure.db.models import Permission, Role, RolePermission
from identity.infrastructure.db.seed import SeedReport, seed_system_roles
from identity.infrastructure.db.session import SessionFactory, transaction


async def _edges(session: AsyncSession) -> set[tuple[str, str]]:
    rows = await session.execute(
        select(Role.code, Permission.code)
        .select_from(RolePermission)
        .join(Role, Role.id == RolePermission.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
    )
    return set(rows.tuples().all())


def _catalogue() -> set[tuple[str, str]]:
    return {
        (role.value, permission.value)
        for role, permissions in SYSTEM_ROLE_PERMISSIONS.items()
        for permission in permissions
    }


@pytest.mark.asyncio
async def test_first_run_creates_the_catalogue(db_session: AsyncSession) -> None:
    report = await seed_system_roles(db_session)

    assert set(report.permissions_created) == {code.value for code in PermissionCode}
    assert set(report.roles_created) == {code.value for code in SYSTEM_ROLE_PERMISSIONS}
    assert await _edges(db_session) == _catalogue()


@pytest.mark.asyncio
async def test_second_run_changes_nothing(db_session: AsyncSession) -> None:
    await seed_system_roles(db_session)

    report = await seed_system_roles(db_session)

    assert report.has_changes is False
    assert report == SeedReport()


@pytest.mark.asyncio
async def test_second_run_does_not_duplicate_rows(db_session: AsyncSession) -> None:
    await seed_system_roles(db_session)

    await seed_system_roles(db_session)

    assert await db_session.scalar(select(func.count()).select_from(Role)) == len(
        SYSTEM_ROLE_PERMISSIONS
    )
    assert await db_session.scalar(select(func.count()).select_from(Permission)) == len(
        PermissionCode
    )


@pytest.mark.asyncio
async def test_system_roles_are_marked_as_such(db_session: AsyncSession) -> None:
    """is_system is what stops an administrator editing CLIENT out from under the code."""
    await seed_system_roles(db_session)

    roles = await db_session.scalars(select(Role))

    assert all(role.is_system for role in roles.all())


@pytest.mark.asyncio
async def test_a_missing_edge_is_restored(db_session: AsyncSession) -> None:
    """The case that matters on deploy: a permission was added to a role."""
    await seed_system_roles(db_session)
    client = await db_session.scalar(select(Role).where(Role.code == RoleCode.CLIENT.value))
    booking = await db_session.scalar(
        select(Permission).where(Permission.code == PermissionCode.CREATE_BOOKING.value)
    )
    assert client is not None
    assert booking is not None
    await db_session.execute(
        delete(RolePermission).where(
            RolePermission.role_id == client.id, RolePermission.permission_id == booking.id
        )
    )

    report = await seed_system_roles(db_session)

    assert report.grants_added == ((RoleCode.CLIENT.value, PermissionCode.CREATE_BOOKING.value),)
    assert await _edges(db_session) == _catalogue()


@pytest.mark.asyncio
async def test_an_edge_outside_the_catalogue_is_removed(db_session: AsyncSession) -> None:
    """A capability the catalogue no longer grants is revoked, not left behind."""
    await seed_system_roles(db_session)
    client = await db_session.scalar(select(Role).where(Role.code == RoleCode.CLIENT.value))
    manage_users = await db_session.scalar(
        select(Permission).where(Permission.code == PermissionCode.MANAGE_USERS.value)
    )
    assert client is not None
    assert manage_users is not None
    db_session.add(RolePermission(role_id=client.id, permission_id=manage_users.id))
    await db_session.flush()

    report = await seed_system_roles(db_session)

    assert report.grants_removed == ((RoleCode.CLIENT.value, PermissionCode.MANAGE_USERS.value),)
    assert await _edges(db_session) == _catalogue()


@pytest.mark.asyncio
async def test_a_permission_outside_the_catalogue_is_reported_not_deleted(
    db_session: AsyncSession,
) -> None:
    """Dropping data is a migration, never a side effect of a deploy (D55)."""
    await seed_system_roles(db_session)
    db_session.add(Permission(id=uuid7(), code="retired_capability", description=""))
    await db_session.flush()

    report = await seed_system_roles(db_session)

    assert report.unknown_permissions == ("retired_capability",)
    survivor = await db_session.scalar(
        select(Permission).where(Permission.code == "retired_capability")
    )
    assert survivor is not None


@pytest.mark.asyncio
async def test_the_seed_commits_through_a_transaction(session_factory: SessionFactory) -> None:
    """The unit of work the CLI uses, exercised the way the CLI uses it."""
    async with transaction(session_factory) as session:
        await seed_system_roles(session)

    async with session_factory() as session:
        stored = await session.scalars(select(Role.code))

    assert set(stored.all()) == {code.value for code in SYSTEM_ROLE_PERMISSIONS}
