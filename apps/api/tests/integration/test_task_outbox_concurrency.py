"""PostgreSQL regression coverage for stale task-outbox finalization.

This test only connects when ``TEST_DATABASE_URL`` is explicitly set. It creates
one uniquely named schema per test and drops that schema at teardown; it never
runs Alembic or touches the database's public schema.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db import engine as db_engine
from app.db.models import TaskOutbox
from app.tasks import outbox as task_outbox

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _normalized_url(url: str, *, asyncpg_driver: bool) -> str:
    """Normalize a DSN without ever rendering it in test output."""
    parsed = make_url(url)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL with asyncpg")
    driver = "postgresql+asyncpg" if asyncpg_driver else "postgresql"
    return parsed.set(drivername=driver).render_as_string(hide_password=False)


async def _connect_admin(url: str, **kwargs: Any) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(
            _normalized_url(url, asyncpg_driver=False), timeout=10, **kwargs
        )
    except Exception as exc:
        # Driver errors can contain connection details. Keep test output
        # actionable without allowing a DSN to enter CI logs.
        raise RuntimeError(
            f"isolated PostgreSQL connection failed ({type(exc).__name__})"
        ) from None


@dataclass(frozen=True)
class IsolatedOutboxDatabase:
    schema: str
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    application_name: str
    database_user: str


@pytest_asyncio.fixture
async def isolated_outbox_database(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[IsolatedOutboxDatabase]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; isolated PostgreSQL test not run")

    schema = f"r01_outbox_{uuid.uuid4().hex}"
    admin = await _connect_admin(TEST_DATABASE_URL)
    schema_created = False
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        schema_created = True
        await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
        public_privileges = await admin.fetch(
            "SELECT acl.privilege_type "
            "FROM pg_catalog.pg_namespace AS namespace "
            "CROSS JOIN LATERAL pg_catalog.aclexplode("
            "COALESCE(namespace.nspacl, "
            "pg_catalog.acldefault('n', namespace.nspowner))"
            ") AS acl "
            "WHERE namespace.nspname = $1 AND acl.grantee = 0",
            schema,
        )
        assert not {
            row["privilege_type"] for row in public_privileges
        }.intersection({"USAGE", "CREATE"})
        database_name, database_user = await admin.fetchrow(
            "SELECT current_database(), current_user"
        )
    except Exception:
        # The schema name is generated locally and uniquely for this fixture.
        if schema_created:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        raise
    finally:
        await admin.close()

    # Bypass application Settings and its .env fallback. The only endpoint used
    # by this fixture is the explicitly supplied TEST_DATABASE_URL.
    application_name = f"outbox-test-{uuid.uuid4().hex[:12]}"
    database_engine = create_async_engine(
        _normalized_url(TEST_DATABASE_URL, asyncpg_driver=True),
        connect_args={
            "server_settings": {
                "search_path": f'"{schema}",pg_catalog',
                "application_name": application_name,
            }
        },
    )
    sessions = async_sessionmaker(database_engine, expire_on_commit=False)
    monkeypatch.setattr(db_engine, "_session_factory", sessions)

    try:
        async with database_engine.begin() as connection:
            await connection.run_sync(TaskOutbox.__table__.create)
            proof = await connection.exec_driver_sql(
                "SELECT current_database(), current_user, current_schema(), "
                "current_schemas(false), "
                "(SELECT n.nspname || '.' || c.relname FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.oid = to_regclass('task_outbox'))"
            )
            (
                actual_database,
                actual_user,
                actual_schema,
                visible_schemas,
                outbox_relation,
            ) = proof.one()
            assert actual_database == database_name
            assert actual_user == database_user
            assert actual_schema == schema
            assert visible_schemas[0] == schema
            assert "public" not in visible_schemas
            assert outbox_relation == f"{schema}.task_outbox"
        yield IsolatedOutboxDatabase(
            schema, database_engine, sessions, application_name, database_user
        )
    finally:
        await database_engine.dispose()
        admin = await _connect_admin(TEST_DATABASE_URL)
        try:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await admin.close()


@pytest.mark.parametrize("stale_completion", ["success", "error"])
async def test_stale_dispatch_finalization_preserves_reclaimed_lease(
    isolated_outbox_database: IsolatedOutboxDatabase,
    stale_completion: str,
) -> None:
    """A's token-checked finalizer cannot overwrite B's reclaimed lease."""
    database = isolated_outbox_database
    outbox_id = f"r01-{uuid.uuid4().hex}"
    token_a = f"lease-a-{uuid.uuid4().hex}"
    token_b = f"lease-b-{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    async with database.sessions() as session:
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="ingest",
                target_type="item",
                target_id=f"item-{uuid.uuid4().hex}",
                payload={"item_id": "test-item", "user_id": "test-user"},
                generation=1,
                lease_token=token_a,
                lease_until=now - timedelta(seconds=1),
            )
        )
        await session.commit()

    # B reclaims A's expired lease in a separate transaction. This places the
    # competing token between A's completed work and its finalization call.
    async with database.sessions() as session:
        lease_until_b = datetime.now(UTC) + timedelta(seconds=60)
        await session.execute(
            update(TaskOutbox)
            .where(TaskOutbox.id == outbox_id)
            .where(TaskOutbox.lease_token == token_a)
            .where(TaskOutbox.lease_until <= datetime.now(UTC))
            .values(lease_token=token_b, lease_until=lease_until_b)
        )
        await session.commit()

    async with database.sessions() as session:
        row = await session.scalar(select(TaskOutbox).where(TaskOutbox.id == outbox_id))
        assert row is not None
        assert row.lease_token == token_b
        assert row.lease_until == lease_until_b
        assert row.dispatched_at is None
        assert row.attempt_count == 0
        initial_available_at = row.available_at

    stale_error = (
        RuntimeError("simulated transport failure") if stale_completion == "error" else None
    )
    assert await task_outbox._finalize_dispatch(outbox_id, token_a, error=stale_error) is False

    async with database.sessions() as session:
        row = await session.scalar(select(TaskOutbox).where(TaskOutbox.id == outbox_id))
        assert row is not None
        assert row.lease_token == token_b
        assert row.lease_until == lease_until_b
        assert row.dispatched_at is None
        assert row.attempt_count == 0
        assert row.available_at == initial_available_at
        assert row.last_error is None


@pytest.mark.parametrize("stale_completion", ["success", "error"])
async def test_concurrent_reclaim_wins_against_stale_finalizer(
    isolated_outbox_database: IsolatedOutboxDatabase,
    stale_completion: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finalizer waiting on B's row lock must recheck B's committed token."""
    database = isolated_outbox_database
    assert TEST_DATABASE_URL is not None  # Fixture skips when not explicitly configured.

    outbox_id = f"r11-{uuid.uuid4().hex}"
    token_a = f"lease-a-{uuid.uuid4().hex}"
    token_b = f"lease-b-{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    async with database.sessions() as session:
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="ingest",
                target_type="item",
                target_id=f"item-{uuid.uuid4().hex}",
                payload={"item_id": "test-item", "user_id": "test-user"},
                generation=1,
                lease_token=token_a,
                lease_until=now - timedelta(seconds=1),
            )
        )
        await session.commit()

    # Connection B reclaims the expired row but holds its transaction open.
    # Connection A then reaches the production finalizer and blocks on B's row
    # lock. We observe that lock wait before committing B, so the interleaving
    # is real rather than a sequential replacement of the token.
    contender_name = f"outbox-b-{uuid.uuid4().hex[:10]}"
    observer_name = f"outbox-observer-{uuid.uuid4().hex[:8]}"
    path = f'"{database.schema}",pg_catalog'
    contender = await _connect_admin(
        TEST_DATABASE_URL,
        server_settings={"search_path": path, "application_name": contender_name},
    )
    observer = await _connect_admin(
        TEST_DATABASE_URL,
        server_settings={"search_path": path, "application_name": observer_name},
    )

    @asynccontextmanager
    async def identified_finalizer_session() -> AsyncIterator[AsyncSession]:
        async with database.sessions() as session:
            # Supabase's session pooler ignores the startup application_name
            # parameter, so set it inside the active transaction for reliable
            # pg_stat_activity observation during the blocked UPDATE.
            await session.execute(
                text("SELECT set_config('application_name', :name, false)"),
                {"name": database.application_name},
            )
            yield session

    monkeypatch.setattr(task_outbox, "session_scope", identified_finalizer_session)
    finalizer: asyncio.Task[bool] | None = None
    try:
        await contender.execute("BEGIN")
        lease_until_b = datetime.now(UTC) + timedelta(seconds=60)
        update_status = await contender.execute(
            "UPDATE task_outbox SET lease_token = $1, lease_until = $2 "
            "WHERE id = $3 AND lease_token = $4 AND lease_until <= $5",
            token_b,
            lease_until_b,
            outbox_id,
            token_a,
            datetime.now(UTC),
        )
        assert update_status == "UPDATE 1"

        error = RuntimeError("synthetic transport failure") if stale_completion == "error" else None
        finalizer = asyncio.create_task(
            task_outbox._finalize_dispatch(outbox_id, token_a, error=error)
        )

        async def wait_for_finalizer_lock_wait() -> None:
            deadline = asyncio.get_running_loop().time() + 10
            while True:
                session_found, blocked = await observer.fetchrow(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_stat_activity "
                    "WHERE application_name = $1 AND state = 'active' "
                    "AND pid <> pg_backend_pid()"
                    "), EXISTS ("
                    "SELECT 1 FROM pg_catalog.pg_stat_activity "
                    "WHERE application_name = $1 AND state = 'active' "
                    "AND pid <> pg_backend_pid() "
                    "AND cardinality(pg_catalog.pg_blocking_pids(pid)) > 0"
                    ")",
                    database.application_name,
                )
                if blocked:
                    return
                if finalizer is not None and finalizer.done():
                    pytest.fail(
                        "finalizer completed before observing contention "
                        f"(session_found={session_found}, blocked={blocked})"
                    )
                if asyncio.get_running_loop().time() >= deadline:
                    pytest.fail(
                        "timed out waiting for finalizer contention "
                        f"(session_found={session_found}, blocked={blocked})"
                    )
                await asyncio.sleep(0.01)

        try:
            await wait_for_finalizer_lock_wait()
        finally:
            # Always release B's lock, including on a bounded observation
            # timeout, so a failed test cannot strand its own connection.
            await contender.execute("COMMIT")

        assert await finalizer is False
        async with database.sessions() as session:
            row = await session.scalar(select(TaskOutbox).where(TaskOutbox.id == outbox_id))
            assert row is not None
            assert row.lease_token == token_b
            assert row.lease_until == lease_until_b
            assert row.dispatched_at is None
            assert row.attempt_count == 0
            assert row.last_error is None
    finally:
        if finalizer is not None and not finalizer.done():
            finalizer.cancel()
            try:
                await finalizer
            except asyncio.CancelledError:
                pass
        await observer.close()
        await contender.close()
