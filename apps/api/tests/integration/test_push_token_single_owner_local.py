"""Local PostgreSQL regression for push-token single-owner semantics.

The fixture refuses hosted databases and creates only a random, private schema
on an explicitly enabled loopback PostgreSQL 17 instance. It runs the actual
Alembic revision against a legacy-shaped table, then exercises the API's
PostgreSQL upsert including competing registrations. No external provider or
network is used.
"""

from __future__ import annotations

import asyncio
import importlib
import ipaddress
import os
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.notifications import TokenRequest, register_token
from app.db.models import Profile, PushToken


def _local_urls(value: str) -> tuple[str, str]:
    split_value = urlsplit(value)
    if split_value.query or split_value.fragment:
        raise ValueError("TEST_DATABASE_URL query and fragment overrides are forbidden")
    parsed = make_url(value)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL")
    if (parsed.host or "").lower().rstrip(".") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("push-token integration test accepts localhost PostgreSQL only")
    return (
        parsed.set(drivername="postgresql").render_as_string(hide_password=False),
        parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


@pytest_asyncio.fixture
async def push_token_sessions(
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if os.environ.get("SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS") != "1":
        pytest.skip("requires SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS=1 for local PostgreSQL")
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL must be explicitly set to local PostgreSQL")
    try:
        admin_url, async_url = _local_urls(value)
    except ValueError as exc:
        pytest.fail(str(exc))

    try:
        admin = await asyncpg.connect(admin_url, timeout=10)
    except Exception as exc:
        pytest.fail(f"local PostgreSQL connection failed ({type(exc).__name__})")

    schema = f"push_token_local_{uuid.uuid4().hex}"
    try:
        server_address, version = await admin.fetchrow(
            "SELECT inet_server_addr()::text, current_setting('server_version_num')"
        )
        try:
            address = ipaddress.ip_interface(server_address).ip
        except (TypeError, ValueError):
            pytest.fail("integration test requires a loopback TCP PostgreSQL server")
        if not address.is_loopback:
            pytest.fail("integration test refuses a non-loopback PostgreSQL server")
        if not str(version).startswith("17"):
            pytest.fail("push-token integration test requires PostgreSQL 17")
        await admin.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION CURRENT_USER')
        await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
    finally:
        await admin.close()

    engine = create_async_engine(
        async_url,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"search_path": f'"{schema}",pg_catalog'},
        },
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Profile.__table__.create)
            # Recreate the pre-0012 schema, which had only the composite PK and
            # could therefore contain a token routed to multiple accounts.
            await connection.execute(
                text(
                    """CREATE TABLE push_tokens (
                         user_id uuid NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
                         token varchar NOT NULL,
                         platform varchar NOT NULL,
                         created_at timestamptz NOT NULL DEFAULT now(),
                         PRIMARY KEY (user_id, token)
                    )"""
                )
            )

            actual_schema = await connection.scalar(text("SELECT current_schema()"))
            assert actual_schema == schema
        yield sessions
    finally:
        await engine.dispose()
        cleanup = await asyncpg.connect(admin_url, timeout=10)
        try:
            await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await cleanup.close()


@pytest.mark.asyncio
async def test_migration_deduplicates_then_atomic_claim_has_one_owner(
    push_token_sessions: async_sessionmaker[AsyncSession],
) -> None:
    older_owner, newest_owner, tie_owner, replacement_owner = sorted(
        str(uuid.uuid4()) for _ in range(4)
    )
    tied_token = "ExpoPushToken[tie-case]"
    rotated_token = "ExpoPushToken[replace-case]"
    async with push_token_sessions() as session:
        session.add_all(Profile(id=owner) for owner in (older_owner, newest_owner, tie_owner, replacement_owner))
        await session.flush()
        await session.execute(
            text(
                """INSERT INTO push_tokens(user_id, token, platform, created_at)
                   VALUES
                     (:older, :token, 'ios', '2026-10-19T00:00:00Z'),
                     (:newer, :token, 'android', '2026-10-20T00:00:00Z'),
                     (:newer, :tied, 'ios', '2026-10-20T00:00:00Z'),
                     (:tie, :tied, 'android', '2026-10-20T00:00:00Z')"""
            ),
            {
                "older": older_owner,
                "newer": newest_owner,
                "token": rotated_token,
                "tied": tied_token,
                "tie": tie_owner,
            },
        )
        await session.commit()

        def apply_revision(sync_connection: object) -> None:
            revision = importlib.import_module(
                "app.db.migrations.versions.20261020_0012_push_token_single_owner"
            )
            with Operations.context(MigrationContext.configure(sync_connection)):
                revision.upgrade()

        connection = await session.connection()
        await connection.run_sync(apply_revision)
        await session.commit()

    async with push_token_sessions() as session:
        rows = list((await session.scalars(select(PushToken).order_by(PushToken.token))).all())
        by_token = {row.token: row for row in rows}
        assert set(by_token) == {rotated_token, tied_token}
        assert by_token[rotated_token].user_id == newest_owner
        # Migration ordering is created_at DESC, user_id ASC for equal times.
        assert by_token[tied_token].user_id == min(newest_owner, tie_owner)

        constraint_exists = await session.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                "WHERE conname = 'push_tokens_token_unique' "
                "AND conrelid = 'push_tokens'::regclass)"
            )
        )
        assert constraint_exists is True

    # Exercise the production route's ON CONFLICT(token) path: the current
    # owner can refresh, but a different account cannot take over its token.
    async with push_token_sessions() as session:
        await register_token(
            TokenRequest(token=rotated_token, platform="ios"),
            SimpleNamespace(id=newest_owner),
            session,
        )
    async with push_token_sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(PushToken).where(PushToken.token == rotated_token)
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].user_id == newest_owner
        assert rows[0].platform == "ios"

    async with push_token_sessions() as session:
        with pytest.raises(HTTPException) as conflict:
            await register_token(
                TokenRequest(token=rotated_token, platform="android"),
                SimpleNamespace(id=replacement_owner),
                session,
            )
        assert conflict.value.status_code == 409
    async with push_token_sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(PushToken).where(PushToken.token == rotated_token)
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].user_id == newest_owner
        assert rows[0].platform == "ios"

    # Competing authenticated owners serialize on the unique token constraint.
    contenders = (older_owner, newest_owner, tie_owner)
    ready = 0
    ready_lock = asyncio.Lock()
    start = asyncio.Event()

    async def claim(owner_id: str) -> None:
        nonlocal ready
        async with ready_lock:
            ready += 1
            if ready == len(contenders):
                start.set()
        await start.wait()
        async with push_token_sessions() as session:
            await register_token(
                TokenRequest(token="ExpoPushToken[competing-claims]", platform="android"),
                SimpleNamespace(id=owner_id),
                session,
            )

    outcomes = await asyncio.gather(
        *(claim(owner) for owner in contenders), return_exceptions=True
    )
    assert sum(not isinstance(outcome, BaseException) for outcome in outcomes) == 1
    rejected = [outcome for outcome in outcomes if isinstance(outcome, HTTPException)]
    assert len(rejected) == len(contenders) - 1
    assert all(outcome.status_code == 409 for outcome in rejected)
    async with push_token_sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(PushToken).where(
                        PushToken.token == "ExpoPushToken[competing-claims]"
                    )
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].user_id in contenders
        assert rows[0].platform == "android"
