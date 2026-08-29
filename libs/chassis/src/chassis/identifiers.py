"""UUID version 7 generation (D41).

Every identifier in the system is a UUID: they cross service boundaries inside
events, where global uniqueness matters more than compactness. Version 7 rather
than version 4 because the first 48 bits are a millisecond timestamp, so
generated keys are time-ordered. A random v4 primary key scatters inserts across
the whole B-tree and dirties a new page for every row; a v7 key appends to the
right-hand edge, which is where the index is already cached.

Chassis rather than a copy in each service: this is cross-cutting infrastructure
with no domain in it, and five copies of a bit-layout are five chances to get the
layout wrong (D7 forbids domain types here, not utilities).

Python 3.12 has no uuid7 in the standard library; it lands in 3.14.
"""

from __future__ import annotations

import secrets
import time
from typing import Final
from uuid import UUID

# RFC 9562 §5.7 field widths, counted from the most significant bit:
#   unix_ts_ms  48 | ver 4 | rand_a 12 | var 2 | rand_b 62
_TIMESTAMP_SHIFT: Final = 80
_TIMESTAMP_MASK: Final = 0xFFFF_FFFF_FFFF
_VERSION: Final = 0x7
_VERSION_SHIFT: Final = 76
_RAND_A_BITS: Final = 12
_RAND_A_SHIFT: Final = 64
_VARIANT: Final = 0b10
_VARIANT_SHIFT: Final = 62
_RAND_B_BITS: Final = 62


def uuid7() -> UUID:
    """A time-ordered UUID, unique across processes without coordination.

    Ordering is by millisecond. Identifiers minted inside the same millisecond
    have no defined order between them, which is what the 74 random bits buy:
    they make collision negligible without a shared counter, and index locality
    is a property of the millisecond prefix, not of the tail.
    """
    timestamp_ms = time.time_ns() // 1_000_000

    value = (timestamp_ms & _TIMESTAMP_MASK) << _TIMESTAMP_SHIFT
    value |= _VERSION << _VERSION_SHIFT
    value |= secrets.randbits(_RAND_A_BITS) << _RAND_A_SHIFT
    value |= _VARIANT << _VARIANT_SHIFT
    value |= secrets.randbits(_RAND_B_BITS)

    return UUID(int=value)


def uuid7_timestamp_ms(identifier: UUID) -> int:
    """The millisecond the identifier was minted in.

    Useful when debugging: a row's age is readable from its primary key without
    joining anything. Raises for identifiers of another version, where the same
    bits mean something else entirely.
    """
    if identifier.version != _VERSION:
        raise ValueError(f"not a UUID version 7: {identifier}")
    return identifier.int >> _TIMESTAMP_SHIFT
