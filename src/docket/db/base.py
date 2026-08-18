"""Declarative base — all tables live in the ``docket`` schema."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from docket.config import DB_SCHEMA


class Base(DeclarativeBase):
    """Base class for all ORM models.

    Binding the schema to ``MetaData`` keeps docket's tables isolated from
    wild-life's in the same database. Sharing an instance is convenience; sharing
    a schema would be a dependency.
    """

    metadata = MetaData(schema=DB_SCHEMA)
