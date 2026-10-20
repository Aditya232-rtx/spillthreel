"""Local PostgreSQL concurrency regressions for preview commit idempotency.

This test creates one random schema on an explicitly authorized, loopback
PostgreSQL 17 instance. It exercises the real API service and row locks, while
mocking only post-commit dispatch. No provider, storage, or external network is
used.
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


PREVIEW_ID = "01K8B3Q6M2V7H9T4W5X1Y0ZABC"


def _local_database_urls(value: str) -> tuple[str, str]:
    split_value = urlsplit(value)
    if split_value.query or split_value.fragment:
        raise ValueError("TEST_DATABASE_URL must not contain query or fragment overrides")
    parsed = make_url(value)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL")
    if parsed.query:
        raise ValueError("TEST_DATABASE_URL must not contain query or fragment overrides")
    if (parsed.host or "").lower().rstrip(".") not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("local preview concurrency tests only accept localhost PostgreSQL")
    return (
        parsed.set(drivername="postgresql").render_as_string(hide_password=False),
        parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False),
    )


def test_local_database_guard_rejects_remote_or_overridden_database() -> None:
    with pytest.raises(ValueError, match="localhost"):
        _local_database_urls("postgresql://db.example.invalid/postgres")
    with pytest.raises(ValueError, match="query or fragment"):
        _local_database_urls("postgresql://localhost/postgres?host=db.example.invalid")
    with pytest.raises(ValueError, match="query or fragment"):
        _local_database_urls("postgresql://localhost/postgres#remote")


@pytest_asyncio.fixture
async def preview_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if os.environ.get("SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS") != "1":
        pytest.skip("requires SPILLTHEREEL_ALLOW_LOCAL_SCHEMA_TESTS=1 for disposable local PostgreSQL")
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

    schema = f"preview_commit_{uuid.uuid4().hex}"
    try:
        server_address, version_number = await admin.fetchrow(
            "SELECT inet_server_addr()::text, current_setting('server_version_num')"
        )
        if not server_address:
            pytest.fail("local PostgreSQL integration requires a loopback TCP server address")
        try:
            parsed_address = ipaddress.ip_interface(server_address).ip
        except ValueError:
            pytest.fail("local PostgreSQL integration requires a valid server IP address")
        if not parsed_address.is_loopback:
            pytest.fail("local PostgreSQL integration refuses non-loopback server addresses")
        if not str(version_number).startswith("17"):
            pytest.fail("local preview concurrency tests require PostgreSQL 17")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
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
    except Exception:
        cleanup = await asyncpg.connect(admin_url, timeout=10)
        try:
            await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await cleanup.close()
        raise
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
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
async def test_concurrent_same_preview_commit_is_one_admission_and_conflicting_selection_fails(
    preview_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id = str(uuid.uuid4())
    candidate_one = str(uuid.uuid4())
    candidate_two = str(uuid.uuid4())
    url_one = "https://www.instagram.com/reel/ConcurrencyOne123/"
    url_two = "https://www.instagram.com/reel/ConcurrencyTwo456/"
    key_one = reservation_key_for_url(url_one)
    key_two = reservation_key_for_url(url_two)
    now = datetime.now(UTC)
    monkeypatch.setattr(
        imports,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_bucket_exports="exports",
            bulk_import_policy_version="preview-concurrency-test-v1",
            bulk_import_history_reconciled=True,
            bulk_import_premium_history_mode="carry_forward",
            bulk_import_existing_item_mode="exclude",
            bulk_import_manual_share_mode="exclude",
            bulk_import_saved_audio_mode="exclude",
        ),
    )
    dispatch_ids: list[list[str]] = []
    dispatch_guard = asyncio.Lock()

    async def capture_dispatch(ids: list[str]) -> None:
        async with dispatch_guard:
            dispatch_ids.append(list(ids))

    monkeypatch.setattr(imports, "dispatch_after_commit", capture_dispatch)

    async with preview_db() as session:
        session.add(Profile(id=owner_id))
        await session.flush()
        session.add(
            ImportPreview(
                id=PREVIEW_ID,
                owner_id=owner_id,
                expires_at=now + timedelta(minutes=50),
                parser_schema_hash="parser-concurrency-v1",
                semantic_fingerprint="b" * 64,
                candidate_count=2,
            )
        )
        session.add_all(
            [
                ImportPreviewCandidate(
                    preview_id=PREVIEW_ID,
                    candidate_id=candidate_one,
                    owner_id=owner_id,
                    ordinal=0,
                    reservation_key=key_one,
                    source_bucket="instagram",
                    kind="media",
                    source_url_norm=url_one.rstrip("/"),
                    collection_names=[],
                ),
                ImportPreviewCandidate(
                    preview_id=PREVIEW_ID,
                    candidate_id=candidate_two,
                    owner_id=owner_id,
                    ordinal=1,
                    reservation_key=key_two,
                    source_bucket="instagram",
                    kind="media",
                    source_url_norm=url_two.rstrip("/"),
                    collection_names=[],
                ),
            ]
        )
        session.add(
            ImportStaging(
                import_id=PREVIEW_ID,
                owner_id=owner_id,
                bucket="exports",
                storage_key=f"{owner_id}/{PREVIEW_ID}.zip",
                lease_until=now + timedelta(hours=1),
            )
        )
        await session.commit()

    start = asyncio.Event()
    ready = 0
    ready_guard = asyncio.Lock()

    async def commit_identical_selection() -> imports.ImportPreviewCommitResponse:
        nonlocal ready
        async with ready_guard:
            ready += 1
            if ready == 2:
                start.set()
        await start.wait()
        async with preview_db() as session:
            return await imports.commit_import_preview(
                PREVIEW_ID,
                imports.ImportPreviewCommitRequest(candidate_ids=[candidate_one]),
                SimpleNamespace(id=owner_id),
                session,
            )

    first, second = await asyncio.gather(
        commit_identical_selection(), commit_identical_selection()
    )
    assert first.import_id == second.import_id == PREVIEW_ID
    assert first.state == second.state == "processing"

    async with preview_db() as session:
        imports_rows = (
            await session.execute(select(Import).where(Import.preview_id == PREVIEW_ID))
        ).scalars().all()
        reservations = (
            await session.execute(
                select(BulkImportReservation.reservation_key).where(
                    BulkImportReservation.user_id == owner_id
                )
            )
        ).scalars().all()
        outbox_rows = (
            await session.execute(
                select(TaskOutbox).where(
                    TaskOutbox.task_type == "import",
                    TaskOutbox.target_type == "import",
                    TaskOutbox.target_id == PREVIEW_ID,
                )
            )
        ).scalars().all()
        preview = await session.get(ImportPreview, PREVIEW_ID)

        assert len(imports_rows) == 1
        assert imports_rows[0].admission_enforced is True
        assert reservations == [key_one]
        assert len(outbox_rows) == 1
        assert outbox_rows[0].payload["admitted_reservation_keys"] == [key_one]
        assert preview is not None and preview.state == "committed"
        assert dispatch_ids == [[outbox_rows[0].id]]

    async with preview_db() as session:
        with pytest.raises(HTTPException) as conflict:
            await imports.commit_import_preview(
                PREVIEW_ID,
                imports.ImportPreviewCommitRequest(candidate_ids=[candidate_two]),
                SimpleNamespace(id=owner_id),
                session,
            )
        assert conflict.value.status_code == 409
        assert conflict.value.detail == {"code": "import_preview_selection_conflict"}

    async with preview_db() as session:
        assert await session.scalar(select(func.count()).select_from(Import)) == 1
        assert await session.scalar(select(func.count()).select_from(BulkImportReservation)) == 1
        assert await session.scalar(select(func.count()).select_from(TaskOutbox)) == 1
