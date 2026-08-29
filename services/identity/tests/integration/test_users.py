"""users and oauth_accounts enforce identity in the database (P1-T09).

Every assertion here is about a constraint, not about code. Application code
runs in several replicas at once, so a check-then-insert in Python excludes
nothing: two requests both read "no such email" and both insert. What these
tests prove is that the second insert fails no matter how the first one raced.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chassis import uuid7_timestamp_ms
from identity.domain.user import OAuthProvider, UserStatus
from identity.infrastructure.db.models import OAuthAccount, User


@pytest.mark.asyncio
async def test_user_is_stored_with_its_defaults(db_session: AsyncSession) -> None:
    user = User(email="ada@example.com", full_name="Ada Lovelace")

    db_session.add(user)
    await db_session.flush()

    assert user.status is UserStatus.ACTIVE
    assert user.timezone == "UTC"
    # No password: a Google-only account has none, and the column allows it.
    assert user.password_hash is None


@pytest.mark.asyncio
async def test_user_id_is_a_uuid_version_7(db_session: AsyncSession) -> None:
    """D41: keys are time-ordered so inserts stay at the right edge of the index."""
    user = User(email="ada@example.com", full_name="Ada Lovelace")

    db_session.add(user)
    await db_session.flush()

    assert user.id.version == 7
    assert uuid7_timestamp_ms(user.id) > 0


@pytest.mark.asyncio
async def test_duplicate_email_is_rejected_by_the_database(db_session: AsyncSession) -> None:
    db_session.add(User(email="ada@example.com", full_name="Ada Lovelace"))
    await db_session.flush()

    db_session.add(User(email="ada@example.com", full_name="Someone Else"))

    with pytest.raises(IntegrityError, match="uq_users_email"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_email_differing_only_in_case_is_the_same_email(db_session: AsyncSession) -> None:
    """citext: case-insensitivity is a property of the column, not of queries.

    Doing it with lower() in every query means the one query that forgets is
    the one that creates the duplicate account.
    """
    db_session.add(User(email="Ada@Example.COM", full_name="Ada Lovelace"))
    await db_session.flush()

    db_session.add(User(email="ada@example.com", full_name="Someone Else"))

    with pytest.raises(IntegrityError, match="uq_users_email"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_email_lookup_ignores_case(db_session: AsyncSession) -> None:
    db_session.add(User(email="Ada@Example.COM", full_name="Ada Lovelace"))
    await db_session.flush()

    found = await db_session.scalar(select(User).where(User.email == "ada@example.com"))

    assert found is not None
    assert found.full_name == "Ada Lovelace"


@pytest.mark.asyncio
async def test_one_provider_account_cannot_belong_to_two_users(db_session: AsyncSession) -> None:
    first = User(email="ada@example.com", full_name="Ada Lovelace")
    second = User(email="grace@example.com", full_name="Grace Hopper")
    db_session.add_all([first, second])
    await db_session.flush()
    db_session.add(
        OAuthAccount(
            user_id=first.id, provider=OAuthProvider.GOOGLE, provider_user_id="google-subject-1"
        )
    )
    await db_session.flush()

    db_session.add(
        OAuthAccount(
            user_id=second.id, provider=OAuthProvider.GOOGLE, provider_user_id="google-subject-1"
        )
    )

    with pytest.raises(IntegrityError, match="uq_oauth_accounts_provider_provider_user_id"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_a_user_can_link_several_providers(db_session: AsyncSession) -> None:
    """The same external id under different providers is a different person."""
    user = User(email="ada@example.com", full_name="Ada Lovelace")
    db_session.add(user)
    await db_session.flush()

    db_session.add(
        OAuthAccount(
            user_id=user.id, provider=OAuthProvider.GOOGLE, provider_user_id="google-subject-1"
        )
    )
    await db_session.flush()

    linked = await db_session.scalars(select(OAuthAccount).where(OAuthAccount.user_id == user.id))
    assert len(linked.all()) == 1


@pytest.mark.asyncio
async def test_deleting_a_user_removes_their_provider_links(db_session: AsyncSession) -> None:
    """A link to a deleted user is a credential pointing at nothing."""
    user = User(email="ada@example.com", full_name="Ada Lovelace")
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        OAuthAccount(
            user_id=user.id, provider=OAuthProvider.GOOGLE, provider_user_id="google-subject-1"
        )
    )
    await db_session.flush()

    await db_session.delete(user)
    await db_session.flush()

    remaining = await db_session.scalars(select(OAuthAccount))
    assert remaining.all() == []
