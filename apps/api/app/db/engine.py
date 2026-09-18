"""Async SQLAlchemy engine + session factory.

Every request handler that touches the DB should depend on `get_session`
(FastAPI dependency) rather than opening a session directly — this keeps
transaction scope bounded to the request and gives us one place to add
per-request timeout / retry logic later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
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
