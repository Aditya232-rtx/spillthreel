"""Raw pgvector publication fence against isolated real PostgreSQL schemas.

Requires an explicit TEST_DATABASE_URL. No application .env fallback,
migrations, public-schema tables, or provider calls are used.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import engine as db_engine
from app.db.models import Item, Profile, TaskOutbox
from app.services.memory.pgvector_store import _lease_current_raw
from app.tasks.worker_claims import WorkerIdentity, WorkerLease, claim_worker

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _async_url(url: str) -> str:
    return make_url(url).set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


@pytest_asyncio.fixture
async def fence_db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[str, str]]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; isolated pgvector lease test not run")

    parsed = make_url(TEST_DATABASE_URL)
    dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    schema = f"pgv_lease_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(dsn, timeout=10, statement_cache_size=0)
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
    finally:
        await admin.close()

    engine = create_async_engine(
        _async_url(TEST_DATABASE_URL),
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {"search_path": f'"{schema}",pg_catalog'},
        },
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_engine, "_session_factory", sessions)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Profile.__table__.create)
            await conn.run_sync(Item.__table__.create)
            await conn.run_sync(TaskOutbox.__table__.create)
        yield schema, dsn
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(dsn, timeout=10, statement_cache_size=0)
        try:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await admin.close()


async def _seed_worker() -> tuple[WorkerIdentity, str, str]:
    from app.db.engine import session_scope

    user_id = str(uuid.uuid4())
    item_id = f"lease-fence-{uuid.uuid4().hex}"
    outbox_id = f"lease-fence-job-{uuid.uuid4().hex}"
    async with session_scope() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            Item(
                id=item_id,
                user_id=user_id,
                source_url="https://example.test/media",
                source_url_norm=f"https://example.test/{uuid.uuid4().hex}",
                platform="instagram",
                state="indexing",
                saved_at=datetime.now(UTC),
                source_type="share_extension",
            )
        )
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="ingest",
                target_type="item",
                target_id=item_id,
                payload={"item_id": item_id},
                generation=1,
            )
        )
        await session.commit()
    return WorkerIdentity(outbox_id, "ingest", "item", item_id, 1), user_id, item_id


async def _raw_connection(schema: str, dsn: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        dsn,
        timeout=10,
        statement_cache_size=0,
        server_settings={"search_path": f'"{schema}",pg_catalog'},
    )


@pytest.mark.asyncio
async def test_publication_fence_accepts_live_claim_on_database_clock(
    fence_db: tuple[str, str],
) -> None:
    schema, dsn = fence_db
    identity, owner_id, item_id = await _seed_worker()
    claim = await claim_worker(identity)
    assert claim.status == "claimed" and claim.lease is not None

    conn = await _raw_connection(schema, dsn)
    try:
        async with conn.transaction():
            valid, failed = await _lease_current_raw(conn, claim.lease, owner_id, item_id)
            assert valid is True
            assert failed == ()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_publication_fence_rejects_a_replaced_lease_after_takeover(
    fence_db: tuple[str, str],
) -> None:
    schema, dsn = fence_db
    identity, owner_id, item_id = await _seed_worker()
    original = await claim_worker(identity)
    assert original.status == "claimed" and original.lease is not None

    conn = await _raw_connection(schema, dsn)
    try:
        await conn.execute(
            "UPDATE items SET worker_lease_until=clock_timestamp()-interval '1 second' "
            "WHERE id=$1",
            item_id,
        )
    finally:
        await conn.close()

    replacement = await claim_worker(identity)
    assert replacement.status == "claimed" and replacement.lease is not None
    assert replacement.lease.token != original.lease.token

    conn = await _raw_connection(schema, dsn)
    try:
        async with conn.transaction():
            valid, failed = await _lease_current_raw(conn, original.lease, owner_id, item_id)
            assert valid is False
            assert failed == ("lease_token_mismatch",)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_publication_fence_rejects_deleted_owner(
    fence_db: tuple[str, str],
) -> None:
    schema, dsn = fence_db
    identity, owner_id, item_id = await _seed_worker()
    claim = await claim_worker(identity)
    assert claim.status == "claimed" and claim.lease is not None

    conn = await _raw_connection(schema, dsn)
    try:
        await conn.execute(
            "UPDATE profiles SET deleted_at=clock_timestamp() WHERE id=$1", owner_id
        )
        async with conn.transaction():
            valid, failed = await _lease_current_raw(conn, claim.lease, owner_id, item_id)
            assert valid is False
            assert failed == ("profile_deleted",)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_publication_row_locks_serialize_with_concurrent_takeover(
    fence_db: tuple[str, str],
) -> None:
    schema, dsn = fence_db
    identity, owner_id, item_id = await _seed_worker()
    claim = await claim_worker(identity)
    assert claim.status == "claimed" and claim.lease is not None

    publisher = await _raw_connection(schema, dsn)
    taker = await _raw_connection(schema, dsn)
    tx = publisher.transaction()
    takeover: asyncio.Task[str] | None = None
    committed = False
    try:
        await tx.start()
        valid, failed = await _lease_current_raw(publisher, claim.lease, owner_id, item_id)
        assert valid is True and failed == ()

        taker_pid = await taker.fetchval("SELECT pg_backend_pid()")

        async def replace_lease() -> str:
            return await taker.execute(
                "UPDATE items SET worker_lease_token='replacement', "
                "worker_lease_until=clock_timestamp()+interval '15 minutes' WHERE id=$1",
                item_id,
            )

        takeover = asyncio.create_task(replace_lease())
        blocked = False
        for _ in range(100):
            blocked = bool(
                await publisher.fetchval(
                    "SELECT cardinality(pg_blocking_pids($1)) > 0", taker_pid
                )
            )
            if blocked:
                break
            await asyncio.sleep(0.01)
        assert blocked, "concurrent takeover should wait for publication's item lock"

        await tx.commit()
        committed = True
        assert await asyncio.wait_for(takeover, timeout=2) == "UPDATE 1"

        async with publisher.transaction():
            valid, failed = await _lease_current_raw(
                publisher, claim.lease, owner_id, item_id
            )
            assert valid is False
            assert failed == ("lease_token_mismatch",)
    finally:
        if not committed:
            await tx.rollback()
        if takeover is not None and not takeover.done():
            takeover.cancel()
            await asyncio.gather(takeover, return_exceptions=True)
        await publisher.close()
        await taker.close()
