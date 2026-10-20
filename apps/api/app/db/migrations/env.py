"""Alembic env — async-aware, reads DATABASE_URL from Settings."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from alembic.ddl.postgresql import PostgresqlImpl
from sqlalchemy import String, pool, text
from sqlalchemy.engine import make_url
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.db.models import Base
from app.settings import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


class PostgresqlWideRevisionImpl(PostgresqlImpl):
    """Use enough room for this repo's dated Alembic revision identifiers."""

    __dialect__ = "postgresql"

    def version_table_impl(
        self,
        *,
        version_table: str,
        version_table_schema: str | None,
        version_table_pk: bool,
        **kw: object,
    ):
        table = super().version_table_impl(
            version_table=version_table,
            version_table_schema=version_table_schema,
            version_table_pk=version_table_pk,
            **kw,
        )
        table.c.version_num.type = String(128)
        return table


target_metadata = Base.metadata


def _set_migration_url() -> str:
    """Resolve settings when Alembic runs, not when this module is imported."""
    settings = get_settings()
    # Migrations must bypass the pooler: DDL over pgbouncer's transaction mode
    # can hang or partially apply, so prefer the direct URL when configured.
    migration_url = settings.database_url_direct or settings.database_url
    config.set_main_option("sqlalchemy.url", migration_url)
    return migration_url


def run_migrations_offline() -> None:
    migration_url = _set_migration_url()
    context.configure(
        url=migration_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    expected_database = make_url(config.get_main_option("sqlalchemy.url")).database
    # This repository uses revision IDs longer than Alembic's default
    # VARCHAR(32). Create/widen the bookkeeping table before Alembic attempts
    # to store the first long revision, including on fresh installations.
    with connection.begin():
        actual_database = connection.execute(text("SELECT current_database()")).scalar_one()
        if expected_database and actual_database != expected_database:
            raise RuntimeError("Alembic connection database does not match configured target")
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS public.alembic_version "
                "(version_num VARCHAR(128) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            text(
                "ALTER TABLE public.alembic_version "
                "ALTER COLUMN version_num TYPE VARCHAR(128)"
            )
        )
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online() -> None:
    _set_migration_url()
    # Keep both asyncpg cache layers off. This is harmless for the direct
    # URL and required if migrations fall back to a transaction-pooler URL.
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
        },
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_migrations_online())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
