"""Only the hash of a refresh token reaches the database (P1-T11, D21).

A refresh token is a bearer credential with a long life. If the table holds it
in the clear, a single read of that table — a backup, a query during an
incident, a misdirected export — is a silent takeover of every account in it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from chassis import uuid7
from identity.domain.identifiers import UserId
from identity.infrastructure.db.models import RefreshToken, User


def _issue() -> tuple[str, str]:
    """A token as it would be handed to a client, and what we may keep of it.

    SHA-256 rather than argon2: the token is 256 bits of randomness, so there is
    no dictionary to run against the digest, and a slow hash on the refresh path
    would buy nothing at the cost of latency on every renewal.
    """
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


async def _user(session: AsyncSession) -> UserId:
    user = User(email="ada@example.com", full_name="Ada Lovelace")
    session.add(user)
    await session.flush()
    return UserId(user.id)


@pytest.mark.asyncio
async def test_the_plaintext_token_appears_in_no_column(db_session: AsyncSession) -> None:
    """Every column at once, not just the one we meant to hash.

    Casting the whole row to text renders every value in it, so a token that
    leaked into user_agent — or into a column added later — fails this too.
    """
    user_id = await _user(db_session)
    token, digest = _issue()
    db_session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=digest,
            family_id=uuid7(),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
            user_agent="Mozilla/5.0",
            ip="203.0.113.7",
        )
    )
    await db_session.flush()

    rendered = await db_session.scalars(text("SELECT refresh_tokens::text FROM refresh_tokens"))
    rows = rendered.all()

    assert len(rows) == 1
    assert token not in rows[0]
    assert digest in rows[0]


@pytest.mark.asyncio
async def test_the_same_token_cannot_be_stored_twice(db_session: AsyncSession) -> None:
    """Lookup on refresh is by hash; two rows for one hash makes it ambiguous."""
    user_id = await _user(db_session)
    _, digest = _issue()
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
    db_session.add(
        RefreshToken(user_id=user_id, token_hash=digest, family_id=uuid7(), expires_at=expires_at)
    )
    await db_session.flush()

    db_session.add(
        RefreshToken(user_id=user_id, token_hash=digest, family_id=uuid7(), expires_at=expires_at)
    )

    with pytest.raises(IntegrityError, match="uq_refresh_tokens_token_hash"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_a_token_cannot_expire_before_it_was_issued(db_session: AsyncSession) -> None:
    """An already-expired token is a bug in the issuer, caught at the write."""
    user_id = await _user(db_session)
    _, digest = _issue()

    db_session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=digest,
            family_id=uuid7(),
            issued_at=dt.datetime.now(dt.UTC),
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1),
        )
    )

    with pytest.raises(IntegrityError, match="ck_refresh_tokens_expiry_after_issue"):
        await db_session.flush()


@pytest.mark.asyncio
async def test_rotation_links_a_token_to_its_successor(db_session: AsyncSession) -> None:
    """replaced_by is what makes a second use of one token detectable."""
    user_id = await _user(db_session)
    family_id = uuid7()
    expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
    _, first_digest = _issue()
    _, second_digest = _issue()
    first = RefreshToken(
        user_id=user_id, token_hash=first_digest, family_id=family_id, expires_at=expires_at
    )
    db_session.add(first)
    await db_session.flush()

    second = RefreshToken(
        user_id=user_id, token_hash=second_digest, family_id=family_id, expires_at=expires_at
    )
    db_session.add(second)
    await db_session.flush()
    first.replaced_by_id = second.id
    first.revoked_at = dt.datetime.now(dt.UTC)
    await db_session.flush()

    family = await db_session.scalars(
        select(RefreshToken).where(RefreshToken.family_id == family_id)
    )
    assert len(family.all()) == 2
    assert first.replaced_by_id == second.id


@pytest.mark.asyncio
async def test_deleting_a_user_removes_their_tokens(db_session: AsyncSession) -> None:
    """A token outliving its user is a credential for an account that is gone."""
    user_id = await _user(db_session)
    _, digest = _issue()
    db_session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=digest,
            family_id=uuid7(),
            expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(days=30),
        )
    )
    await db_session.flush()

    user = await db_session.get(User, user_id)
    assert user is not None
    await db_session.delete(user)
    await db_session.flush()

    remaining = await db_session.scalars(select(RefreshToken))
    assert remaining.all() == []
