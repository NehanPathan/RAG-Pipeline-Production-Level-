import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

import src.infrastructure.database.postgres.models  # noqa: F401 — register all models
from src.infrastructure.database.postgres.connection import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def _database_url() -> str:
    """The database to migrate.

    The Alembic config wins when it carries a URL, and `.env` is the
    fallback. Previously this read `settings.database_url` unconditionally,
    which meant `sqlalchemy.url` was silently ignored: `alembic -x`, a
    `-c` override and a programmatic `set_main_option` all had no effect,
    and every migration ran against whatever `.env` happened to say.

    That made it impossible to migrate staging and production from one
    checkout -- and, more quietly, it meant a test that thought it was
    migrating a throwaway container was really migrating the developer's own
    database.
    """
    configured = config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured

    from src.config import get_settings

    return get_settings().database_url


async def run_async_migrations() -> None:
    connectable = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
