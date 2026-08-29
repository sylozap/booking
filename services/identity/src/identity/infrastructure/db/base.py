"""Declarative base shared by every ORM model in this service.

Two settings here are worth more than they look.

**Naming convention.** Without it PostgreSQL invents constraint names and
Alembic writes them into migrations as it found them, so ``downgrade`` on a
database created by a different PostgreSQL version drops a constraint that does
not exist under that name. With it, every index, unique, check and foreign key
is named by a rule, in the migration and in the database alike.

**timestamptz everywhere.** A ``datetime`` column is a moment, and a moment
without a zone is ambiguous twice a year (D13). The mapping makes the correct
type the one you get by default rather than the one you must remember to ask
for; local time never reaches the database at all.
"""

from __future__ import annotations

import datetime as dt
from typing import Final

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012  # SQLAlchemy reads this as a plain class attribute.
        dt.datetime: DateTime(timezone=True),
    }
