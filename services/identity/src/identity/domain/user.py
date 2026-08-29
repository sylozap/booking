"""User states and the external identity providers a user can arrive through."""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    """Where an account is in its life cycle.

    ``ANONYMIZED`` is what account deletion means here (ADR-0017): the row
    survives with its personal data stripped, because bookings and payments
    reference it and cannot be deleted alongside a profile. The distinction
    from ``DISABLED`` matters — a disabled account can be restored, an
    anonymized one cannot, and only the second is irreversible.
    """

    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ANONYMIZED = "ANONYMIZED"


class OAuthProvider(StrEnum):
    """External identity providers. Only Google is in MVP scope (ADR-0010)."""

    GOOGLE = "GOOGLE"
