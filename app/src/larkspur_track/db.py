"""Database engine and the declarative base for ORM models."""

from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase

from larkspur_track.config import get_settings

# Predictable constraint names so Alembic migrations stay stable and reviewable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache
def get_engine() -> Engine:
    """Create the engine on first use, so liveness checks never need the database."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)
