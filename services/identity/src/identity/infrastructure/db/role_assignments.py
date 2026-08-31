"""Granting and revoking roles.

Both operations report whether they changed anything, which the database
decides rather than a preceding SELECT: granting a role somebody already holds
is a no-op, and two concurrent grants must not both believe they were first.
The audit record that has to accompany a real change arrives with P1-T12.

The caller owns the transaction (CODING_STANDARDS 7). The scenario that calls
this arrives with the consumer of ``SpecialistInvited`` in phase 2 (ADR-0013);
until then the grant path has no HTTP surface, and inventing one now would mean
guessing at its shape.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chassis import uuid7
from identity.domain.access import RoleCode, requires_tenant
from identity.domain.exceptions import RoleScopeMismatch
from identity.domain.identifiers import TenantId, UserId
from identity.infrastructure.db.models import Role, UserRole


class UnknownRoleError(LookupError):
    """A role from the catalogue has no row in the database.

    Not a DomainError: the code came from ``identity.domain.access``, so no
    request can cause this and no client can fix it. It means the seed has not
    run against this database — a deployment fault, answered as a 500 by the
    handler of last resort rather than dressed up as a 4xx.
    """


class RoleAssignmentRepository:
    """Reads and writes role grants, and records every change it makes."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def grant(
        self,
        *,
        user_id: UserId,
        role: RoleCode,
        tenant_id: TenantId | None,
        actor_user_id: UserId | None = None,
    ) -> bool:
        """Give the user this role in this tenant. True if anything changed."""
        _check_scope(role, tenant_id)
        role_id = await self._role_id(role)

        # ON CONFLICT DO NOTHING ... RETURNING: the database decides whether
        # this is new, atomically. A SELECT-then-INSERT would let two concurrent
        # grants both believe they were first and both write an audit entry.
        statement = (
            pg_insert(UserRole)
            .values(
                id=uuid7(),
                user_id=user_id,
                role_id=role_id,
                tenant_id=tenant_id,
                granted_by_user_id=actor_user_id,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "role_id", "tenant_id"])
            .returning(UserRole.id)
        )
        return (await self._session.scalars(statement)).one_or_none() is not None

    async def revoke(
        self,
        *,
        user_id: UserId,
        role: RoleCode,
        tenant_id: TenantId | None,
    ) -> bool:
        """Take the role away in this tenant. True if anything changed.

        Scoped by ``tenant_id`` like every other query in this service (D40):
        revoking ORG_ADMIN must not touch the same person's grant in a
        different organization.
        """
        _check_scope(role, tenant_id)
        role_id = await self._role_id(role)

        statement = (
            delete(UserRole)
            .where(
                UserRole.user_id == user_id,
                UserRole.role_id == role_id,
                # IS NOT DISTINCT FROM, not ==: a platform-wide grant has a NULL
                # tenant, and `NULL = NULL` is NULL, so `==` would delete nothing
                # and silently report success.
                UserRole.tenant_id.is_not_distinct_from(tenant_id),
            )
            .returning(UserRole.id)
        )
        return (await self._session.scalars(statement)).one_or_none() is not None

    async def roles_in_tenant(
        self, *, user_id: UserId, tenant_id: TenantId | None
    ) -> tuple[RoleCode, ...]:
        """Which roles the user holds in this tenant, and only in this one."""
        rows = await self._session.scalars(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user_id,
                UserRole.tenant_id.is_not_distinct_from(tenant_id),
            )
        )
        return tuple(RoleCode(code) for code in rows.all())

    async def _role_id(self, role: RoleCode) -> uuid.UUID:
        role_id = await self._session.scalar(select(Role.id).where(Role.code == role.value))
        if role_id is None:
            raise UnknownRoleError(f"role {role.value} is missing; has the seed run?")
        return role_id


def _check_scope(role: RoleCode, tenant_id: TenantId | None) -> None:
    if requires_tenant(role) and tenant_id is None:
        raise RoleScopeMismatch(f"{role.value} is granted within an organization")
    if not requires_tenant(role) and tenant_id is not None:
        raise RoleScopeMismatch(f"{role.value} is platform-wide and takes no organization")
