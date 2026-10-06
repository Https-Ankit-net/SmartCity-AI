"""Alembic environment: runs migrations against DATABASE_URL using the app's models."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.db.database import DATABASE_URL, Base

config = context.config
target_metadata = Base.metadata

# Called from app.db.migrate with an open connection: the app owns logging, so leave it alone.
connection = config.attributes.get("connection")
if connection is None and config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite can't ALTER most things in place; batch mode copies the table instead.
        render_as_batch=kwargs.pop("is_sqlite"),
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"}, is_sqlite=DATABASE_URL.startswith("sqlite"))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if connection is not None:
        _configure(connection=connection, is_sqlite=connection.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = DATABASE_URL
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as conn:
        _configure(connection=conn, is_sqlite=conn.dialect.name == "sqlite")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
