"""Local PostgreSQL race between two previews competing for the last quota slot.

The test invokes the real ``commit_import_preview`` route function with separate
SQLAlchemy sessions and isolated tables. It connects only through an explicitly
provided ``TEST_DATABASE_URL`` that resolves to loopback PostgreSQL 17, creates
one random private schema, and drops only that schema. No Alembic migrations,
staging database, storage provider, or external worker are used.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import imports
from app.db.models import (
    BulkImportReservation,
    Import,
    ImportPreview,
    ImportPreviewCandidate,
    ImportStaging,
    Item,
    Profile,
    TaskOutbox,
)
from app.services.imports.admission import reservation_key_for_url


PREVIEW_IDS = ("01K8B3Q6M2V7H9T4W5X1Y0ZABC", "01K8B3Q6M2V7H9T4W5X1Y0ZABD")


def _local_database_urls(value: str) -> tuple[str, str]:
    split_value = urlsplit(value)
    if split_value.query or split_value.fragment:
        raise ValueError("TEST_DATABASE_URL must not contain query or fragment overrides")
    parsed = make_url(value)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL")
    if (parsed.host or "").lower().rstrip(".") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("cross-preview quota test only accepts localhost PostgreSQL")
    return (
        parsed.set(drivername="postgresql").render_as_string(hide_password=False),
        parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


@pytest_asyncio.fixture
async def preview_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if os.environ.get("SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS") != "1":
        pytest.skip("requires SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS=1 for local PostgreSQL")
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL must be explicitly set to local PostgreSQL")
    try:
        admin_url, async_url = _local_database_urls(database_url)
    except ValueError as exc:
        pytest.fail(str(exc))

    import asyncpg

    try:
        admin = await asyncpg.connect(admin_url, timeout=10)
    except Exception as exc:
        pytest.fail(f"local PostgreSQL connection failed ({type(exc).__name__})")

    schema = f"cross_preview_quota_{uuid.uuid4().hex}"
    try:
        server_address, version_number = await admin.fetchrow(
            "SELECT inet_server_addr()::text, current_setting('server_version_num')"
        )
        if not server_address:
            pytest.fail("local PostgreSQL test requires a loopback TCP server address")
        try:
            parsed_address = ipaddress.ip_interface(server_address).ip
        except ValueError:
            pytest.fail("local PostgreSQL test requires a valid server IP address")
        if not parsed_address.is_loopback:
            pytest.fail("refusing a non-loopback PostgreSQL server")
        if not str(version_number).startswith("17"):
            pytest.fail("cross-preview quota test requires PostgreSQL 17")
        await admin.execute(f'CREATE SCHEMA "{schema}" AUTHORIZATION CURRENT_USER')
        await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
    finally:
        await admin.close()

    try:
        engine = create_async_engine(
            async_url,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "server_settings": {"search_path": f'"{schema}",pg_catalog'},
            },
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            for model in (
                Profile,
                Item,
                ImportPreview,
                ImportPreviewCandidate,
                ImportStaging,
                Import,
                BulkImportReservation,
                TaskOutbox,
            ):
                await connection.run_sync(model.__table__.create)
            visible = await connection.scalar(text("SELECT current_schemas(false)"))
            assert visible[0] == schema and "public" not in visible
        yield sessions
    finally:
        if "engine" in locals():
            await engine.dispose()
        cleanup = await asyncpg.connect(admin_url, timeout=10)
        try:
            await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await cleanup.close()


@pytest.mark.asyncio
async def test_different_open_previews_cannot_overreserve_the_last_free_slot(
    preview_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = str(uuid.uuid4())
    candidate_ids = (str(uuid.uuid4()), str(uuid.uuid4()))
    urls = (
        "https://www.instagram.com/reel/CrossPreviewLastSlotA/",
        "https://www.instagram.com/reel/CrossPreviewLastSlotB/",
    )
    now = datetime.now(UTC)
    monkeypatch.setattr(
        imports,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_bucket_exports="exports",
            bulk_import_policy_version="cross-preview-quota-test-v1",
            bulk_import_history_reconciled=True,
            bulk_import_premium_history_mode="carry_forward",
            bulk_import_existing_item_mode="exclude",
            bulk_import_manual_share_mode="exclude",
            bulk_import_saved_audio_mode="exclude",
        ),
    )
    dispatched: list[list[str]] = []

    async def capture_dispatch(outbox_ids: list[str]) -> None:
        dispatched.append(list(outbox_ids))

    monkeypatch.setattr(imports, "dispatch_after_commit", capture_dispatch)

    async with preview_db() as session, session.begin():
        session.add(Profile(id=owner_id))
        await session.flush()
        # 149 Instagram + 100 other reservations leave exactly one free
        # lifetime slot, which is also the final Instagram slot.
        session.add_all(
            [
                BulkImportReservation(
                    id=f"seed-{index}",
                    user_id=owner_id,
                    reservation_key=f"{index:064x}",
                    source_bucket="instagram" if index < 149 else "other",
                    tier="free",
                )
                for index in range(249)
            ]
        )
        for index, preview_id in enumerate(PREVIEW_IDS):
            session.add(
                ImportPreview(
                    id=preview_id,
                    owner_id=owner_id,
                    expires_at=now + timedelta(minutes=50),
                    parser_schema_hash="cross-preview-quota-v1",
                    semantic_fingerprint=("a" if index == 0 else "b") * 64,
                    candidate_count=1,
                )
            )
            session.add(
                ImportPreviewCandidate(
                    preview_id=preview_id,
                    candidate_id=candidate_ids[index],
                    owner_id=owner_id,
                    ordinal=0,
                    reservation_key=reservation_key_for_url(urls[index]),
                    source_bucket="instagram",
                    kind="media",
                    source_url_norm=urls[index].rstrip("/"),
                    collection_names=[],
                )
            )
            session.add(
                ImportStaging(
                    import_id=preview_id,
                    owner_id=owner_id,
                    bucket="exports",
                    storage_key=f"{owner_id}/{preview_id}.zip",
                    lease_until=now + timedelta(hours=1),
                )
            )

    start = asyncio.Event()
    ready = 0
    ready_lock = asyncio.Lock()

    async def commit_one(index: int):
        nonlocal ready
        async with ready_lock:
            ready += 1
            if ready == 2:
                start.set()
        await start.wait()
        async with preview_db() as session:
            try:
                result = await imports.commit_import_preview(
                    PREVIEW_IDS[index],
                    imports.ImportPreviewCommitRequest(candidate_ids=[candidate_ids[index]]),
                    SimpleNamespace(id=owner_id),
                    session,
                )
                return ("admitted", result.import_id)
            except HTTPException as exc:
                return ("rejected", exc.status_code, exc.detail)

    outcomes = await asyncio.gather(commit_one(0), commit_one(1))
    admitted = [outcome for outcome in outcomes if outcome[0] == "admitted"]
    rejected = [outcome for outcome in outcomes if outcome[0] == "rejected"]
    assert len(admitted) == 1
    assert len(rejected) == 1
    assert rejected[0][1:] == (409, {"code": "import_allowance_exceeded"})

    async with preview_db() as session:
        reservations = (
            await session.execute(
                select(BulkImportReservation).where(
                    BulkImportReservation.user_id == owner_id,
                    BulkImportReservation.tier == "free",
                )
            )
        ).scalars().all()
        imports_rows = (
            await session.execute(select(Import).where(Import.user_id == owner_id))
        ).scalars().all()
        previews = (
            await session.execute(
                select(ImportPreview).where(ImportPreview.owner_id == owner_id)
            )
        ).scalars().all()
        outbox_rows = (
            await session.execute(
                select(TaskOutbox).where(
                    TaskOutbox.task_type == "import",
                    TaskOutbox.target_type == "import",
                    TaskOutbox.target_id.in_(PREVIEW_IDS),
                )
            )
        ).scalars().all()

        assert len(reservations) == 250
        assert sum(row.source_bucket == "instagram" for row in reservations) == 150
        assert sum(row.source_bucket == "other" for row in reservations) == 100
        assert len(imports_rows) == len(outbox_rows) == 1
        committed = [preview for preview in previews if preview.state == "committed"]
        opened = [preview for preview in previews if preview.state == "open"]
        assert len(committed) == len(opened) == 1
        assert len(dispatched) == 1 and len(dispatched[0]) == 1
        assert await session.scalar(
            select(func.count()).select_from(BulkImportReservation).where(
                BulkImportReservation.user_id == owner_id
            )
        ) == 250
