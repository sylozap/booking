"""Service chassis: cross-cutting infrastructure shared by every service.

Scope is limited to infrastructure — logging, telemetry, configuration, error
format, health endpoints, outbox and idempotent consumption. Domain models and
DTOs of any service are forbidden here (ADR-0009, D7).

The chassis is extracted from ``identity`` in phase 2; this package is an empty
skeleton until then.
"""

__all__: list[str] = []
