"""Permission changes are recorded, in the same transaction (P1-T12, D48).

A grant that is not in the log is worse than one that never happened: the log is
what an investigation trusts, and a gap in it is indistinguishable from nothing
having occurred.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from chassis import uuid7
from identity.domain.access import RoleCode
from identity.domain.audit import AuditAction
from identity.domain.identifiers import TenantId, UserId
from identity.infrastructure.db.models import AuditEntry, Role, User
from identity.infrastructure.db.role_assignments import RoleAssignmentRepository
from identity.infrastructure.db.seed import seed_system_roles
from identity.infrastructure.db.session import SessionFactory, transaction


@pytest.fixture
def acme() -> TenantId:
    return TenantId(uuid7())


async def _user(session: AsyncSession, email: str) -> UserId:
    user = User(email=email, full_name="Ada Lovelace")
    session.add(user)
    await session.flush()
    return UserId(user.id)


async def _entries(session: AsyncSession) -> list[AuditEntry]:
    rows = await session.scalars(select(AuditEntry).order_by(AuditEntry.occurred_at))
    return list(rows.all())


@pytest.mark.asyncio
async def test_granting_a_role_records_actor_subject_and_time(
    db_session: AsyncSession, acme: TenantId
) -> None:
    await seed_system_roles(db_session)
    subject = await _user(db_session, "ada@example.com")
    actor = await _user(db_session, "grace@example.com")
    before = dt.datetime.now(dt.UTC)

    await RoleAssignmentRepository(db_session).grant(
        user_id=subject, role=RoleCode.ORG_ADMIN, tenant_id=acme, actor_user_id=actor
    )

    entries = await _entries(db_session)
    assert len(entries) == 1
    assert entries[0].action is AuditAction.ROLE_GRANTED
    assert entries[0].actor_user_id == actor
    assert entries[0].subject_user_id == subject
    assert entries[0].role_code == RoleCode.ORG_ADMIN.value
    assert entries[0].tenant_id == acme
    assert entries[0].occurred_at >= before


@pytest.mark.asyncio
async def test_revoking_a_role_records_it_too(db_session: AsyncSession, acme: TenantId) -> None:
    await seed_system_roles(db_session)
    subject = await _user(db_session, "ada@example.com")
    actor = await _user(db_session, "grace@example.com")
    repository = RoleAssignmentRepository(db_session)
    await repository.grant(
        user_id=subject, role=RoleCode.ORG_ADMIN, tenant_id=acme, actor_user_id=actor
    )

    await repository.revoke(
        user_id=subject, role=RoleCode.ORG_ADMIN, tenant_id=acme, actor_user_id=actor
    )

    actions = [entry.action for entry in await _entries(db_session)]
    assert actions == [AuditAction.ROLE_GRANTED, AuditAction.ROLE_REVOKED]


@pytest.mark.asyncio
async def test_a_grant_that_changed_nothing_is_not_recorded(
    db_session: AsyncSession, acme: TenantId
) -> None:
    """The log records what happened, not what was attempted."""
    await seed_system_roles(db_session)
    subject = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)
    await repository.grant(user_id=subject, role=RoleCode.CLIENT, tenant_id=acme)

    await repository.grant(user_id=subject, role=RoleCode.CLIENT, tenant_id=acme)

    assert len(await _entries(db_session)) == 1


@pytest.mark.asyncio
async def test_a_revoke_that_changed_nothing_is_not_recorded(
    db_session: AsyncSession, acme: TenantId
) -> None:
    await seed_system_roles(db_session)
    subject = await _user(db_session, "ada@example.com")

    await RoleAssignmentRepository(db_session).revoke(
        user_id=subject, role=RoleCode.CLIENT, tenant_id=acme
    )

    assert await _entries(db_session) == []


@pytest.mark.asyncio
async def test_a_grant_without_a_human_actor_is_recorded(
    db_session: AsyncSession, acme: TenantId
) -> None:
    """Grants arrive from events too (ADR-0013); those have no actor."""
    await seed_system_roles(db_session)
    subject = await _user(db_session, "ada@example.com")

    await RoleAssignmentRepository(db_session).grant(
        user_id=subject, role=RoleCode.SPECIALIST, tenant_id=acme
    )

    entries = await _entries(db_session)
    assert len(entries) == 1
    assert entries[0].actor_user_id is None
    assert entries[0].subject_user_id == subject


@pytest.mark.asyncio
async def test_the_entry_outlives_the_role_it_names(
    db_session: AsyncSession, acme: TenantId
) -> None:
    """The code is copied in, so deleting the role does not blank the history."""
    await seed_system_roles(db_session)
    subject = await _user(db_session, "ada@example.com")
    repository = RoleAssignmentRepository(db_session)
    await repository.grant(user_id=subject, role=RoleCode.SPECIALIST, tenant_id=acme)
    await repository.revoke(user_id=subject, role=RoleCode.SPECIALIST, tenant_id=acme)

    await db_session.execute(delete(Role).where(Role.code == RoleCode.SPECIALIST.value))
    await db_session.flush()

    codes = {entry.role_code for entry in await _entries(db_session)}
    assert codes == {RoleCode.SPECIALIST.value}


@pytest.mark.asyncio
async def test_a_rolled_back_grant_leaves_no_entry(
    session_factory: SessionFactory, acme: TenantId
) -> None:
    """One transaction: there is no way to have the grant without the record.

    Asserted from the failing side, because that is the side that can go wrong
    silently — an audit row written by a listener after the commit survives a
    rollback of the change it claims to describe.
    """
    async with transaction(session_factory) as session:
        await seed_system_roles(session)
        subject = await _user(session, "ada@example.com")

    async def grant_then_fail() -> None:
        async with transaction(session_factory) as session:
            await RoleAssignmentRepository(session).grant(
                user_id=subject, role=RoleCode.CLIENT, tenant_id=acme
            )
            raise RuntimeError("deliberate")

    with pytest.raises(RuntimeError, match="deliberate"):
        await grant_then_fail()

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuditEntry)) == 0
