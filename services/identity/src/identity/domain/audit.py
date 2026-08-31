"""What the audit log records (D48).

Business actions are already reconstructible from the outbox: every state change
publishes an event that carries who did what. Permission changes are not — a
grant is not a business event any consumer reacts to, and it is exactly the
change an investigation asks about first. Hence one table, in this service,
for this class of action only.
"""

from __future__ import annotations

from enum import StrEnum


class AuditAction(StrEnum):
    ROLE_GRANTED = "ROLE_GRANTED"
    ROLE_REVOKED = "ROLE_REVOKED"
