"""Real PostgreSQL regression for selected preview admission through outbox handoff.

The test uses a unique schema in the local TEST_DATABASE_URL database, creates
only the tables involved in admission/dispatch, and drops that schema afterward.
It does not call storage, extractors, Cloud Tasks, or any external provider.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
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
from app.services.imports.entries import build_occurrences
from app.services.imports.ig_parser import ParsedExport, SavedPost
from app.tasks.worker_entry import ImportPayload

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
PREVIEW_ID = "01K8B3Q6M2V7H9T4W5X1Y0ZABC"


def _url_for_local_database(url: str) -> tuple[str, str]:
    parsed = make_url(url)
    if parsed.drivername not in {"postgres", "postgresql", "postgresql+asyncpg"}:
        raise ValueError("TEST_DATABASE_URL must use PostgreSQL")
    if parsed.query:
        raise ValueError("TEST_DATABASE_URL must not contain connection override parameters")
    if parsed.host not in {None, "localhost", "127.0.0.1", "::1"}:
        raise ValueError("preview worker contract tests only accept local PostgreSQL")
    admin_url = parsed.set(drivername="postgresql").render_as_string(hide_password=False)
    async_url = parsed.set(drivername="postgresql+asyncpg").render_as_string(hide_password=False)
    return admin_url, async_url


def test_local_database_guard_rejects_query_host_override() -> None:
    with pytest.raises(ValueError, match="connection override parameters"):
        _url_for_local_database("postgresql://localhost/postgres?host=db.example.invalid")


@pytest_asyncio.fixture
async def preview_db(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is unset; preview/outbox PostgreSQL regression not run")

    import asyncpg

    try:
        admin_url, async_url = _url_for_local_database(TEST_DATABASE_URL)
        admin = await asyncpg.connect(admin_url, timeout=10)
    except Exception as exc:
        pytest.fail(f"local PostgreSQL setup failed ({type(exc).__name__})")

    schema = f"r15_preview_{uuid.uuid4().hex}"
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM PUBLIC')
    except Exception as exc:
        await admin.close()
        pytest.fail(f"isolated preview schema setup failed ({type(exc).__name__})")
    finally:
        if not admin.is_closed():
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
            actual_schema = await connection.exec_driver_sql("SELECT current_schema()")
            assert actual_schema.scalar_one() == schema
        yield sessions
    finally:
        await engine.dispose()
        cleanup = await asyncpg.connect(admin_url, timeout=10)
        try:
            await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await cleanup.close()


@pytest.mark.asyncio
async def test_preview_commit_persists_only_selected_keys_for_worker(
    preview_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = str(uuid.uuid4())
    selected_id = str(uuid.uuid4())
    unselected_id = str(uuid.uuid4())
    selected_url = "https://www.instagram.com/reel/DBSelected123/"
    unselected_url = "https://www.instagram.com/reel/DBUnselected456/"
    now = datetime.now(UTC)
    policy_settings = SimpleNamespace(
        supabase_bucket_exports="exports",
        bulk_import_policy_version="integration-test-v1",
        bulk_import_history_reconciled=True,
        bulk_import_premium_history_mode="carry_forward",
        bulk_import_existing_item_mode="exclude",
        bulk_import_manual_share_mode="exclude",
        bulk_import_saved_audio_mode="exclude",
    )
    monkeypatch.setattr(imports, "get_settings", lambda: policy_settings)
    dispatched: list[list[str]] = []

    async def record_dispatch(ids: list[str]) -> None:
        dispatched.append(ids)

    monkeypatch.setattr(imports, "dispatch_after_commit", record_dispatch)

    async with preview_db() as session:
        session.add(Profile(id=owner))
        await session.flush()
        session.add(
            ImportPreview(
                id=PREVIEW_ID,
                owner_id=owner,
                expires_at=now + timedelta(minutes=50),
                parser_schema_hash="parser-v1",
                semantic_fingerprint="a" * 64,
                candidate_count=2,
            )
        )
        session.add_all(
            [
                ImportPreviewCandidate(
                    preview_id=PREVIEW_ID,
                    candidate_id=selected_id,
                    owner_id=owner,
                    ordinal=0,
                    reservation_key=reservation_key_for_url(selected_url),
                    source_bucket="instagram",
                    kind="media",
                    source_url_norm=selected_url.rstrip("/"),
                    collection_names=[],
                ),
                ImportPreviewCandidate(
                    preview_id=PREVIEW_ID,
                    candidate_id=unselected_id,
                    owner_id=owner,
                    ordinal=1,
                    reservation_key=reservation_key_for_url(unselected_url),
                    source_bucket="instagram",
                    kind="media",
                    source_url_norm=unselected_url.rstrip("/"),
                    collection_names=[],
                ),
            ]
        )
        session.add(
            ImportStaging(
                import_id=PREVIEW_ID,
                owner_id=owner,
                bucket="exports",
                storage_key=f"{owner}/{PREVIEW_ID}.zip",
                lease_until=now + timedelta(hours=1),
            )
        )
        await session.commit()

    request = imports.ImportPreviewCommitRequest(candidate_ids=[selected_id])
    async with preview_db() as session:
        response = await imports.commit_import_preview(
            PREVIEW_ID, request, SimpleNamespace(id=owner), session
        )
        assert response.import_id == PREVIEW_ID
        assert response.state == "processing"

    async with preview_db() as session:
        import_row = await session.get(Import, PREVIEW_ID)
        preview = await session.get(ImportPreview, PREVIEW_ID)
        outbox = (
            await session.execute(
                select(TaskOutbox).where(
                    TaskOutbox.task_type == "import",
                    TaskOutbox.target_id == PREVIEW_ID,
                )
            )
        ).scalar_one()
        reservations = (
            await session.execute(
                select(BulkImportReservation.reservation_key).where(
                    BulkImportReservation.user_id == owner
                )
            )
        ).scalars().all()
        assert import_row is not None and import_row.admission_enforced is True
        assert preview is not None and preview.state == "committed"
        assert reservations == [reservation_key_for_url(selected_url)]

    payload = ImportPayload.model_validate(outbox.payload)
    assert payload.admitted_reservation_keys == [reservation_key_for_url(selected_url)]
    assert payload.already_reserved_reservation_keys == []
    assert dispatched == [[outbox.id]]

    parsed = ParsedExport(
        posts=(
            SavedPost(url=selected_url, saved_at=now),
            SavedPost(url=unselected_url, saved_at=now),
        ),
        collections=(),
        audio=(),
        parser_schema_hash="parser-v1",
        unknown_label_count=0,
        entries_with_unknowns=0,
    )
    occurrences, eligible = build_occurrences(
        parsed,
        import_id=PREVIEW_ID,
        owner_id=owner,
        admitted_reservation_keys=set(payload.admitted_reservation_keys or ()),
        already_reserved_reservation_keys=set(payload.already_reserved_reservation_keys or ()),
    )
    assert set(eligible) == {reservation_key_for_url(selected_url)}
    assert [row.values["outcome"] for row in occurrences] == ["admitted", "excluded"]
    assert occurrences[1].values["entry_reservation_key"] is None
