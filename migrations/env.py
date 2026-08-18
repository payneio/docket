"""Alembic environment — synchronous psycopg engine, docket schema only."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from docket.config import DB_SCHEMA, settings
from docket.db.base import Base
import docket.models  # noqa: F401  (import for side effect: registers every table)

config = context.config
config.set_main_option("sqlalchemy.url", settings.sync_database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Never touch anything outside our schema. The database is shared with
    wild-life and others; an autogenerate that saw their tables would offer to
    drop them."""
    if type_ == "table":
        return obj.schema == DB_SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema=DB_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            version_table_schema=DB_SCHEMA,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
