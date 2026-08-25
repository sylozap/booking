"""Domain: entities, value objects, rules, domain exceptions and ports.

Imports nothing but the standard library and itself — no pydantic, no
SQLAlchemy, no clock, no UUID generation (CODING_STANDARDS 2.2, 6). The test
for this rule is that every domain test runs without a database, a broker or a
network.
"""

__all__: list[str] = []
