"""Real PostgreSQL lease interleavings in a unique non-public schema.

Requires an explicitly supplied TEST_DATABASE_URL. Never runs migrations and
never points at DATABASE_URL or an application `.env` fallback.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db import engine as db_engine
from app.db.models import (
    Category,
    Collection,
    CollectionItem,
    Import,
    ImportCollection,
    ImportEntry,
    ImportEntryOccurrence,
    ImportPreview,
    ImportPreviewCandidate,
    ImportStaging,
    Item,
    Profile,
    TaskOutbox,
    UserDeletionJob,
)
from app.services.imports.entries import save_import_progress, sync_import_entries_for_item
from app.services.imports.recovery import (
    ImportCommitReconciliation,
    reconcile_import_commit,
)
from app.tasks.worker_claims import (
    TargetExecution,
    WorkerIdentity,
    claim_worker,
    claim_target_intent,
    complete_target_intent,
    finalize_worker,
    finalize_worker_in_session,
    target_intent_is_current_in_session,
)
from app.tasks.outbox import _finalize_dispatch

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def _url(url: str) -> str:
    parsed = make_url(url)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL")
    return parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)


@dataclass(frozen=True)
class WorkerDB:
    schema: str
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def worker_db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WorkerDB]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; isolated worker lease test not run")
    from sqlalchemy.ext.asyncio import create_async_engine as create_engine
    import asyncpg

    parsed = make_url(TEST_DATABASE_URL)
    admin_dsn = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    schema = f"r03_worker_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(admin_dsn, timeout=10)
    created = False
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        created = True
        await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
    except Exception as exc:
        if created:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        pytest.fail(f"isolated schema setup failed ({type(exc).__name__})")
    finally:
        await admin.close()

    application_name = f"worker-test-{uuid.uuid4().hex[:10]}"
    engine = create_engine(
        _url(TEST_DATABASE_URL),
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "server_settings": {
                "search_path": f'"{schema}",pg_catalog',
                "application_name": application_name,
            }
        },
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_engine, "_session_factory", sessions)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Profile.__table__.create)
            # Imports now has a composite owner-scoped FK to previews; create
            # both preview tables in the same isolated schema before imports.
            await conn.run_sync(ImportPreview.__table__.create)
            await conn.run_sync(ImportPreviewCandidate.__table__.create)
            await conn.run_sync(Import.__table__.create)
            await conn.run_sync(ImportStaging.__table__.create)
            await conn.run_sync(Category.__table__.create)
            await conn.run_sync(Item.__table__.create)
            await conn.run_sync(Collection.__table__.create)
            await conn.run_sync(CollectionItem.__table__.create)
            await conn.run_sync(ImportEntry.__table__.create)
            await conn.run_sync(ImportEntryOccurrence.__table__.create)
            await conn.run_sync(ImportCollection.__table__.create)
            await conn.run_sync(TaskOutbox.__table__.create)
            await conn.run_sync(UserDeletionJob.__table__.create)
            proof = await conn.exec_driver_sql(
                "SELECT current_schema(), current_schemas(false), "
                "(SELECT n.nspname FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE c.oid=to_regclass('profiles'))"
            )
            current, visible, profile_schema = proof.one()
            assert current == schema and visible[0] == schema and "public" not in visible
            assert profile_schema == schema
        yield WorkerDB(schema, engine, sessions)
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(admin_dsn, timeout=10)
        try:
            await admin.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await admin.close()


@pytest.mark.asyncio
async def test_duplicate_claims_serialize_and_completed_redelivery_is_noop(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    item_id = f"worker-item-{uuid.uuid4().hex}"
    outbox_id = f"worker-job-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            Item(
                id=item_id,
                user_id=user_id,
                source_url="https://example.test/media",
                source_url_norm=f"https://example.test/{uuid.uuid4().hex}",
                platform="youtube",
                state="queued",
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

    identity = WorkerIdentity(outbox_id, "ingest", "item", item_id, 1)
    first, second = await asyncio.gather(claim_worker(identity), claim_worker(identity))
    outcomes = {first.status, second.status}
    assert outcomes == {"claimed", "busy"}
    lease = first.lease or second.lease
    assert lease is not None
    async with worker_db.sessions() as session:
        item = await session.get(Item, item_id)
        assert item is not None
        item.state = "fully_indexed"
        assert await finalize_worker_in_session(session, lease, status="completed") is True
        await session.commit()
    async with worker_db.sessions() as session:
        item = await session.get(Item, item_id)
        outbox = await session.get(TaskOutbox, outbox_id)
        assert item is not None and item.state == "fully_indexed"
        assert item.worker_lease_token is None
        assert outbox is not None and outbox.execution_status == "completed"
    redelivery = await claim_worker(identity)
    assert redelivery.status == "completed"
    assert redelivery.lease is None


@pytest.mark.asyncio
async def test_owner_capacity_serializes_distinct_item_claims_and_retries_after_release(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    item_ids = [f"capacity-item-{uuid.uuid4().hex}" for _ in range(2)]
    outbox_ids = [f"capacity-job-{uuid.uuid4().hex}" for _ in range(2)]
    dispatch_tokens = [f"dispatcher-{uuid.uuid4().hex}" for _ in range(2)]
    dispatched_at = datetime.now(UTC)
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        for item_id, outbox_id, dispatch_token in zip(
            item_ids, outbox_ids, dispatch_tokens, strict=True
        ):
            session.add(
                Item(
                    id=item_id,
                    user_id=user_id,
                    source_url=f"https://example.test/{item_id}",
                    source_url_norm=f"https://example.test/{uuid.uuid4().hex}",
                    platform="youtube",
                    state="queued",
                    saved_at=datetime.now(UTC),
                    source_type="share_extension",
                )
            )
            outbox = TaskOutbox(
                id=outbox_id,
                task_type="ingest",
                target_type="item",
                target_id=item_id,
                payload={"item_id": item_id},
                generation=1,
                dispatched_at=dispatched_at,
                lease_token=dispatch_token,
                lease_until=dispatched_at + timedelta(minutes=1),
            )
            outbox.attempt_count = 1
            session.add(outbox)
        await session.commit()

    identities = [
        WorkerIdentity(outbox_id, "ingest", "item", item_id, 1)
        for item_id, outbox_id in zip(item_ids, outbox_ids, strict=True)
    ]
    first, second = await asyncio.gather(
        *(claim_worker(identity, max_active_items=1) for identity in identities)
    )
    assert {first.status, second.status} == {"claimed", "capacity"}
    lease = first.lease or second.lease
    assert lease is not None
    capacity_identity = identities[0] if first.status == "capacity" else identities[1]
    capacity_outbox_id = capacity_identity.outbox_id
    capacity_dispatch_token = dispatch_tokens[
        outbox_ids.index(capacity_outbox_id)
    ]

    async with worker_db.sessions() as session:
        assert await session.scalar(
            select(func.count(Item.id)).where(
                Item.user_id == user_id,
                Item.worker_lease_token.is_not(None),
                Item.worker_lease_until > datetime.now(UTC),
            )
        ) == 1
        deferred = await session.get(TaskOutbox, capacity_outbox_id)
        assert deferred is not None
        assert deferred.dispatched_at is None
        assert deferred.available_at > datetime.now(UTC)
        assert deferred.lease_token is None and deferred.lease_until is None

    # The first dispatch call may still be finalizing when a fast worker
    # reaches its capacity branch. Its old token must not erase the durable
    # delayed redispatch state.
    assert await _finalize_dispatch(
        capacity_outbox_id, capacity_dispatch_token
    ) is False
    async with worker_db.sessions() as session:
        deferred = await session.get(TaskOutbox, capacity_outbox_id)
        assert deferred is not None and deferred.dispatched_at is None
        assert deferred.available_at > datetime.now(UTC)

    assert await finalize_worker(lease, status="pending", error_code="test_release") is True
    retried = await claim_worker(capacity_identity, max_active_items=1)
    assert retried.status == "claimed"
    assert retried.lease is not None


@pytest.mark.asyncio
async def test_ingest_and_enhance_share_one_item_lease(worker_db: WorkerDB) -> None:
    user_id = str(uuid.uuid4())
    item_id = f"worker-item-{uuid.uuid4().hex}"
    ingest_id = f"worker-ingest-{uuid.uuid4().hex}"
    enhance_id = f"worker-enhance-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            Item(
                id=item_id,
                user_id=user_id,
                source_url="https://example.test/media",
                source_url_norm=f"https://example.test/{uuid.uuid4().hex}",
                platform="youtube",
                state="queued",
                saved_at=datetime.now(UTC),
                source_type="share_extension",
            )
        )
        session.add_all(
            [
                TaskOutbox(
                    id=ingest_id,
                    task_type="ingest",
                    target_type="item",
                    target_id=item_id,
                    payload={"item_id": item_id},
                    generation=1,
                ),
                TaskOutbox(
                    id=enhance_id,
                    task_type="enhance",
                    target_type="item",
                    target_id=item_id,
                    payload={"item_id": item_id},
                    generation=1,
                ),
            ]
        )
        await session.commit()
    ingest_identity = WorkerIdentity(ingest_id, "ingest", "item", item_id, 1)
    enhance_identity = WorkerIdentity(enhance_id, "enhance", "item", item_id, 1)
    a, b = await asyncio.gather(claim_worker(ingest_identity), claim_worker(enhance_identity))
    assert {a.status, b.status} == {"claimed", "busy"}


@pytest.mark.asyncio
async def test_soft_deleted_profile_fences_claim_and_late_completion(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    item_id = f"worker-item-{uuid.uuid4().hex}"
    outbox_id = f"worker-job-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            Item(
                id=item_id,
                user_id=user_id,
                source_url="https://example.test/media",
                source_url_norm=f"https://example.test/{uuid.uuid4().hex}",
                platform="youtube",
                state="queued",
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
    identity = WorkerIdentity(outbox_id, "ingest", "item", item_id, 1)
    claimed = await claim_worker(identity)
    assert claimed.status == "claimed" and claimed.lease is not None

    async with worker_db.sessions() as session:
        profile = await session.get(Profile, user_id)
        assert profile is not None
        profile.deleted_at = datetime.now(UTC)
        await session.commit()

    # A provider may finish after deletion begins, but it cannot publish.
    assert await finalize_worker(claimed.lease, status="completed") is False
    redelivery = await claim_worker(identity)
    assert redelivery.status == "stale"


@pytest.mark.asyncio
async def test_import_execution_fence_rejects_reclaimed_generation_and_deleted_owner(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    import_id = f"worker-import-{uuid.uuid4().hex}"
    outbox_id = f"worker-import-job-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(Import(id=import_id, user_id=user_id, state="processing"))
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="import",
                target_type="import",
                target_id=import_id,
                payload={"import_id": import_id, "user_id": user_id},
                generation=1,
            )
        )
        await session.commit()

    identity = WorkerIdentity(outbox_id, "import", "import", import_id, 1)
    claim = await claim_target_intent(identity, owner_id=user_id)
    assert claim.status == "claimed" and claim.token is not None
    execution = TargetExecution(identity, claim.token, user_id)

    # A new generation represents a durable takeover after retry. The old
    # token may no longer authorize writes, even though its row still exists.
    async with worker_db.sessions() as session:
        session.add(
            TaskOutbox(
                id=f"worker-import-job-{uuid.uuid4().hex}",
                task_type="import",
                target_type="import",
                target_id=import_id,
                payload={"import_id": import_id, "user_id": user_id},
                generation=2,
            )
        )
        await session.commit()
    async with worker_db.sessions() as session:
        assert await target_intent_is_current_in_session(session, execution) is False

    # Exercise deletion separately so the generation check above cannot mask it.
    deleted_user_id = str(uuid.uuid4())
    deleted_import_id = f"worker-import-{uuid.uuid4().hex}"
    deleted_outbox_id = f"worker-import-job-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=deleted_user_id))
        await session.flush()
        session.add(Import(id=deleted_import_id, user_id=deleted_user_id, state="processing"))
        session.add(
            TaskOutbox(
                id=deleted_outbox_id,
                task_type="import",
                target_type="import",
                target_id=deleted_import_id,
                payload={"import_id": deleted_import_id, "user_id": deleted_user_id},
                generation=1,
            )
        )
        await session.commit()
    deleted_identity = WorkerIdentity(
        deleted_outbox_id, "import", "import", deleted_import_id, 1
    )
    deleted_claim = await claim_target_intent(deleted_identity, owner_id=deleted_user_id)
    assert deleted_claim.status == "claimed" and deleted_claim.token is not None
    deleted_execution = TargetExecution(deleted_identity, deleted_claim.token, deleted_user_id)
    async with worker_db.sessions() as session:
        profile = await session.get(Profile, deleted_user_id)
        assert profile is not None
        profile.deleted_at = datetime.now(UTC)
        await session.commit()
    async with worker_db.sessions() as session:
        assert await target_intent_is_current_in_session(session, deleted_execution) is False


@pytest.mark.asyncio
async def test_import_commit_reconciliation_uses_durable_rows_in_fresh_transaction(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    import_id = f"worker-import-{uuid.uuid4().hex}"
    outbox_id = f"worker-import-job-{uuid.uuid4().hex}"
    storage_key = f"{user_id}/{import_id}.zip"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(Import(id=import_id, user_id=user_id, state="processing"))
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="import",
                target_type="import",
                target_id=import_id,
                payload={
                    "import_id": import_id,
                    "user_id": user_id,
                    "storage_key": storage_key,
                },
                generation=1,
            )
        )
        await session.commit()

    committed = await reconcile_import_commit(
        worker_db.engine,
        import_id=import_id,
        user_id=user_id,
        storage_key=storage_key,
        outbox_id=outbox_id,
    )
    missing_import_id = f"missing-{uuid.uuid4().hex}"
    absent = await reconcile_import_commit(
        worker_db.engine,
        import_id=missing_import_id,
        user_id=user_id,
        storage_key=f"{user_id}/{missing_import_id}.zip",
        outbox_id=f"missing-job-{uuid.uuid4().hex}",
    )
    assert committed is ImportCommitReconciliation.COMMITTED
    assert absent is ImportCommitReconciliation.PROVEN_UNCOMMITTED


@pytest.mark.asyncio
async def test_account_delete_winning_commit_reconciliation_preserves_unknown_source(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    import_id = f"worker-import-{uuid.uuid4().hex}"
    outbox_id = f"worker-import-job-{uuid.uuid4().hex}"
    storage_key = f"{user_id}/{import_id}.zip"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(Import(id=import_id, user_id=user_id, state="processing"))
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="import",
                target_type="import",
                target_id=import_id,
                payload={
                    "import_id": import_id,
                    "user_id": user_id,
                    "storage_key": storage_key,
                },
                generation=1,
            )
        )
        await session.commit()

    query_started = asyncio.Event()

    def observe_profile_lock(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
        if "FROM profiles" in statement and "FOR UPDATE" in statement:
            query_started.set()

    async with worker_db.sessions() as deleting:
        async with deleting.begin():
            delete_pid = await deleting.scalar(text("SELECT pg_backend_pid()"))
            profile = (
                await deleting.execute(
                    select(Profile).where(Profile.id == user_id).with_for_update()
                )
            ).scalar_one()
            await deleting.delete(profile)
            await deleting.flush()
            event.listen(worker_db.engine.sync_engine, "before_cursor_execute", observe_profile_lock)
            try:
                reconcile_task = asyncio.create_task(
                    reconcile_import_commit(
                        worker_db.engine,
                        import_id=import_id,
                        user_id=user_id,
                        storage_key=storage_key,
                        outbox_id=outbox_id,
                    )
                )
                await asyncio.wait_for(query_started.wait(), timeout=2)
                # The event fires just before driver execution. Confirm the
                # reconciler is actually waiting on the deletion-held row lock
                # before allowing the deleting transaction to commit.
                deadline = asyncio.get_running_loop().time() + 2
                while True:
                    async with worker_db.engine.connect() as monitor:
                        waiting = await monitor.scalar(
                            text(
                                "SELECT 1 FROM pg_stat_activity "
                                "WHERE application_name LIKE 'worker-test-%' "
                                "AND state = 'active' AND wait_event_type = 'Lock' "
                                "AND query ILIKE '%from profiles%' "
                                "AND :delete_pid = ANY(pg_blocking_pids(pid)) LIMIT 1"
                            ),
                            {"delete_pid": delete_pid},
                        )
                    if waiting:
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise AssertionError("reconciler did not wait on deletion's profile lock")
                    await asyncio.sleep(0.01)
            finally:
                event.remove(
                    worker_db.engine.sync_engine, "before_cursor_execute", observe_profile_lock
                )

    outcome = await asyncio.wait_for(reconcile_task, timeout=2)
    assert outcome is ImportCommitReconciliation.UNKNOWN


@pytest.mark.asyncio
async def test_ingest_completion_updates_import_progress_atomically(worker_db: WorkerDB) -> None:
    user_id = str(uuid.uuid4())
    item_id = f"worker-item-{uuid.uuid4().hex}"
    import_id = f"worker-import-{uuid.uuid4().hex}"
    replay_import_id = f"worker-import-{uuid.uuid4().hex}"
    reservation_key = "a" * 64
    occurrence_key = "b" * 64
    replay_occurrence_key = "e" * 64
    now = datetime.now(UTC)
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(Import(id=import_id, user_id=user_id, state="processing"))
        session.add(Import(id=replay_import_id, user_id=user_id, state="indexing"))
        session.add(
            Item(
                id=item_id,
                user_id=user_id,
                source_url="https://example.test/imported",
                source_url_norm="https://example.test/imported",
                platform="youtube",
                state="queued",
                saved_at=now,
                source_type="instagram_import",
            )
        )
        await session.flush()
        session.add(
            ImportEntry(
                import_id=import_id,
                reservation_key=reservation_key,
                owner_id=user_id,
                source_url_norm="https://example.test/imported",
                source_bucket="other",
                work_queued_at=now,
                item_id=item_id,
                state="queued",
            )
        )
        session.add(
            ImportEntryOccurrence(
                import_id=import_id,
                occurrence_key=occurrence_key,
                owner_id=user_id,
                source_position=0,
                source_kind="media",
                outcome="admitted",
                entry_reservation_key=reservation_key,
                source_url_norm="https://example.test/imported",
            )
        )
        session.add(
            ImportEntry(
                import_id=replay_import_id,
                reservation_key=reservation_key,
                owner_id=user_id,
                source_url_norm="https://example.test/imported",
                source_bucket="other",
                state="retryable_failed",
                last_error_code="reserved_item_pending",
            )
        )
        session.add(
            ImportEntryOccurrence(
                import_id=replay_import_id,
                occurrence_key=replay_occurrence_key,
                owner_id=user_id,
                source_position=0,
                source_kind="media",
                outcome="already_reserved",
                entry_reservation_key=reservation_key,
                source_url_norm="https://example.test/imported",
            )
        )
        await session.commit()

    async with worker_db.sessions() as session:
        item = await session.get(Item, item_id)
        assert item is not None
        item.state = "fully_indexed"
        await sync_import_entries_for_item(
            session, item_id=item_id, owner_id=user_id, item_state="fully_indexed"
        )
        await session.commit()

    async with worker_db.sessions() as session:
        row = await session.get(Import, import_id)
        entry = await session.get(ImportEntry, (import_id, reservation_key))
        replay_row = await session.get(Import, replay_import_id)
        replay_entry = await session.get(ImportEntry, (replay_import_id, reservation_key))
        assert row is not None and entry is not None and replay_row is not None and replay_entry is not None
        assert entry.state == "completed"
        assert replay_entry.item_id == item_id
        assert replay_entry.state == "completed"
        assert replay_entry.work_queued_at is None
        assert replay_row.state == "complete"
        assert replay_row.full_queued == 0
        assert replay_row.full_done == 1
        assert row.full_queued == 1
        assert row.full_done == 1
        assert row.failed == 0
        assert row.state == "processing"
        await save_import_progress(
            session,
            row,
            total_parsed=1,
            parser_schema_hash="schema-v1",
            finalize_manifest=True,
        )
        await session.flush()
        assert row.state == "complete"
        assert row.finished_at is not None

        # Duplicate terminal delivery leaves derived totals unchanged.
        await sync_import_entries_for_item(
            session, item_id=item_id, owner_id=user_id, item_state="fully_indexed"
        )
        await session.commit()
        row = await session.get(Import, import_id)
        assert row is not None and row.full_done == 1 and row.full_queued == 1


@pytest.mark.asyncio
async def test_completed_replay_manifest_finalizes_after_all_entries_are_recorded(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    import_id = f"worker-import-{uuid.uuid4().hex}"
    reservation_key = "c" * 64
    occurrence_key = "d" * 64
    now = datetime.now(UTC)
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        row = Import(id=import_id, user_id=user_id, state="processing")
        session.add(row)
        await session.flush()
        session.add(
            ImportEntry(
                import_id=import_id,
                reservation_key=reservation_key,
                owner_id=user_id,
                source_url_norm="https://example.test/already-complete",
                source_bucket="other",
                work_queued_at=None,
                item_id=None,
                state="completed",
            )
        )
        session.add(
            ImportEntryOccurrence(
                import_id=import_id,
                occurrence_key=occurrence_key,
                owner_id=user_id,
                source_position=0,
                source_kind="media",
                outcome="already_reserved",
                entry_reservation_key=reservation_key,
                source_url_norm="https://example.test/already-complete",
            )
        )
        await session.flush()

        # Worker completion before the importer closes its manifest must not
        # mark it terminal. The importer’s final fenced write owns that point.
        await sync_import_entries_for_item(
            session,
            item_id="no-linked-item",
            owner_id=user_id,
            item_state="fully_indexed",
        )
        await save_import_progress(
            session, row, total_parsed=1, parser_schema_hash="schema-v1"
        )
        assert row.state == "processing"

        await save_import_progress(
            session,
            row,
            total_parsed=1,
            parser_schema_hash="schema-v1",
            finalize_manifest=True,
        )
        await session.commit()
        assert row.state == "complete"
        assert row.finished_at is not None
        assert row.full_queued == 0
        assert row.full_done == 1


@pytest.mark.asyncio
async def test_expired_item_lease_is_reclaimed_with_new_token(worker_db: WorkerDB) -> None:
    user_id = str(uuid.uuid4())
    item_id = f"worker-item-{uuid.uuid4().hex}"
    outbox_id = f"worker-job-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            Item(
                id=item_id,
                user_id=user_id,
                source_url="https://example.test/media",
                source_url_norm=f"https://example.test/{uuid.uuid4().hex}",
                platform="youtube",
                state="queued",
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

    identity = WorkerIdentity(outbox_id, "ingest", "item", item_id, 1)
    first = await claim_worker(identity)
    assert first.status == "claimed" and first.lease is not None
    async with worker_db.sessions() as session:
        item = await session.get(Item, item_id)
        assert item is not None
        item.worker_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    reclaimed = await claim_worker(identity)
    assert reclaimed.status == "claimed" and reclaimed.lease is not None
    assert reclaimed.lease.token != first.lease.token


@pytest.mark.asyncio
async def test_older_generation_is_stale_after_newer_intent(worker_db: WorkerDB) -> None:
    user_id = str(uuid.uuid4())
    item_id = f"worker-item-{uuid.uuid4().hex}"
    old_id = f"worker-old-{uuid.uuid4().hex}"
    new_id = f"worker-new-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            Item(
                id=item_id,
                user_id=user_id,
                source_url="https://example.test/media",
                source_url_norm=f"https://example.test/{uuid.uuid4().hex}",
                platform="youtube",
                state="queued",
                saved_at=datetime.now(UTC),
                source_type="share_extension",
            )
        )
        session.add_all(
            [
                TaskOutbox(
                    id=old_id,
                    task_type="ingest",
                    target_type="item",
                    target_id=item_id,
                    payload={"item_id": item_id},
                    generation=1,
                ),
                TaskOutbox(
                    id=new_id,
                    task_type="ingest",
                    target_type="item",
                    target_id=item_id,
                    payload={"item_id": item_id},
                    generation=2,
                ),
            ]
        )
        await session.commit()

    stale = await claim_worker(WorkerIdentity(old_id, "ingest", "item", item_id, 1))
    assert stale.status == "stale"
    current = await claim_worker(WorkerIdentity(new_id, "ingest", "item", item_id, 2))
    assert current.status == "claimed" and current.lease is not None


@pytest.mark.asyncio
async def test_delete_intent_requires_soft_delete_and_completes_after_purge(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    outbox_id = f"worker-delete-{uuid.uuid4().hex}"
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="delete_user",
                target_type="user",
                target_id=user_id,
                payload={"user_id": user_id},
                generation=1,
            )
        )
        await session.commit()

    identity = WorkerIdentity(outbox_id, "delete_user", "user", user_id, 1)
    forged = await claim_target_intent(identity, owner_id=user_id)
    assert forged.status == "invalid"

    async with worker_db.sessions() as session:
        profile = await session.get(Profile, user_id)
        assert profile is not None
        profile.deleted_at = datetime.now(UTC)
        await session.commit()
    valid = await claim_target_intent(identity, owner_id=user_id)
    assert valid.status == "claimed" and valid.token is not None

    # Profile may be gone after a partial purge; the exact pending intent
    # remains sufficient to safely retry and mark completion.
    async with worker_db.sessions() as session:
        await session.delete(await session.get(Profile, user_id))
        await session.commit()
    assert await complete_target_intent(identity, valid.token, owner_id=user_id) is True
    assert (await claim_target_intent(identity, owner_id=user_id)).status == "completed"


@pytest.mark.asyncio
async def test_target_claims_are_exclusive_and_old_owner_cannot_complete_after_reclaim(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    outbox_id = f"worker-import-{uuid.uuid4().hex}"
    identity = WorkerIdentity(outbox_id, "import", "import", f"imp-{uuid.uuid4().hex}", 1)
    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id))
        await session.flush()
        session.add(
            Import(
                id=identity.target_id,
                user_id=user_id,
                state="processing",
            )
        )
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="import",
                target_type="import",
                target_id=identity.target_id,
                payload={"import_id": identity.target_id, "user_id": user_id, "storage_key": "owner/archive.zip"},
                generation=1,
            )
        )
        await session.commit()

    expected = {"user_id": user_id, "storage_key": "owner/archive.zip"}
    first, duplicate = await asyncio.gather(
        claim_target_intent(identity, owner_id=user_id, payload_fields=expected),
        claim_target_intent(identity, owner_id=user_id, payload_fields=expected),
    )
    assert {first.status, duplicate.status} == {"claimed", "busy"}
    first_owner = first if first.status == "claimed" else duplicate
    assert first_owner.token is not None

    async with worker_db.sessions() as session:
        outbox = await session.get(TaskOutbox, outbox_id)
        assert outbox is not None
        outbox.execution_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    reclaimed = await claim_target_intent(identity, owner_id=user_id, payload_fields=expected)
    assert reclaimed.status == "claimed" and reclaimed.token is not None
    assert reclaimed.token != first_owner.token
    assert await complete_target_intent(identity, first_owner.token, owner_id=user_id) is False
    assert await complete_target_intent(identity, reclaimed.token, owner_id=user_id) is True

    # Immutable request fields must match the durable payload exactly.
    mismatch = WorkerIdentity(
        f"worker-bad-{uuid.uuid4().hex}", "import", "import", identity.target_id, 2
    )
    async with worker_db.sessions() as session:
        session.add(
            TaskOutbox(
                id=mismatch.outbox_id,
                task_type="import",
                target_type="import",
                target_id=mismatch.target_id,
                payload={"import_id": mismatch.target_id, "user_id": user_id, "storage_key": "other/archive.zip"},
                generation=2,
            )
        )
        await session.commit()
    rejected = await claim_target_intent(
        mismatch,
        owner_id=user_id,
        payload_fields={"user_id": user_id, "storage_key": "owner/archive.zip"},
    )
    assert rejected.status == "invalid"


@pytest.mark.asyncio
async def test_import_summary_claim_is_owner_scoped_and_exclusive(
    worker_db: WorkerDB,
) -> None:
    user_id = str(uuid.uuid4())
    other_user_id = str(uuid.uuid4())
    import_id = f"summary-import-{uuid.uuid4().hex}"
    outbox_id = f"summary-job-{uuid.uuid4().hex}"
    identity = WorkerIdentity(outbox_id, "import_summary", "import", import_id, 1)
    payload = {"user_id": user_id, "import_id": import_id}
    async with worker_db.sessions() as session:
        session.add_all([Profile(id=user_id), Profile(id=other_user_id)])
        await session.flush()
        session.add(Import(id=import_id, user_id=user_id, state="complete"))
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="import_summary",
                target_type="import",
                target_id=import_id,
                payload={**payload, "generation": 1},
                generation=1,
            )
        )
        await session.commit()

    forged = await claim_target_intent(
        identity, owner_id=other_user_id, payload_fields=payload
    )
    assert forged.status == "invalid"

    first, duplicate = await asyncio.gather(
        claim_target_intent(identity, owner_id=user_id, payload_fields=payload),
        claim_target_intent(identity, owner_id=user_id, payload_fields=payload),
    )
    assert {first.status, duplicate.status} == {"claimed", "busy"}
    lease = first if first.status == "claimed" else duplicate
    assert lease.token is not None
    assert await complete_target_intent(identity, lease.token, owner_id=user_id) is True
    assert (await claim_target_intent(identity, owner_id=user_id, payload_fields=payload)).status == "completed"


@pytest.mark.asyncio
async def test_deletion_checkpoint_fences_worker_that_returns_after_takeover(
    worker_db: WorkerDB,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real PostgreSQL lease predicates prevent a stale post-Storage advance."""
    from types import SimpleNamespace

    import app.services.media.supabase_storage as storage
    import app.services.users.deletion as deletion

    user_id = str(uuid.uuid4())
    outbox_id = f"delete-job-{uuid.uuid4().hex}"
    now = datetime.now(UTC)
    identity = WorkerIdentity(outbox_id, "delete_user", "user", user_id, 1)
    stale_execution = TargetExecution(identity, "lease-old", user_id)
    current_execution = TargetExecution(identity, "lease-new", user_id)

    async with worker_db.sessions() as session:
        session.add(Profile(id=user_id, deleted_at=now))
        session.add(
            TaskOutbox(
                id=outbox_id,
                task_type="delete_user",
                target_type="user",
                target_id=user_id,
                payload={"user_id": user_id, "generation": 1},
                generation=1,
                execution_status="pending",
                execution_lease_token="lease-old",
                execution_lease_until=now + timedelta(minutes=5),
            )
        )
        session.add(UserDeletionJob(user_id=user_id, next_step="storage_media"))
        await session.commit()

    takeover_once = False

    async def pause_and_take_over(bucket: str, prefix: str) -> int:
        nonlocal takeover_once
        assert bucket == "media"
        assert prefix == f"{user_id}/"
        assert not takeover_once
        takeover_once = True
        async with worker_db.sessions() as session:
            outbox = await session.get(TaskOutbox, outbox_id)
            assert outbox is not None
            outbox.execution_lease_token = "lease-new"
            outbox.execution_lease_until = datetime.now(UTC) + timedelta(minutes=5)
            await session.commit()

        # The current claimant records this idempotent step before the stale
        # worker's simulated Storage request returns.
        await deletion._advance(user_id, "storage_media", current_execution)
        return 0

    monkeypatch.setattr(storage, "delete_prefix", pause_and_take_over)
    monkeypatch.setattr(
        deletion,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_bucket_media="media",
            supabase_bucket_exports="exports",
        ),
    )

    with pytest.raises(deletion.DeletionExecutionLost):
        await deletion.delete_user_account(user_id, stale_execution)

    async with worker_db.sessions() as session:
        checkpoint = await session.get(UserDeletionJob, user_id)
        assert checkpoint is not None
        assert checkpoint.next_step == "storage_exports"
