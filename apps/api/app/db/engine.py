"""Async SQLAlchemy engine + session factory.

Every request handler that touches the DB should depend on `get_session`
(FastAPI dependency) rather than opening a session directly — this keeps
transaction scope bounded to the request and gives us one place to add
per-request timeout / retry logic later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_pg_pool: asyncpg.Pool | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        # Supabase's transaction pooler (:6543) is pgbouncer in transaction
        # mode — it doesn't support asyncpg's server-side prepared-statement
        # cache. Disable it via connect_args so we can safely use the pooler
        # for the app's OLTP traffic. `pool_pre_ping` is unnecessary since
        # pgbouncer manages the underlying pool for us.
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_size=5,
            max_overflow=10,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
            },
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _session_factory


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency — one session per request, auto-close."""
    async with _get_session_factory()() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Use in worker code that isn't inside a FastAPI request."""
    async with _get_session_factory()() as session:
        yield session


async def get_pg_pool() -> asyncpg.Pool:
    """Shared raw-asyncpg pool for pgvector similarity queries.

    Separate from the SQLAlchemy engine on purpose: pgvector similarity
    uses raw SQL with the `<=>` operator plus a per-connection codec
    (`register_vector`), which is simplest on a plain asyncpg pool.
    DSN is derived from the same DATABASE_URL setting (asyncpg scheme).
    """
    global _pg_pool
    if _pg_pool is None:
        settings = get_settings()
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        _pg_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    return _pg_pool


async def close_pg_pool() -> None:
    """Close the shared pgvector pool (tests / shutdown)."""
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
