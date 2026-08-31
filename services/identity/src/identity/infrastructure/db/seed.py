"""Reconcile the system roles and permissions into the database.

The catalogue lives in ``identity.domain.access``; this brings the database in
line with it. Run on every deploy, so it must be **convergent**, not merely
repeatable: running it twice changes nothing the second time, and running it
after a permission was added to a role grants that permission to everyone
holding the role — without a hand-written migration and without duplicating
what the first run created.

Reconciliation covers the role → permission edges, because those are the
catalogue. It does not delete permission rows that disappeared from the
catalogue: a permission may still be referenced by an organization-defined
role, and dropping data is a deliberate migration under expand/contract (D55),
not a side effect of a deploy. Orphans are reported instead.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from chassis import uuid7
from identity.domain.access import SYSTEM_ROLE_PERMISSIONS, PermissionCode, RoleCode
from identity.infrastructure.db.models import Permission, Role, RolePermission


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What the run changed. Empty on a second run — that is the assertion."""

    permissions_created: tuple[str, ...] = ()
    roles_created: tuple[str, ...] = ()
    grants_added: tuple[tuple[str, str], ...] = ()
    grants_removed: tuple[tuple[str, str], ...] = ()
    #: Permission rows no longer named by the catalogue. Reported, never
    #: deleted: removing one is a migration, not a deploy-time side effect.
    unknown_permissions: tuple[str, ...] = field(default=())

    @property
    def has_changes(self) -> bool:
        return bool(
            self.permissions_created
            or self.roles_created
            or self.grants_added
            or self.grants_removed
        )


async def seed_system_roles(session: AsyncSession) -> SeedReport:
    """Bring roles, permissions and their edges in line with the catalogue.

    The caller owns the transaction (CODING_STANDARDS 7). Either the whole
    reconciliation lands or none of it does; a half-seeded role is a role that
    silently lacks a permission, which shows up as an authorization bug rather
    than as a failed deploy.
    """
    permissions_created = await _ensure_permissions(session)
    roles_created = await _ensure_roles(session)
    added, removed = await _reconcile_role_permissions(session)

    known = {code.value for code in PermissionCode}
    stored = set((await session.scalars(select(Permission.code))).all())

    return SeedReport(
        permissions_created=permissions_created,
        roles_created=roles_created,
        grants_added=added,
        grants_removed=removed,
        unknown_permissions=tuple(sorted(stored - known)),
    )


async def _ensure_permissions(session: AsyncSession) -> tuple[str, ...]:
    rows = [
        {"id": uuid7(), "code": code.value, "description": _describe_permission(code)}
        for code in PermissionCode
    ]
    # ON CONFLICT DO NOTHING with RETURNING: the returned rows are exactly the
    # ones this call inserted, which is what makes "second run changes nothing"
    # something the report can state rather than something we hope for.
    statement = (
        insert(Permission).values(rows).on_conflict_do_nothing(index_elements=["code"])
    ).returning(Permission.code)
    created = (await session.scalars(statement)).all()
    return tuple(sorted(created))


async def _ensure_roles(session: AsyncSession) -> tuple[str, ...]:
    rows = [
        {
            "id": uuid7(),
            "code": code.value,
            "description": _describe_role(code),
            "is_system": True,
        }
        for code in SYSTEM_ROLE_PERMISSIONS
    ]
    statement = (
        insert(Role).values(rows).on_conflict_do_nothing(index_elements=["code"])
    ).returning(Role.code)
    created = (await session.scalars(statement)).all()
    return tuple(sorted(created))


async def _reconcile_role_permissions(
    session: AsyncSession,
) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    """Make the stored edges of every system role equal the catalogue's."""
    # .tuples(): a Result yields Row objects, which behave like tuples but are
    # not typed as ones. It is the difference between a dict of the declared
    # types and a dict of Any.
    role_ids: dict[str, uuid.UUID] = dict(
        (await session.execute(select(Role.code, Role.id))).tuples().all()
    )
    permission_ids: dict[str, uuid.UUID] = dict(
        (await session.execute(select(Permission.code, Permission.id))).tuples().all()
    )

    wanted: set[tuple[str, str]] = {
        (role.value, permission.value)
        for role, permissions in SYSTEM_ROLE_PERMISSIONS.items()
        for permission in permissions
    }

    system_roles = {code.value for code in SYSTEM_ROLE_PERMISSIONS}
    stored_rows = await session.execute(
        select(Role.code, Permission.code)
        .select_from(RolePermission)
        .join(Role, Role.id == RolePermission.role_id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Role.code.in_(system_roles))
    )
    stored: set[tuple[str, str]] = set(stored_rows.tuples().all())

    to_add = sorted(wanted - stored)
    to_remove = sorted(stored - wanted)

    if to_add:
        await session.execute(
            insert(RolePermission).values(
                [
                    {"role_id": role_ids[role], "permission_id": permission_ids[permission]}
                    for role, permission in to_add
                ]
            )
        )
    for role, permission in to_remove:
        # Deleting an edge is safe in a way that deleting a permission is not:
        # it revokes a capability the catalogue no longer grants, which is the
        # entire point of reconciling.
        await session.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role_ids[role],
                RolePermission.permission_id == permission_ids[permission],
            )
        )

    return tuple(to_add), tuple(to_remove)


def _describe_permission(code: PermissionCode) -> str:
    """Human-readable text for an administration screen, not a contract."""
    return code.value.replace("_", " ").capitalize()


def _describe_role(code: RoleCode) -> str:
    return code.value.replace("_", " ").capitalize()
