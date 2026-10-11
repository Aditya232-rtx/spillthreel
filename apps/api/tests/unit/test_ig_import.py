"""Unit tests for the IG import pipeline (Phase 1 backend).

Covers `ig_pipeline.process_import` (dedup, counters, per-item enqueue),
POST /v1/imports (inline vs async paths), GET /v1/imports/{id} (owner
scoping), POST /work/import, and the local-dispatcher import branch.

No DB, no network, no creds: auth + session dependencies are overridden
with fakes; enqueue / storage / parser-boundary calls are mocked. Style
mirrors tests/unit (plain fakes + monkeypatch, no live fixtures).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.v1.imports as imports_module
import app.services.imports.ig_pipeline as ig_pipeline_module
import app.tasks.worker_entry as worker_entry_module
from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Import, Profile
from app.main import create_app
from app.services.imports.ig_parser import (
    Owner,
    ParsedExport,
    ParserError,
    SavedAudio,
    SavedCollection,
    SavedPost,
)
from app.services.imports.ig_pipeline import (
    ImportNotFoundError,
    ImportOutcome,
)
from app.services.ingest.platform_detect import normalize_url
from app.tasks.local_dispatcher import dispatch_local

_TEST_USER_ID = "11111111-1111-1111-1111-111111111111"

_URL_A = "https://www.instagram.com/reel/DbM7eWINqAy/"
_URL_A_DUP = "https://www.instagram.com/reel/DbM7eWINqAy/?igsh=abc123&utm_source=x"
_URL_B = "https://www.instagram.com/p/C8xYz123456/"
_URL_GARBAGE = "https://example.com/not-an-ig-post"


def _post(url: str, caption: str | None = "tasty pasta") -> SavedPost:
    return SavedPost(
        url=url,
        saved_at=datetime(2026, 8, 2, 3, 3),
        caption=caption,
        hashtags=("pasta", "recipe"),
        owner=Owner(username="chef"),
    )


def _parsed(posts: list[SavedPost] | None = None, audio: bool = True) -> ParsedExport:
    return ParsedExport(
        posts=tuple(posts if posts is not None else [_post(_URL_A)]),
        collections=(
            SavedCollection(
                name="dinners",
                type=None,
                privacy=None,
                update_time=None,
                media=(_post(_URL_B),),
            ),
        ),
        audio=((SavedAudio(title="song", artist="band", saved_at=None),) if audio else ()),
        parser_schema_hash="abc123hash",
        unknown_label_count=0,
        entries_with_unknowns=0,
    )


class _FakeResult:
    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _FakeSession:
    """Stands in for AsyncSession for import tests.

    `execute` only ever sees the imports-row SELECT (item upserts go
    through the monkeypatched `_insert_item_once`, audio through a
    recorded bulk insert) — the canned row + recorded statements are
    enough to assert owner scoping and counter writes.
    """

    def __init__(self, import_row: Import | None) -> None:
        self.import_row = import_row
        self.added: list[Any] = []
        self.commits = 0
        self.stmts: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.stmts.append(stmt)
        return _FakeResult(self.import_row)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


def _patch_pipeline_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @asynccontextmanager
    async def _scope() -> AsyncIterator[_FakeSession]:
        yield session

    monkeypatch.setattr(ig_pipeline_module, "session_scope", _scope)


def _patch_item_insert(
    monkeypatch: pytest.MonkeyPatch, preseed_norms: set[str] | None = None
) -> tuple[set[str], list[tuple[str, dict[str, Any]]]]:
    """Fake the saves-mirroring upsert: None on norm conflict, new id else."""
    seen: set[str] = set(preseed_norms or set())
    inserted: list[tuple[str, dict[str, Any]]] = []

    async def _fake_insert(session: Any, values: dict[str, Any]) -> str | None:
        norm = str(values["source_url_norm"])
        if norm in seen:
            return None
        seen.add(norm)
        new_id = f"item-{len(seen)}"
        inserted.append((new_id, values))
        return new_id

    monkeypatch.setattr(ig_pipeline_module, "_insert_item_once", _fake_insert)
    return seen, inserted


def _import_row(state: str = "processing") -> Import:
    return Import(id="imp-1", user_id=_TEST_USER_ID, state=state)


class TestBuildItemValues:
    def test_ok_maps_parser_fields(self) -> None:
        values = ig_pipeline_module._build_item_values(
            _post(_URL_A), _TEST_USER_ID, datetime.now(UTC)
        )

        assert values is not None
        assert values["user_id"] == _TEST_USER_ID
        assert values["source_url"] == _URL_A
        assert values["source_url_norm"] == normalize_url(_URL_A)
        assert values["platform"] == "instagram"
        assert values["state"] == "queued"
        assert values["source_type"] == "instagram_import"
        assert values["caption"] == "tasty pasta"
        assert values["hashtags"] == ["pasta", "recipe"]
        assert values["owner_username"] == "chef"

    def test_unsupported_platform_skipped(self) -> None:
        assert (
            ig_pipeline_module._build_item_values(
                _post(_URL_GARBAGE), _TEST_USER_ID, datetime.now(UTC)
            )
            is None
        )

    def test_empty_url_skipped(self) -> None:
        assert (
            ig_pipeline_module._build_item_values(_post("  "), _TEST_USER_ID, datetime.now(UTC))
            is None
        )

    def test_naive_saved_at_assumed_utc(self) -> None:
        values = ig_pipeline_module._build_item_values(
            _post(_URL_A), _TEST_USER_ID, datetime.now(UTC)
        )

        assert values is not None
        saved_at = values["saved_at"]
        assert isinstance(saved_at, datetime)
        assert saved_at.tzinfo is not None


class TestInsertItemOnce:
    async def test_statement_carries_saves_dedup_shape(self) -> None:
        session = _FakeSession(None)

        got = await ig_pipeline_module._insert_item_once(
            session,  # type: ignore[arg-type]
            {
                "id": "x",
                "user_id": _TEST_USER_ID,
                "source_url": _URL_A,
                "source_url_norm": normalize_url(_URL_A),
            },
        )

        assert got is None  # fake returns the canned None row
        assert len(session.stmts) == 1
        rendered = str(session.stmts[0])
        assert "ON CONFLICT" in rendered
        assert "source_url_norm" in rendered


class TestProcessImport:
    async def test_inserts_enqueue_and_counters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        _, inserted = _patch_item_insert(monkeypatch)
        mock_enqueue = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "enqueue_task", mock_enqueue)

        outcome = await ig_pipeline_module.process_import(
            "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)], audio=False)
        )

        # 1 post + 1 collection media = 2 candidates, both new.
        assert outcome.ok is True
        assert outcome.total_parsed == 2
        assert outcome.queued == 2
        assert outcome.duplicates == 0
        assert outcome.skipped == 0
        assert len(inserted) == 2
        assert mock_enqueue.await_count == 2
        for call, (item_id, _) in zip(mock_enqueue.await_args_list, inserted, strict=True):
            assert call.args[0] == "ingest"
            assert call.args[1] == {"item_id": item_id, "user_id": _TEST_USER_ID}
            assert call.kwargs["dedup_key"] == f"ingest-{item_id}"
        assert row.state == "indexing"
        assert row.total_parsed == 2
        assert row.full_queued == 2
        assert row.parser_schema_hash == "abc123hash"
        assert session.commits >= 1

    async def test_duplicate_within_batch_enqueued_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        _patch_item_insert(monkeypatch)
        mock_enqueue = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "enqueue_task", mock_enqueue)

        outcome = await ig_pipeline_module.process_import(
            "imp-1",
            _TEST_USER_ID,
            _parsed([_post(_URL_A), _post(_URL_A_DUP)], audio=False),
        )

        # A-dup normalizes identically; collection media B is also new.
        assert outcome.total_parsed == 3
        assert outcome.queued == 2
        assert outcome.duplicates == 1
        assert mock_enqueue.await_count == 2
        assert row.full_queued == 2

    async def test_preexisting_row_counts_as_duplicate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        _patch_item_insert(monkeypatch, preseed_norms={normalize_url(_URL_A)})
        mock_enqueue = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "enqueue_task", mock_enqueue)

        outcome = await ig_pipeline_module.process_import(
            "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)], audio=False)
        )

        assert outcome.queued == 1  # only collection media B is new
        assert outcome.duplicates == 1
        mock_enqueue.assert_awaited_once()

    async def test_garbage_row_skipped_without_enqueue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        _, inserted = _patch_item_insert(monkeypatch)
        mock_enqueue = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "enqueue_task", mock_enqueue)

        outcome = await ig_pipeline_module.process_import(
            "imp-1",
            _TEST_USER_ID,
            _parsed([_post(_URL_GARBAGE)], audio=False),
        )

        assert outcome.total_parsed == 2  # garbage still counts as parsed
        assert outcome.skipped == 1
        assert outcome.queued == 1
        assert all(v["source_url"] != _URL_GARBAGE for _, v in inserted)
        mock_enqueue.assert_awaited_once()

    async def test_audio_saved_on_first_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        _patch_item_insert(monkeypatch)
        monkeypatch.setattr(ig_pipeline_module, "enqueue_task", AsyncMock())

        outcome = await ig_pipeline_module.process_import(
            "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)])
        )

        assert outcome.audio_saved == 1
        assert outcome.total_parsed == 3  # 2 media + 1 audio

    async def test_missing_import_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(None)
        _patch_pipeline_session(monkeypatch, session)

        with pytest.raises(ImportNotFoundError):
            await ig_pipeline_module.process_import("nope", _TEST_USER_ID, _parsed())

    async def test_bad_zip_marks_failed_without_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        mock_enqueue = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "enqueue_task", mock_enqueue)

        outcome = await ig_pipeline_module.process_import("imp-1", _TEST_USER_ID, b"not-a-zip")

        assert outcome.ok is False
        assert row.state == "failed"
        assert row.failure_reason is not None
        assert row.finished_at is not None
        mock_enqueue.assert_not_awaited()


def _imports_client(session: _FakeSession) -> TestClient:
    async def _override_user() -> Profile:
        return Profile(id=_TEST_USER_ID, display_name="Test User")

    async def _override_session() -> AsyncIterator[_FakeSession]:
        yield session

    app = FastAPI()
    app.include_router(imports_module.router, prefix="/v1")
    app.dependency_overrides[_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    return TestClient(app)


class TestPostImports:
    def test_inline_path_returns_201(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(None)
        client = _imports_client(session)
        parsed = _parsed([_post(_URL_A)], audio=False)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: parsed)
        mock_process = AsyncMock(return_value=ImportOutcome(ok=True, total_parsed=2, queued=2))
        monkeypatch.setattr(imports_module, "process_import", mock_process)

        resp = client.post(
            "/v1/imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["state"] == "indexing"
        assert body["total_parsed"] == 2
        assert body["import_id"]
        mock_process.assert_awaited_once()
        assert mock_process.await_args.args[1] == _TEST_USER_ID
        assert mock_process.await_args.args[2] is parsed
        created = session.added[0]
        assert isinstance(created, Import)
        assert created.state == "processing"
        assert created.user_id == _TEST_USER_ID

    def test_large_upload_enqueues_worker_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(None)
        client = _imports_client(session)
        posts = [_post(f"https://www.instagram.com/reel/reel{i:06d}/") for i in range(51)]
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed(posts, audio=False))
        mock_upload = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", mock_upload)
        mock_enqueue = AsyncMock()
        monkeypatch.setattr(imports_module, "enqueue_task", mock_enqueue)

        resp = client.post(
            "/v1/imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert resp.status_code == 202
        body = resp.json()
        assert body["state"] == "processing"
        assert body["total_parsed"] == 0
        import_id = body["import_id"]
        mock_upload.assert_awaited_once()
        assert mock_upload.await_args.args[0] == "exports"
        assert mock_upload.await_args.args[1] == f"{_TEST_USER_ID}/{import_id}.zip"
        mock_enqueue.assert_awaited_once_with(
            "import",
            {
                "import_id": import_id,
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/{import_id}.zip",
            },
            dedup_key=f"import-{import_id}",
        )

    def test_bad_zip_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(None)
        client = _imports_client(session)
        monkeypatch.setattr(
            imports_module,
            "parse_zip",
            lambda _path: (_ for _ in ()).throw(ParserError("boom")),
        )

        resp = client.post(
            "/v1/imports", files={"file": ("export.zip", b"junk", "application/zip")}
        )

        assert resp.status_code == 400
        assert session.added == []


def _full_import_row() -> Import:
    row = Import(id="imp-9", user_id=_TEST_USER_ID, state="indexing")
    row.total_parsed = 10
    row.text_indexed_done = 0
    row.full_queued = 8
    row.full_done = 0
    row.failed = 0
    row.parser_schema_hash = "abc123hash"
    row.started_at = datetime(2026, 8, 2, tzinfo=UTC)
    return row


class TestGetImport:
    def test_owner_gets_row_and_counts(self) -> None:
        client = _imports_client(_FakeSession(_full_import_row()))

        resp = client.get("/v1/imports/imp-9")

        assert resp.status_code == 200
        body = resp.json()
        assert body["import_id"] == "imp-9"
        assert body["state"] == "indexing"
        assert body["total_parsed"] == 10
        assert body["full_queued"] == 8
        assert body["full_done"] == 0
        assert body["failed"] == 0
        assert body["parser_schema_hash"] == "abc123hash"

    def test_others_import_is_404(self) -> None:
        # Owner isolation lives in the WHERE user_id filter (same pattern
        # as GET /v1/items/{id}): a row belonging to someone else reads
        # back as None → 404.
        client = _imports_client(_FakeSession(None))

        resp = client.get("/v1/imports/imp-9")

        assert resp.status_code == 404

    def test_query_filters_by_owner(self) -> None:
        session = _FakeSession(_full_import_row())
        client = _imports_client(session)

        resp = client.get("/v1/imports/imp-9")

        assert resp.status_code == 200
        assert session.stmts, "expected the SELECT to be recorded"
        rendered = " ".join(str(stmt) for stmt in session.stmts)
        assert "user_id" in rendered


class TestWorkerImport:
    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(return_value=ImportOutcome(ok=True, total_parsed=4, queued=3))
        monkeypatch.setattr(worker_entry_module, "process_stored_import", mock)
        client = TestClient(create_app())

        resp = client.post(
            "/work/import",
            json={
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["total_parsed"] == 4
        mock.assert_awaited_once_with("imp-1", _TEST_USER_ID, f"{_TEST_USER_ID}/imp-1.zip")

    def test_deterministic_failure_returns_200_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock = AsyncMock(return_value=ImportOutcome(ok=False))
        monkeypatch.setattr(worker_entry_module, "process_stored_import", mock)
        client = TestClient(create_app())

        resp = client.post(
            "/work/import",
            json={
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    def test_transient_error_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(worker_entry_module, "process_stored_import", mock)
        client = TestClient(create_app())

        resp = client.post(
            "/work/import",
            json={
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
            },
        )

        assert resp.status_code == 500
        assert "import_failed" in resp.json()["detail"]

    def test_missing_storage_key_returns_422(self) -> None:
        client = TestClient(create_app())

        resp = client.post("/work/import", json={"import_id": "imp-1"})

        assert resp.status_code == 422


class TestLocalDispatcherImport:
    async def test_import_branch_routes_to_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(return_value=ImportOutcome(ok=True))
        monkeypatch.setattr(ig_pipeline_module, "process_stored_import", mock)

        await dispatch_local(
            "import",
            {
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
            },
        )

        mock.assert_awaited_once_with("imp-1", _TEST_USER_ID, f"{_TEST_USER_ID}/imp-1.zip")

    async def test_import_branch_missing_fields_noops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "process_stored_import", mock)

        await dispatch_local("import", {"import_id": "imp-1"})

        mock.assert_not_awaited()
