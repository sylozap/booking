"""Role grants are scoped to a tenant (P1-T10, D20).

The property that matters: the same person holds different roles in different
organizations, and neither grant says anything about the other. Everything a
permission check will later rely on is decided by these rows and by the unique
constraint over them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chassis import uuid7
from identity.domain.access import RoleCode
from identity.domain.exceptions import RoleScopeMismatch
from identity.domain.identifiers import TenantId, UserId
from identity.infrastructure.db.models import Role, User, UserRole
from identity.infrastructure.db.role_assignments import RoleAssignmentRepository
from identity.infrastructure.db.seed import seed_system_roles


@pytest.fixture
def acme() -> TenantId:
    """One organization. A test that needs two says so itself."""
    return TenantId(uuid7())


@pytest.fixture
def globex() -> TenantId:
    return TenantId(uuid7())


async def _user(session: AsyncSession, email: str) -> UserId:
    user = User(email=email, full_name="Ada Lovelace")
    session.add(user)
    await session.flush()
    return UserId(user.id)


@pytest.mark.asyncio
async def test_one_user_holds_different_roles_in_different_organizations(
    db_session: AsyncSession, acme: TenantId, globex: TenantId
) -> None:
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)

    await repository.grant(user_id=user_id, role=RoleCode.CLIENT, tenant_id=acme)
    await repository.grant(user_id=user_id, role=RoleCode.ORG_ADMIN, tenant_id=globex)

    assert await repository.roles_in_tenant(user_id=user_id, tenant_id=acme) == (RoleCode.CLIENT,)
    assert await repository.roles_in_tenant(user_id=user_id, tenant_id=globex) == (
        RoleCode.ORG_ADMIN,
    )


@pytest.mark.asyncio
async def test_roles_are_invisible_from_another_organization(
    db_session: AsyncSession, acme: TenantId
) -> None:
    """D40: a query without the tenant filter is the multi-tenant defect."""
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)
    await repository.grant(user_id=user_id, role=RoleCode.ORG_ADMIN, tenant_id=acme)

    elsewhere = await repository.roles_in_tenant(user_id=user_id, tenant_id=TenantId(uuid7()))

    assert elsewhere == ()


@pytest.mark.asyncio
async def test_granting_the_same_role_twice_changes_nothing(
    db_session: AsyncSession, acme: TenantId
) -> None:
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)
    assert await repository.grant(user_id=user_id, role=RoleCode.CLIENT, tenant_id=acme) is True

    granted_again = await repository.grant(user_id=user_id, role=RoleCode.CLIENT, tenant_id=acme)

    assert granted_again is False
    assert await repository.roles_in_tenant(user_id=user_id, tenant_id=acme) == (RoleCode.CLIENT,)


@pytest.mark.asyncio
async def test_duplicate_grant_is_rejected_by_the_database(
    db_session: AsyncSession, acme: TenantId
) -> None:
    """The repository is polite about it; the constraint is what guarantees it."""
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    role_id = await db_session.scalar(select(Role.id).where(Role.code == RoleCode.CLIENT.value))
    db_session.add(UserRole(user_id=user_id, role_id=role_id, tenant_id=acme))
    await db_session.flush()

    db_session.add(UserRole(user_id=user_id, role_id=role_id, tenant_id=acme))

    with pytest.raises(IntegrityError, match="uq_user_roles_user_id_role_id_tenant_id"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_duplicate_platform_wide_grant_is_rejected(db_session: AsyncSession) -> None:
    """NULLS NOT DISTINCT: without it two NULL tenants are two different grants."""
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    role_id = await db_session.scalar(
        select(Role.id).where(Role.code == RoleCode.SUPER_ADMIN.value)
    )
    db_session.add(UserRole(user_id=user_id, role_id=role_id, tenant_id=None))
    await db_session.flush()

    db_session.add(UserRole(user_id=user_id, role_id=role_id, tenant_id=None))

    with pytest.raises(IntegrityError, match="uq_user_roles_user_id_role_id_tenant_id"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_revoking_in_one_organization_leaves_the_other_alone(
    db_session: AsyncSession, acme: TenantId, globex: TenantId
) -> None:
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)
    await repository.grant(user_id=user_id, role=RoleCode.ORG_ADMIN, tenant_id=acme)
    await repository.grant(user_id=user_id, role=RoleCode.ORG_ADMIN, tenant_id=globex)

    await repository.revoke(user_id=user_id, role=RoleCode.ORG_ADMIN, tenant_id=acme)

    assert await repository.roles_in_tenant(user_id=user_id, tenant_id=acme) == ()
    assert await repository.roles_in_tenant(user_id=user_id, tenant_id=globex) == (
        RoleCode.ORG_ADMIN,
    )


@pytest.mark.asyncio
async def test_revoking_a_role_nobody_holds_changes_nothing(
    db_session: AsyncSession, acme: TenantId
) -> None:
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)

    revoked = await repository.revoke(user_id=user_id, role=RoleCode.CLIENT, tenant_id=acme)

    assert revoked is False


@pytest.mark.asyncio
async def test_platform_wide_role_is_granted_and_revoked_without_a_tenant(
    db_session: AsyncSession,
) -> None:
    """SUPER_ADMIN belongs to no organization; NULL is how that is expressed."""
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)

    granted = await repository.grant(user_id=user_id, role=RoleCode.SUPER_ADMIN, tenant_id=None)
    roles = await repository.roles_in_tenant(user_id=user_id, tenant_id=None)
    revoked = await repository.revoke(user_id=user_id, role=RoleCode.SUPER_ADMIN, tenant_id=None)

    assert granted is True
    assert roles == (RoleCode.SUPER_ADMIN,)
    assert revoked is True


@pytest.mark.asyncio
async def test_tenant_scoped_role_without_a_tenant_is_refused(
    db_session: AsyncSession,
) -> None:
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)

    with pytest.raises(RoleScopeMismatch, match="within an organization"):
        await repository.grant(user_id=user_id, role=RoleCode.ORG_ADMIN, tenant_id=None)


@pytest.mark.asyncio
async def test_platform_wide_role_with_a_tenant_is_refused(
    db_session: AsyncSession, acme: TenantId
) -> None:
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)

    with pytest.raises(RoleScopeMismatch, match="takes no organization"):
        await repository.grant(user_id=user_id, role=RoleCode.SUPER_ADMIN, tenant_id=acme)


@pytest.mark.asyncio
async def test_a_granted_role_cannot_be_deleted(db_session: AsyncSession, acme: TenantId) -> None:
    """ON DELETE RESTRICT: deleting a role somebody holds is not a small mistake."""
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    await RoleAssignmentRepository(db_session).grant(
        user_id=user_id, role=RoleCode.CLIENT, tenant_id=acme
    )
    role = await db_session.scalar(select(Role).where(Role.code == RoleCode.CLIENT.value))
    assert role is not None

    await db_session.delete(role)

    with pytest.raises(IntegrityError, match="fk_user_roles_role_id_roles"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_deleting_a_user_removes_their_grants(
    db_session: AsyncSession, acme: TenantId
) -> None:
    await seed_system_roles(db_session)
    user_id = await _user(db_session, "ada@example.com")
    await RoleAssignmentRepository(db_session).grant(
        user_id=user_id, role=RoleCode.CLIENT, tenant_id=acme
    )

    await db_session.execute(delete(User).where(User.id == user_id))

    remaining = await db_session.scalars(select(UserRole))
    assert remaining.all() == []
