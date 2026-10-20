"""Unit tests for the IG import pipeline (Phase 1 backend).

Covers `ig_pipeline.process_import` (dedup, counters, per-item enqueue),
POST /v1/imports (inline vs async paths), GET /v1/imports/{id} (owner
scoping), POST /work/import, and the local-dispatcher import branch.

No DB, no network, no creds: auth + session dependencies are overridden
with fakes; enqueue / storage / parser-boundary calls are mocked. Style
mirrors tests/unit (plain fakes + monkeypatch, no live fixtures).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

import app.api.v1.imports as imports_module
import app.services.imports.ig_pipeline as ig_pipeline_module
import app.tasks.worker_entry as worker_entry_module
from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Import, ImportEntry, Profile
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

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return []


class _FakeSession:
    """Stands in for AsyncSession for import tests.

    `execute` only ever sees the imports-row SELECT (item upserts go
    through the monkeypatched `_insert_item_once`, audio through a
    recorded bulk insert) — the canned row + recorded statements are
    enough to assert owner scoping and counter writes.
    """

    def __init__(self, import_row: Import | None) -> None:
        self.import_row = import_row
        self.bind = "synthetic-bind"
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False
        self.cancel_commit = False
        self.commit_applied = False
        self.stmts: list[Any] = []
        self.import_entries: list[Any] = []
        self.occurrence_positions: set[int] = set()

    async def execute(self, stmt: Any) -> _FakeResult:
        self.stmts.append(stmt)
        return _FakeResult(self.import_row)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1
        self.commit_applied = True
        if self.cancel_commit:
            import asyncio

            raise asyncio.CancelledError()
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def flush(self) -> None:
        return None

    async def delete(self, obj: Any) -> None:
        self.deleted = getattr(self, "deleted", []) + [obj]


class _SequencedSession(_FakeSession):
    """Return deterministic owner-scoped lookup results in route-query order."""

    def __init__(self, results: list[Any]) -> None:
        super().__init__(None)
        self.results = list(results)

    async def execute(self, stmt: Any) -> _FakeResult:
        self.stmts.append(stmt)
        row = self.results.pop(0) if self.results else None
        return _FakeResult(row)


class _RowsResult(_FakeResult):
    def __init__(self, rows: list[Any]) -> None:
        super().__init__(None)
        self.rows = rows

    def scalars(self) -> _RowsResult:
        return self

    def all(self) -> list[Any]:
        return self.rows


class _RouteSession(_FakeSession):
    def __init__(self, results: list[Any]) -> None:
        super().__init__(None)
        self.results = list(results)

    async def execute(self, stmt: Any) -> Any:
        self.stmts.append(stmt)
        value = self.results.pop(0)
        return _RowsResult(value) if isinstance(value, list) else _FakeResult(value)


def _patch_pipeline_session(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @asynccontextmanager
    async def _scope() -> AsyncIterator[_FakeSession]:
        yield session

    monkeypatch.setattr(ig_pipeline_module, "session_scope", _scope)
    monkeypatch.setattr(
        ig_pipeline_module,
        "target_intent_is_current_in_session",
        AsyncMock(return_value=True),
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        ig_pipeline_module,
        "record_task_intent",
        AsyncMock(side_effect=lambda *_a, **_k: SimpleNamespace(id="outbox")),
    )
    monkeypatch.setattr(
        ig_pipeline_module,
        "ensure_existing_intent",
        AsyncMock(side_effect=lambda *_a, **_k: SimpleNamespace(id="outbox")),
    )
    monkeypatch.setattr(ig_pipeline_module, "dispatch_after_commit", AsyncMock())

    async def _no_entry(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _create_entry(
        _session: _FakeSession,
        *,
        item_id: str | None,
        state: str,
        work_queued_at: datetime | None,
        **_kwargs: Any,
    ) -> Any:
        from types import SimpleNamespace

        entry = SimpleNamespace(item_id=item_id, state=state, work_queued_at=work_queued_at)
        _session.import_entries.append(entry)
        return entry

    async def _persist_occurrences(
        _session: _FakeSession, occurrences: list[Any]
    ) -> set[int]:
        positions = {int(occ.values["source_position"]) for occ in occurrences}
        new = positions - _session.occurrence_positions
        _session.occurrence_positions.update(positions)
        return new

    async def _save_progress(
        _session: _FakeSession,
        import_row: Import,
        *,
        total_parsed: int,
        parser_schema_hash: str,
        finalize_manifest: bool = False,
    ) -> Any:
        from app.services.imports.entries import ImportProgress

        progress = ImportProgress(
            total_parsed=len(_session.occurrence_positions),
            full_queued=sum(entry.work_queued_at is not None for entry in _session.import_entries),
            text_indexed_done=0,
            full_done=0,
            failed=0,
        )
        import_row.total_parsed = progress.total_parsed
        import_row.full_queued = progress.full_queued
        import_row.text_indexed_done = progress.text_indexed_done
        import_row.full_done = progress.full_done
        import_row.failed = progress.failed
        import_row.parser_schema_hash = parser_schema_hash
        import_row.state = "indexing" if finalize_manifest else "processing"
        return progress

    monkeypatch.setattr(ig_pipeline_module, "_existing_import_entry", _no_entry)
    monkeypatch.setattr(ig_pipeline_module, "create_import_entry", _create_entry)
    monkeypatch.setattr(ig_pipeline_module, "persist_occurrences", _persist_occurrences)
    monkeypatch.setattr(ig_pipeline_module, "save_import_progress", _save_progress)


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
    @staticmethod
    def _execution() -> Any:
        from app.tasks.worker_claims import TargetExecution, WorkerIdentity

        return TargetExecution(
            identity=WorkerIdentity("outbox-1", "import", "import", "imp-1", 1),
            token="lease-token",
            owner_id=_TEST_USER_ID,
        )

    async def test_stale_execution_cannot_write_import_chunk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        fence = AsyncMock(side_effect=(True, False))
        monkeypatch.setattr(ig_pipeline_module, "target_intent_is_current_in_session", fence)
        insert_item = AsyncMock(side_effect=AssertionError("stale execution must not insert items"))
        monkeypatch.setattr(ig_pipeline_module, "_insert_item_once", insert_item)

        with pytest.raises(ig_pipeline_module.ImportLeaseLostError):
            await ig_pipeline_module.process_import(
                "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)], audio=False),
                execution=self._execution(),
            )

        assert fence.await_count == 2
        insert_item.assert_not_awaited()
        assert session.commits == 0
        assert row.state == "processing"
        assert row.failure_reason is None

    async def test_stale_failure_handler_does_not_mark_import_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        fence_results = iter((True, True, False))
        fence = AsyncMock(side_effect=lambda *_args: next(fence_results))
        monkeypatch.setattr(ig_pipeline_module, "target_intent_is_current_in_session", fence)

        async def fail_insert(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("synthetic transient failure")

        monkeypatch.setattr(ig_pipeline_module, "_insert_item_once", fail_insert)
        with pytest.raises(ig_pipeline_module.ImportLeaseLostError):
            await ig_pipeline_module.process_import(
                "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)], audio=False),
                execution=self._execution(),
            )

        assert fence.await_count == 3
        assert session.commits == 0
        assert row.state == "processing"
        assert row.failure_reason is None

    async def test_stale_parse_failure_does_not_publish_failed_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        monkeypatch.setattr(
            ig_pipeline_module,
            "_resolve_parsed",
            lambda _source: (_ for _ in ()).throw(ParserError("synthetic malformed export")),
        )
        monkeypatch.setattr(
            ig_pipeline_module, "target_intent_is_current_in_session", AsyncMock(return_value=False)
        )

        with pytest.raises(ig_pipeline_module.ImportLeaseLostError):
            await ig_pipeline_module.process_import(
                "imp-1", _TEST_USER_ID, b"bad-zip", execution=self._execution()
            )

        assert session.commits == 0
        assert row.state == "processing"
        assert row.failure_reason is None

    async def test_stale_final_transaction_cannot_write_audio_or_terminal_counters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        fence = AsyncMock(side_effect=(True, False))
        monkeypatch.setattr(ig_pipeline_module, "target_intent_is_current_in_session", fence)
        audio_insert = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "_insert_audio_rows", audio_insert)
        audio_only = ParsedExport(
            posts=(), collections=(),
            audio=(SavedAudio(title="song", artist="band", saved_at=None),),
            parser_schema_hash="abc123hash", unknown_label_count=0, entries_with_unknowns=0,
        )

        with pytest.raises(ig_pipeline_module.ImportLeaseLostError):
            await ig_pipeline_module.process_import(
                "imp-1", _TEST_USER_ID, audio_only, execution=self._execution()
            )

        assert fence.await_count == 2
        audio_insert.assert_not_awaited()
        assert session.commits == 0
        assert row.state == "processing"
        assert row.full_queued == 0

    async def test_stale_execution_after_chunk_commit_does_not_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        fence = AsyncMock(side_effect=(True, True, False))
        monkeypatch.setattr(ig_pipeline_module, "target_intent_is_current_in_session", fence)
        _patch_item_insert(monkeypatch)
        dispatch = ig_pipeline_module.dispatch_after_commit

        with pytest.raises(ig_pipeline_module.ImportLeaseLostError):
            await ig_pipeline_module.process_import(
                "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)], audio=False),
                execution=self._execution(),
            )

        assert session.commits == 1
        assert fence.await_count == 3
        dispatch.assert_not_awaited()
        assert row.state == "processing"

    async def test_inserts_enqueue_and_counters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        _, inserted = _patch_item_insert(monkeypatch)
        mock_enqueue = ig_pipeline_module.dispatch_after_commit
        outcome = await ig_pipeline_module.process_import(
            "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)], audio=False),
            execution=self._execution(),
        )

        # 1 post + 1 collection media = 2 candidates, both new.
        assert outcome.ok is True
        assert outcome.total_parsed == 2
        assert outcome.queued == 2
        assert outcome.duplicates == 0
        assert outcome.skipped == 0
        assert len(inserted) == 2
        assert mock_enqueue.await_count == 1
        assert len(mock_enqueue.await_args.args[0]) == 2
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
        mock_enqueue = ig_pipeline_module.dispatch_after_commit
        outcome = await ig_pipeline_module.process_import(
            "imp-1",
            _TEST_USER_ID,
            _parsed([_post(_URL_A), _post(_URL_A_DUP)], audio=False),
            execution=self._execution(),
        )

        # A-dup normalizes identically; collection media B is also new.
        assert outcome.total_parsed == 3
        assert outcome.queued == 2
        assert outcome.duplicates == 1
        assert mock_enqueue.await_count == 1
        assert row.full_queued == 2

    async def test_preexisting_row_counts_as_duplicate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        _patch_item_insert(monkeypatch, preseed_norms={normalize_url(_URL_A)})
        mock_enqueue = ig_pipeline_module.dispatch_after_commit

        outcome = await ig_pipeline_module.process_import(
            "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)], audio=False),
            execution=self._execution(),
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
        mock_enqueue = ig_pipeline_module.dispatch_after_commit

        outcome = await ig_pipeline_module.process_import(
            "imp-1",
            _TEST_USER_ID,
            _parsed([_post(_URL_GARBAGE)], audio=False),
            execution=self._execution(),
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

        outcome = await ig_pipeline_module.process_import(
            "imp-1", _TEST_USER_ID, _parsed([_post(_URL_A)]), execution=self._execution()
        )

        assert outcome.audio_saved == 1
        assert outcome.total_parsed == 3  # 2 media + 1 audio

    async def test_missing_import_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(None)
        _patch_pipeline_session(monkeypatch, session)

        with pytest.raises(ImportNotFoundError):
            await ig_pipeline_module.process_import(
                "nope", _TEST_USER_ID, _parsed(), execution=self._execution()
            )

    async def test_bad_zip_marks_failed_without_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = _import_row()
        session = _FakeSession(row)
        _patch_pipeline_session(monkeypatch, session)
        summary_intent = AsyncMock()
        monkeypatch.setattr(
            ig_pipeline_module, "record_import_summary_intent", summary_intent
        )
        mock_enqueue = ig_pipeline_module.dispatch_after_commit

        outcome = await ig_pipeline_module.process_import(
            "imp-1", _TEST_USER_ID, b"not-a-zip", execution=self._execution()
        )

        assert outcome.ok is False
        assert row.state == "failed"
        assert row.failure_reason is not None
        assert row.finished_at is not None
        summary_intent.assert_awaited_once_with(
            session, owner_id=_TEST_USER_ID, import_id="imp-1"
        )
        mock_enqueue.assert_not_awaited()

    def test_worker_filter_applies_admitted_keys_to_posts_collections_and_audio(self) -> None:
        from app.services.imports.admission import reservation_key_for_url, saved_audio_identity_url

        parsed = _parsed([_post(_URL_A), _post(_URL_B)], audio=True)
        allowed = {
            reservation_key_for_url(_URL_A),
            reservation_key_for_url(saved_audio_identity_url("song", "band", None)),
        }

        filtered = ig_pipeline_module._filter_admitted_entries(parsed, allowed)

        assert [post.url for post in filtered.posts] == [_URL_A]
        assert [post.url for coll in filtered.collections for post in coll.media] == []
        assert len(filtered.audio) == 1


def _imports_client(
    session: _FakeSession, *, raise_server_exceptions: bool = True
) -> TestClient:
    async def _override_user() -> Profile:
        return Profile(id=_TEST_USER_ID, display_name="Test User")

    async def _override_session() -> AsyncIterator[_FakeSession]:
        yield session

    app = FastAPI()
    app.include_router(imports_module.router, prefix="/v1")
    # The legacy intake implementation remains unmounted in production. These
    # focused tests use a private test-only route to retain coverage of its
    # reconciliation/admission helpers while the public route requires preview
    # selection and commit.
    legacy_test_router = APIRouter()
    legacy_test_router.add_api_route(
        "/_test/legacy-imports",
        imports_module.create_import,
        methods=["POST"],
        response_model=imports_module.ImportCreateResponse,
    )
    app.include_router(legacy_test_router)
    app.dependency_overrides[_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _enable_admission(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    from types import SimpleNamespace

    from app.services.imports.admission import (
        AdmissionResult,
        AdmissionStatus,
        AdmissionTier,
        reservation_key_for_url,
    )

    monkeypatch.setattr(
        imports_module,
        "get_settings",
        lambda: SimpleNamespace(
            bulk_import_policy_version="test-v1",
            bulk_import_history_reconciled=True,
            bulk_import_premium_history_mode="carry_forward",
            bulk_import_existing_item_mode="exclude",
            bulk_import_manual_share_mode="exclude",
            bulk_import_saved_audio_mode="exclude",
            supabase_bucket_exports="exports",
        ),
    )

    calls: list[Any] = []

    async def admit(_session: Any, *, user_id: str, tier: Any, candidates: Any, policy: Any) -> Any:
        calls.extend(candidates)
        return AdmissionResult(
            tier=AdmissionTier.FREE,
            candidates=tuple(
                type("CandidateStatus", (), {
                    "reservation_key": reservation_key_for_url(candidate.url),
                    "source_bucket": candidate.source_bucket,
                    "status": AdmissionStatus.ADMITTED,
                })()
                for candidate in candidates
            ),
            total_used=len(candidates),
            instagram_used=len(candidates),
            other_used=0,
        )

    monkeypatch.setattr(imports_module, "admit_bulk_import", admit)
    monkeypatch.setattr(imports_module, "create_import_staging_marker", AsyncMock(return_value=True))
    monkeypatch.setattr(
        imports_module,
        "lock_import_staging_for_admission",
        AsyncMock(return_value=SimpleNamespace(import_id="staged-import")),
    )
    return calls


class TestPostImports:
    def test_public_direct_import_is_gated_before_consuming_or_staging_upload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _FakeSession(None)
        client = _imports_client(session)
        stream = AsyncMock(side_effect=AssertionError("upload must not be read"))
        parse = AsyncMock(side_effect=AssertionError("archive must not be parsed"))
        upload = AsyncMock(side_effect=AssertionError("archive must not be staged"))
        staging = AsyncMock(side_effect=AssertionError("staging marker must not be created"))
        admission = AsyncMock(side_effect=AssertionError("quota must not be checked"))
        monkeypatch.setattr(imports_module, "_stream_upload_to_temp", stream)
        monkeypatch.setattr(imports_module, "parse_zip", parse)
        monkeypatch.setattr(imports_module, "upload_object", upload)
        monkeypatch.setattr(imports_module, "create_import_staging_marker", staging)
        monkeypatch.setattr(imports_module, "admit_bulk_import", admission)

        response = client.post(
            "/v1/imports",
            files={"file": ("export.zip", b"must-not-be-consumed", "application/zip")},
        )

        assert response.status_code == 410
        assert response.json()["detail"] == {
            "code": "preview_required",
            "message": "Create an import preview, select candidates, then commit the selection.",
            "preview_url": "/v1/imports/previews",
        }
        public_operation = client.get("/openapi.json").json()["paths"]["/v1/imports"]["post"]
        assert "requestBody" not in public_operation
        stream.assert_not_awaited()
        parse.assert_not_awaited()
        upload.assert_not_awaited()
        staging.assert_not_awaited()
        admission.assert_not_awaited()
        assert session.added == []
        assert session.commits == 0

    async def test_upload_staging_reads_in_bounded_chunks_and_aborts_at_limit(self) -> None:
        from app.api.v1.imports import UploadTooLarge, _stream_upload_to_temp

        class ChunkedUpload:
            def __init__(self) -> None:
                self.calls: list[int] = []

            async def read(self, size: int) -> bytes:
                self.calls.append(size)
                return b"x" * size

        upload = ChunkedUpload()
        with pytest.raises(UploadTooLarge):
            await _stream_upload_to_temp(upload, max_bytes=32)  # type: ignore[arg-type]
        assert upload.calls == [64 * 1024]

    async def test_archive_parse_deadline_is_enforced(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1.imports import _parse_archive_bounded

        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def slow_parse(_path):
            started.set()
            release.wait(timeout=2)
            finished.set()
            return _parsed()

        monkeypatch.setattr(imports_module, "parse_zip", slow_parse)
        try:
            with pytest.raises(TimeoutError):
                await _parse_archive_bounded(tmp_path / "bounded.zip", timeout_seconds=1)
            assert started.is_set()
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 1)

    async def test_timed_out_parsers_keep_capacity_until_background_work_finishes(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.api.v1.imports import ParserCapacityExceeded, _parse_archive_bounded

        started = threading.Barrier(2)
        release = threading.Event()
        finished = threading.Event()
        finished_count = 0
        finished_lock = threading.Lock()
        calls = 0
        calls_lock = threading.Lock()

        def blocking_parse(_path):
            nonlocal calls, finished_count
            with calls_lock:
                calls += 1
                call_number = calls
            if call_number > 2:
                return _parsed(audio=False)
            started.wait(timeout=2)
            try:
                release.wait(timeout=2)
            finally:
                with finished_lock:
                    finished_count += 1
                    if finished_count == 2:
                        finished.set()
            return _parsed(audio=False)

        monkeypatch.setattr(imports_module, "parse_zip", blocking_parse)
        attempts = await asyncio.gather(
            *(
                _parse_archive_bounded(tmp_path / f"blocked-{index}.zip", timeout_seconds=0.01)
                for index in range(2)
            ),
            return_exceptions=True,
        )
        assert all(isinstance(result, TimeoutError) for result in attempts)
        with pytest.raises(ParserCapacityExceeded):
            await _parse_archive_bounded(tmp_path / "rejected.zip", timeout_seconds=1)

        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        result = await _parse_archive_bounded(tmp_path / "recovered.zip", timeout_seconds=1)
        assert result.parser_schema_hash == "abc123hash"

    def test_semantic_replay_with_different_request_key_returns_existing_before_staging(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_admission(monkeypatch)
        existing = Import(
            id="existing-semantic-import",
            user_id=_TEST_USER_ID,
            state="complete",
            semantic_fingerprint=imports_module._semantic_import_fingerprint(_parsed(audio=False)),
            total_parsed=1,
        )
        session = _SequencedSession([None, existing])
        client = _imports_client(session)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed(audio=False))
        upload = AsyncMock()
        marker = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", upload)
        monkeypatch.setattr(imports_module, "create_import_staging_marker", marker)

        response = client.post(
            "/_test/legacy-imports",
            files={"file": ("different-packaging.zip", b"different raw bytes", "application/zip")},
            headers={"Idempotency-Key": "different-request-key"},
        )

        assert response.status_code == 202
        assert response.json() == {
            "import_id": existing.id,
            "state": existing.state,
            "total_parsed": existing.total_parsed,
        }
        upload.assert_not_awaited()
        marker.assert_not_awaited()
        assert session.added == []

    def test_semantic_duplicate_race_after_staging_cleans_only_duplicate_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_admission(monkeypatch)
        existing = Import(
            id="existing-semantic-import",
            user_id=_TEST_USER_ID,
            state="indexing",
            semantic_fingerprint=imports_module._semantic_import_fingerprint(_parsed(audio=False)),
            total_parsed=4,
        )
        # With no client idempotency key there is one semantic lookup before
        # staging and a second one under the account lock.
        session = _SequencedSession([None, existing])
        client = _imports_client(session)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed(audio=False))
        upload = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", upload)
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 202
        assert response.json()["import_id"] == existing.id
        upload.assert_awaited_once()
        cleanup.assert_awaited_once()
        assert cleanup.await_args.kwargs["error_code"] == "SemanticDuplicateUpload"
        assert session.added == []
        assert session.commits == 1

    def test_semantic_fingerprint_is_order_independent_but_preserves_folder_attribution_and_multiplicity(
        self,
    ) -> None:
        post_a = _post(_URL_A)
        post_b = _post(_URL_B)
        original = _parsed([post_a, post_b], audio=False)
        reordered = ParsedExport(
            posts=(post_b, post_a),
            collections=original.collections,
            audio=original.audio,
            parser_schema_hash=original.parser_schema_hash,
            unknown_label_count=0,
            entries_with_unknowns=0,
        )
        duplicate = ParsedExport(
            posts=(post_a, post_a, post_b),
            collections=original.collections,
            audio=original.audio,
            parser_schema_hash=original.parser_schema_hash,
            unknown_label_count=0,
            entries_with_unknowns=0,
        )
        moved = ParsedExport(
            posts=original.posts,
            collections=(SavedCollection("work", None, None, None, (_post(_URL_B),)),),
            audio=original.audio,
            parser_schema_hash=original.parser_schema_hash,
            unknown_label_count=0,
            entries_with_unknowns=0,
        )

        fingerprint = imports_module._semantic_import_fingerprint(original)
        assert imports_module._semantic_import_fingerprint(reordered) == fingerprint
        assert imports_module._semantic_import_fingerprint(duplicate) != fingerprint
        assert imports_module._semantic_import_fingerprint(moved) != fingerprint


class TestImportDiscoveryAndRetry:
    async def test_recent_imports_use_owner_scoped_started_at_id_keyset(self) -> None:
        from types import SimpleNamespace

        now = datetime(2026, 10, 18, 12, tzinfo=UTC)
        first = Import(id="01A", user_id=_TEST_USER_ID, state="complete")
        first.started_at = now
        second = Import(id="019", user_id=_TEST_USER_ID, state="failed")
        second.started_at = now
        session = _RouteSession([[first, second]])

        result = await imports_module.list_imports(
            SimpleNamespace(id=_TEST_USER_ID), session, limit=1
        )

        assert [row.import_id for row in result.imports] == ["01A"]
        assert result.next_cursor is not None
        assert imports_module._decode_import_cursor(result.next_cursor) == (now, "01A")
        query = str(session.stmts[0])
        assert "imports.user_id" in query
        assert "imports.started_at" in query
        assert "imports.id" in query

    async def test_import_entries_page_checks_ownership_and_uses_reservation_key_cursor(self) -> None:
        from types import SimpleNamespace

        owned = Import(id="owned-import", user_id=_TEST_USER_ID, state="indexing")
        first = ImportEntry(
            import_id=owned.id,
            reservation_key="1" * 64,
            owner_id=_TEST_USER_ID,
            source_url_norm=_URL_A,
            source_bucket="instagram",
            state="queued",
        )
        first.created_at = datetime(2026, 10, 18, 12, tzinfo=UTC)
        second = ImportEntry(
            import_id=owned.id,
            reservation_key="2" * 64,
            owner_id=_TEST_USER_ID,
            source_url_norm=_URL_B,
            source_bucket="instagram",
            state="completed",
        )
        second.created_at = datetime(2026, 10, 18, 11, tzinfo=UTC)
        session = _RouteSession([owned, [first, second]])

        result = await imports_module.list_import_entries(
            owned.id,
            SimpleNamespace(id=_TEST_USER_ID),
            session,
            limit=1,
        )

        assert [row.reservation_key for row in result.entries] == ["1" * 64]
        assert result.next_cursor == "1" * 64
        assert "imports.user_id" in str(session.stmts[0])
        assert "import_entries.owner_id" in str(session.stmts[1])
        assert "import_entries.reservation_key" in str(session.stmts[1])

    async def test_selected_retry_requeues_only_failed_owned_item_and_preserves_membership(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        failed_key = "a" * 64
        noop_key = "b" * 64
        import_row = Import(id="owned-import", user_id=_TEST_USER_ID, state="failed")
        import_row.total_parsed = 2
        import_row.failed = 1
        failed_entry = ImportEntry(
            import_id=import_row.id,
            reservation_key=failed_key,
            owner_id=_TEST_USER_ID,
            source_url_norm=_URL_A,
            source_bucket="instagram",
            state="retryable_failed",
            item_id="item-failed",
            last_error_code="network_error",
        )
        queued_entry = ImportEntry(
            import_id=import_row.id,
            reservation_key=noop_key,
            owner_id=_TEST_USER_ID,
            source_url_norm=_URL_B,
            source_bucket="instagram",
            state="queued",
            item_id="item-queued",
        )
        failed_item = SimpleNamespace(
            id="item-failed", user_id=_TEST_USER_ID, state="failed", failure_reason="timeout"
        )
        queued_item = SimpleNamespace(
            id="item-queued", user_id=_TEST_USER_ID, state="queued", failure_reason=None
        )
        session = _RouteSession(
            [
                Profile(id=_TEST_USER_ID, display_name="Test"),
                import_row,
                [failed_entry, queued_entry],
                [failed_item, queued_item],
                1,
            ]
        )
        monkeypatch.setattr(
            imports_module,
            "derive_import_progress",
            AsyncMock(
                return_value=SimpleNamespace(
                    total_parsed=2,
                    full_queued=2,
                    text_indexed_done=0,
                    full_done=0,
                    failed=0,
                )
            ),
        )
        monkeypatch.setattr(imports_module, "next_generation", AsyncMock(return_value=2))
        monkeypatch.setattr(
            imports_module,
            "record_task_intent",
            AsyncMock(return_value=SimpleNamespace(id="outbox-retry")),
        )
        dispatch = AsyncMock()
        monkeypatch.setattr(imports_module, "dispatch_after_commit", dispatch)

        result = await imports_module.retry_import_entries(
            import_row.id,
            imports_module.RetryImportEntriesRequest(
                reservation_keys=[failed_key, noop_key]
            ),
            SimpleNamespace(id=_TEST_USER_ID),
            session,
        )

        assert result.queued == [failed_key]
        assert result.skipped == [noop_key]
        assert failed_item.state == "queued"
        assert failed_item.failure_reason is None
        assert failed_entry.state == "queued"
        assert failed_entry.last_error_code is None
        assert failed_entry.retry_count == 1
        assert failed_entry.item_id == "item-failed"
        assert failed_entry.reservation_key == failed_key
        assert queued_item.state == "queued"
        assert session.commits == 1
        assert [type(row).__name__ for row in session.added] == ["IngestionEvent"]
        assert [str(stmt).find("FOR UPDATE") >= 0 for stmt in session.stmts[:4]] == [
            True,
            True,
            True,
            True,
        ]
        assert "profiles.id" in str(session.stmts[0])
        assert "imports.id" in str(session.stmts[1])
        imports_module.next_generation.assert_awaited_once_with(
            session, "ingest", "item", "item-failed"
        )
        dispatch.assert_awaited_once_with(["outbox-retry"])

    async def test_retry_count_is_durable_and_caps_repeated_manual_requeues(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        key = "c" * 64
        import_row = Import(id="owned-import", user_id=_TEST_USER_ID, state="failed")
        entry = ImportEntry(
            import_id=import_row.id,
            reservation_key=key,
            owner_id=_TEST_USER_ID,
            source_url_norm=_URL_A,
            source_bucket="instagram",
            state="retryable_failed",
            item_id="item-failed",
            retry_count=3,
        )
        item = SimpleNamespace(
            id="item-failed", user_id=_TEST_USER_ID, state="failed", failure_reason="timeout"
        )
        session = _RouteSession(
            [Profile(id=_TEST_USER_ID, display_name="Test"), import_row, [entry], [item], 0]
        )
        monkeypatch.setattr(
            imports_module,
            "derive_import_progress",
            AsyncMock(
                return_value=SimpleNamespace(
                    total_parsed=1,
                    full_queued=1,
                    text_indexed_done=0,
                    full_done=0,
                    failed=1,
                )
            ),
        )
        intent = AsyncMock()
        monkeypatch.setattr(imports_module, "record_task_intent", intent)
        monkeypatch.setattr(imports_module, "dispatch_after_commit", AsyncMock())

        result = await imports_module.retry_import_entries(
            import_row.id,
            imports_module.RetryImportEntriesRequest(reservation_keys=[key]),
            SimpleNamespace(id=_TEST_USER_ID),
            session,
        )

        assert result.queued == []
        assert result.skipped == [key]
        assert entry.retry_count == 3
        assert entry.state == "terminal_failed"
        assert entry.last_error_code == "retry_limit_reached"
        assert item.state == "failed"
        assert import_row.state == "failed"
        assert session.commits == 1
        intent.assert_not_awaited()

    @pytest.mark.parametrize("same_body", [True, False])
    def test_idempotency_key_reuses_import_and_rejects_changed_body(
        self, monkeypatch: pytest.MonkeyPatch, same_body: bool
    ) -> None:
        import hashlib

        _enable_admission(monkeypatch)
        key = "request-123"
        raw = b"zip-bytes"
        fingerprint = hashlib.sha256(
            b"spillthereel-import-v1\0" + hashlib.sha256(raw).digest()
        ).hexdigest()
        existing = Import(
            id="existing-import",
            user_id=_TEST_USER_ID,
            state="indexing",
            request_key_hash=hashlib.sha256(key.encode()).hexdigest(),
            request_fingerprint=fingerprint if same_body else "0" * 64,
            total_parsed=2,
        )
        session = _FakeSession(existing)
        client = _imports_client(session)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed(audio=False))
        upload = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", upload)

        response = client.post(
            "/_test/legacy-imports",
            files={"file": ("export.zip", raw, "application/zip")},
            headers={"Idempotency-Key": key},
        )

        assert upload.await_count == 0
        if same_body:
            assert response.status_code == 202
            assert response.json() == {
                "import_id": "existing-import",
                "state": "indexing",
                "total_parsed": 2,
            }
        else:
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "idempotency_key_payload_conflict"

    async def test_cancelled_commit_with_committed_reconciliation_still_reraises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from io import BytesIO
        from types import SimpleNamespace

        from fastapi import Response
        from starlette.datastructures import UploadFile

        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        session.cancel_commit = True
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))
        monkeypatch.setattr(imports_module, "upload_object", AsyncMock())
        monkeypatch.setattr(
            imports_module,
            "record_import_processing_intent",
            AsyncMock(return_value="outbox-cancelled-commit"),
        )
        reconcile = AsyncMock(return_value=imports_module.ImportCommitReconciliation.COMMITTED)
        monkeypatch.setattr(imports_module, "reconcile_import_commit", reconcile)
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)

        with pytest.raises(asyncio.CancelledError):
            await imports_module.create_import(
                Response(),
                UploadFile(filename="export.zip", file=BytesIO(b"zip-bytes")),
                SimpleNamespace(id=_TEST_USER_ID),
                session,
            )

        assert session.commit_applied is True
        reconcile.assert_awaited_once()
        cleanup.assert_not_awaited()

    async def test_cancelled_commit_with_unavailable_reconciliation_preserves_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from io import BytesIO
        from types import SimpleNamespace

        from fastapi import Response
        from starlette.datastructures import UploadFile

        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        session.cancel_commit = True
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))
        monkeypatch.setattr(imports_module, "upload_object", AsyncMock())
        monkeypatch.setattr(
            imports_module,
            "record_import_processing_intent",
            AsyncMock(return_value="outbox-cancelled-unknown"),
        )
        monkeypatch.setattr(
            imports_module,
            "reconcile_import_commit",
            AsyncMock(side_effect=RuntimeError("synthetic reconciliation database failure")),
        )
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)

        with pytest.raises(asyncio.CancelledError):
            await imports_module.create_import(
                Response(),
                UploadFile(filename="export.zip", file=BytesIO(b"zip-bytes")),
                SimpleNamespace(id=_TEST_USER_ID),
                session,
            )

        assert session.commit_applied is True
        cleanup.assert_not_awaited()

    def test_small_upload_stages_source_and_commits_durable_intent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.imports.admission import reservation_key_for_url

        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        client = _imports_client(session)
        parsed = _parsed([_post(_URL_A)], audio=False)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: parsed)
        mock_upload = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", mock_upload)
        mock_intent = AsyncMock(return_value="outbox-small")
        monkeypatch.setattr(imports_module, "record_import_processing_intent", mock_intent)
        mock_dispatch = AsyncMock()
        monkeypatch.setattr(imports_module, "dispatch_after_commit", mock_dispatch)

        resp = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert resp.status_code == 202
        body = resp.json()
        assert body["state"] == "processing"
        assert body["total_parsed"] == 0
        import_id = body["import_id"]
        assert session.commits == 1
        mock_upload.assert_awaited_once()
        assert mock_upload.await_args.args[1] == f"{_TEST_USER_ID}/{import_id}.zip"
        mock_intent.assert_awaited_once()
        assert mock_intent.await_args.kwargs["import_id"] == import_id
        assert mock_intent.await_args.kwargs["admitted_reservation_keys"] == {
            reservation_key_for_url(_URL_A), reservation_key_for_url(_URL_B)
        }
        mock_dispatch.assert_awaited_once_with(["outbox-small"])
        created = session.added[0]
        assert isinstance(created, Import)
        assert created.state == "processing"
        assert created.user_id == _TEST_USER_ID

    def test_staging_failure_rolls_back_admission_without_processing_intent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.media.supabase_storage import StorageError

        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        client = _imports_client(session)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))

        async def fail_upload(*_args: Any, **_kwargs: Any) -> None:
            raise StorageError("synthetic storage failure")

        intent = AsyncMock()
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", fail_upload)
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)
        monkeypatch.setattr(imports_module, "record_import_processing_intent", intent)

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 500
        assert session.commits == 0
        assert session.rollbacks == 0
        cleanup.assert_awaited_once()
        intent.assert_not_awaited()

    def test_commit_exception_preserves_source_for_reconciliation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        session.fail_commit = True
        client = _imports_client(session, raise_server_exceptions=False)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))
        monkeypatch.setattr(imports_module, "upload_object", AsyncMock())
        monkeypatch.setattr(
            imports_module, "record_import_processing_intent", AsyncMock(return_value="outbox")
        )
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)
        reconciliation = AsyncMock(return_value=imports_module.ImportCommitReconciliation.UNKNOWN)
        monkeypatch.setattr(imports_module, "reconcile_import_commit", reconciliation)

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 500
        assert session.rollbacks == 1
        assert session.commit_applied is True
        cleanup.assert_not_awaited()
        reconciliation.assert_awaited_once()

    def test_committed_lost_ack_reconciles_to_accepted_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        session.fail_commit = True
        session.bind = object()
        client = _imports_client(session, raise_server_exceptions=False)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))
        monkeypatch.setattr(imports_module, "upload_object", AsyncMock())
        monkeypatch.setattr(
            imports_module,
            "record_import_processing_intent",
            AsyncMock(return_value="outbox-committed"),
        )
        reconcile = AsyncMock(
            return_value=imports_module.ImportCommitReconciliation.COMMITTED
        )
        monkeypatch.setattr(imports_module, "reconcile_import_commit", reconcile)
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 202
        assert response.json()["import_id"]
        reconcile.assert_awaited_once_with(
            session.bind,
            import_id=response.json()["import_id"],
            user_id=_TEST_USER_ID,
            storage_key=f"{_TEST_USER_ID}/{response.json()['import_id']}.zip",
            outbox_id="outbox-committed",
        )
        cleanup.assert_not_awaited()

    def test_fresh_read_proving_absence_cleans_only_staged_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        session.fail_commit = True
        session.bind = object()
        client = _imports_client(session, raise_server_exceptions=False)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))
        monkeypatch.setattr(imports_module, "upload_object", AsyncMock())
        monkeypatch.setattr(
            imports_module,
            "record_import_processing_intent",
            AsyncMock(return_value="outbox-uncommitted"),
        )
        monkeypatch.setattr(
            imports_module,
            "reconcile_import_commit",
            AsyncMock(return_value=imports_module.ImportCommitReconciliation.PROVEN_UNCOMMITTED),
        )
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 500
        cleanup.assert_awaited_once()
        assert cleanup.await_args.kwargs["bucket"] == "exports"
        assert cleanup.await_args.kwargs["storage_key"] == f"{_TEST_USER_ID}/{session.added[0].id}.zip"

    def test_reconciliation_database_error_preserves_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        session.fail_commit = True
        session.bind = object()
        client = _imports_client(session, raise_server_exceptions=False)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))
        monkeypatch.setattr(imports_module, "upload_object", AsyncMock())
        monkeypatch.setattr(
            imports_module,
            "record_import_processing_intent",
            AsyncMock(return_value="outbox-db-error"),
        )
        monkeypatch.setattr(
            imports_module,
            "reconcile_import_commit",
            AsyncMock(side_effect=RuntimeError("synthetic database unavailable")),
        )
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 500
        cleanup.assert_not_awaited()

    def test_quota_rejection_cleans_staged_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.imports.admission import AdmissionResult, AdmissionStatus, AdmissionTier, CandidateAdmission, reservation_key_for_url

        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        client = _imports_client(session, raise_server_exceptions=False)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)], audio=False))
        upload = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", upload)
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)

        async def quota(*_args: Any, **kwargs: Any) -> AdmissionResult:
            candidates = kwargs["candidates"]
            return AdmissionResult(
                tier=AdmissionTier.FREE,
                candidates=tuple(
                    CandidateAdmission(reservation_key_for_url(candidate.url), candidate.source_bucket, AdmissionStatus.LIMIT_REACHED)
                    for candidate in candidates
                ),
                total_used=250,
                instagram_used=150,
                other_used=100,
            )

        monkeypatch.setattr(imports_module, "admit_bulk_import", quota)
        response = client.post("/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")})
        assert response.status_code == 409
        upload.assert_awaited_once()
        cleanup.assert_awaited_once()
        assert session.commits == 0

    async def test_blocked_upload_does_not_hold_account_admission_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio
        from io import BytesIO
        from types import SimpleNamespace

        from fastapi import Response
        from starlette.datastructures import UploadFile

        from app.services.imports.admission import (
            AdmissionResult,
            AdmissionStatus,
            AdmissionTier,
            CandidateAdmission,
            reservation_key_for_url,
        )

        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)], audio=False))
        upload_started = asyncio.Event()
        release_upload = asyncio.Event()
        account_lock = asyncio.Lock()
        independent_operation_done = asyncio.Event()

        async def upload(*_args: Any, **_kwargs: Any) -> None:
            upload_started.set()
            await release_upload.wait()

        async def admit(_session: Any, *, candidates: Any, **_kwargs: Any) -> AdmissionResult:
            async with account_lock:
                return AdmissionResult(
                    tier=AdmissionTier.FREE,
                    candidates=tuple(
                        CandidateAdmission(
                            reservation_key_for_url(candidate.url),
                            candidate.source_bucket,
                            AdmissionStatus.ADMITTED,
                        )
                        for candidate in candidates
                    ),
                    total_used=len(candidates),
                    instagram_used=len(candidates),
                    other_used=0,
                )

        monkeypatch.setattr(imports_module, "upload_object", upload)
        marker_committed = asyncio.Event()

        async def create_marker(*_args: Any, **_kwargs: Any) -> bool:
            # The marker transaction completes before the slow Storage call.
            session.commits += 1
            marker_committed.set()
            return True

        monkeypatch.setattr(imports_module, "create_import_staging_marker", create_marker)
        monkeypatch.setattr(
            imports_module,
            "lock_import_staging_for_admission",
            AsyncMock(return_value=SimpleNamespace(import_id="staged-import")),
        )
        monkeypatch.setattr(imports_module, "admit_bulk_import", admit)
        monkeypatch.setattr(imports_module, "record_import_processing_intent", AsyncMock(return_value="outbox"))
        monkeypatch.setattr(imports_module, "dispatch_after_commit", AsyncMock())

        request_task = asyncio.create_task(
            imports_module.create_import(
                Response(),
                UploadFile(filename="export.zip", file=BytesIO(b"zip-bytes")),
                SimpleNamespace(id=_TEST_USER_ID),
                session,
            )
        )
        try:
            await asyncio.wait_for(upload_started.wait(), timeout=1)
            assert marker_committed.is_set()

            async def independent_owner_operation() -> None:
                async with account_lock:
                    independent_operation_done.set()

            await asyncio.wait_for(independent_owner_operation(), timeout=1)
            assert independent_operation_done.is_set()
        finally:
            release_upload.set()
        result = await request_task
        assert result.state == "processing"

    def test_replay_of_already_admitted_url_does_not_create_new_import_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.imports.admission import (
            AdmissionResult,
            AdmissionStatus,
            AdmissionTier,
            CandidateAdmission,
            reservation_key_for_url,
        )

        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        client = _imports_client(session)
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed([_post(_URL_A)]))

        async def already_admitted(
            _session: Any, *, user_id: str, tier: Any, candidates: Any, policy: Any
        ) -> AdmissionResult:
            return AdmissionResult(
                tier=AdmissionTier.FREE,
                candidates=tuple(
                    CandidateAdmission(
                        reservation_key_for_url(candidate.url),
                        candidate.source_bucket,
                        AdmissionStatus.ALREADY_ADMITTED,
                    )
                    for candidate in candidates
                ),
                total_used=1,
                instagram_used=1,
                other_used=0,
            )

        monkeypatch.setattr(imports_module, "admit_bulk_import", already_admitted)
        upload = AsyncMock()
        intent = AsyncMock()
        cleanup = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", upload)
        monkeypatch.setattr(imports_module, "_schedule_import_staging_cleanup", cleanup)
        monkeypatch.setattr(imports_module, "record_import_processing_intent", intent)

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 202
        assert response.json()["import_id"]
        assert session.rollbacks == 0
        upload.assert_awaited_once()
        intent.assert_awaited_once()
        assert intent.await_args.kwargs["already_reserved_reservation_keys"]

    def test_large_upload_enqueues_worker_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        client = _imports_client(session)
        posts = [_post(f"https://www.instagram.com/reel/reel{i:06d}/") for i in range(51)]
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: _parsed(posts, audio=False))
        mock_upload = AsyncMock()
        monkeypatch.setattr(imports_module, "upload_object", mock_upload)
        mock_enqueue = AsyncMock()
        monkeypatch.setattr(imports_module, "dispatch_after_commit", mock_enqueue)
        monkeypatch.setattr(
            imports_module,
            "record_import_processing_intent",
            AsyncMock(return_value="outbox"),
        )

        resp = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert resp.status_code == 202
        body = resp.json()
        assert body["state"] == "processing"
        assert body["total_parsed"] == 0
        import_id = body["import_id"]
        mock_upload.assert_awaited_once()
        assert mock_upload.await_args.args[0] == "exports"
        assert mock_upload.await_args.args[1] == f"{_TEST_USER_ID}/{import_id}.zip"
        mock_enqueue.assert_awaited_once_with(["outbox"])

    def test_bad_zip_returns_400(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _enable_admission(monkeypatch)
        session = _FakeSession(None)
        client = _imports_client(session)
        monkeypatch.setattr(
            imports_module,
            "parse_zip",
            lambda _path: (_ for _ in ()).throw(ParserError("boom")),
        )

        resp = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"junk", "application/zip")}
        )

        assert resp.status_code == 400
        assert session.added == []

    def test_unconfigured_policy_fails_before_read_or_parse(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = _FakeSession(None)
        client = _imports_client(session)
        monkeypatch.setattr(imports_module, "get_settings", lambda: object())
        def parse(_path: Any) -> ParsedExport:
            raise AssertionError("parser must not run")

        monkeypatch.setattr(imports_module, "parse_zip", parse)

        resp = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert resp.status_code == 503
        assert resp.json()["detail"]["code"] == "bulk_import_policy_not_configured"
        assert session.added == []

    def test_configured_caps_still_fail_closed_until_old_usage_is_reconciled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace
        from app.services.imports.admission import AdmissionPolicyNotConfigured

        monkeypatch.setattr(
            imports_module,
            "get_settings",
            lambda: SimpleNamespace(
                bulk_import_policy_version="test-v1",
                bulk_import_history_reconciled=False,
                bulk_import_premium_history_mode="carry_forward",
                bulk_import_existing_item_mode="exclude",
                bulk_import_manual_share_mode="exclude",
                bulk_import_saved_audio_mode="exclude",
            ),
        )
        with pytest.raises(AdmissionPolicyNotConfigured):
            imports_module._configured_admission_policy(imports_module.get_settings())

    def test_separate_premium_allowance_cannot_be_activated_without_owner_decision(
        self,
    ) -> None:
        from types import SimpleNamespace
        from app.services.imports.admission import AdmissionPolicyNotConfigured

        settings = SimpleNamespace(
            bulk_import_policy_version="test-v1",
            bulk_import_history_reconciled=True,
            bulk_import_premium_history_mode="separate_allowance",
            bulk_import_existing_item_mode="exclude",
            bulk_import_manual_share_mode="exclude",
            bulk_import_saved_audio_mode="exclude",
        )
        with pytest.raises(AdmissionPolicyNotConfigured, match="owner approval"):
            imports_module._configured_admission_policy(settings)

    def test_supported_urls_use_correct_bucket_and_unsupported_rows_do_not_consume(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.imports.admission import reservation_key_for_url

        calls = _enable_admission(monkeypatch)
        session = _FakeSession(None)
        client = _imports_client(session)
        parsed = ParsedExport(
            posts=(
                _post(_URL_A),
                _post("https://www.tiktok.com/@creator/video/123"),
                _post("https://example.com/unsupported"),
            ),
            collections=(),
            audio=(),
            parser_schema_hash="schema",
            unknown_label_count=0,
            entries_with_unknowns=0,
        )
        monkeypatch.setattr(imports_module, "parse_zip", lambda _path: parsed)
        mock_intent = AsyncMock(return_value="outbox")
        monkeypatch.setattr(imports_module, "record_import_processing_intent", mock_intent)
        monkeypatch.setattr(imports_module, "upload_object", AsyncMock())
        monkeypatch.setattr(imports_module, "dispatch_after_commit", AsyncMock())

        response = client.post(
            "/_test/legacy-imports", files={"file": ("export.zip", b"zip-bytes", "application/zip")}
        )

        assert response.status_code == 202
        assert [candidate.source_bucket.value for candidate in calls] == ["instagram", "other"]
        assert mock_intent.await_args.kwargs["admitted_reservation_keys"] == {
            reservation_key_for_url(_URL_A),
            reservation_key_for_url("https://www.tiktok.com/@creator/video/123"),
        }


def test_import_allowance_route_returns_server_owned_free_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_admission(monkeypatch)
    session = _FakeSession(Profile(id=_TEST_USER_ID, display_name="Test User"))
    client = _imports_client(session)

    response = client.get("/v1/me/import-allowance")

    assert response.status_code == 200
    assert response.json() == {
        "tier": "free",
        "policy_version": "test-v1",
        "total_limit": 250,
        "total_used": 0,
        "total_remaining": 250,
        "instagram_limit": 150,
        "instagram_used": 0,
        "instagram_remaining": 150,
        "other_limit": 100,
        "other_used": 0,
        "other_remaining": 100,
    }


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
    @staticmethod
    def _allow_exact_intent(monkeypatch: pytest.MonkeyPatch) -> None:
        from app.tasks.worker_claims import TargetIntentResult

        monkeypatch.setattr(
            worker_entry_module,
            "claim_target_intent",
            AsyncMock(return_value=TargetIntentResult("claimed", "token")),
        )
        monkeypatch.setattr(
            worker_entry_module, "complete_target_intent", AsyncMock(return_value=True)
        )

    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(return_value=ImportOutcome(ok=True, total_parsed=4, queued=3))
        monkeypatch.setattr(worker_entry_module, "process_stored_import", mock)
        self._allow_exact_intent(monkeypatch)
        client = TestClient(create_app())
        reservation_key = "a" * 64

        resp = client.post(
            "/work/import",
            json={
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
                "admitted_reservation_keys": [reservation_key],
                "outbox_id": "job-1",
                "generation": 1,
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["total_parsed"] == 4
        mock.assert_awaited_once()
        assert mock.await_args.args == (
            "imp-1", _TEST_USER_ID, f"{_TEST_USER_ID}/imp-1.zip", {reservation_key}
        )
        execution = mock.await_args.kwargs["execution"]
        assert execution.owner_id == _TEST_USER_ID
        assert execution.token == "token"
        assert execution.identity.target_id == "imp-1"

    def test_deterministic_failure_returns_200_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock = AsyncMock(return_value=ImportOutcome(ok=False))
        monkeypatch.setattr(worker_entry_module, "process_stored_import", mock)
        self._allow_exact_intent(monkeypatch)
        client = TestClient(create_app())

        resp = client.post(
            "/work/import",
            json={
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
                "outbox_id": "job-1",
                "generation": 1,
            },
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    def test_transient_error_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(worker_entry_module, "process_stored_import", mock)
        self._allow_exact_intent(monkeypatch)
        client = TestClient(create_app())

        resp = client.post(
            "/work/import",
            json={
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
                "outbox_id": "job-1",
                "generation": 1,
            },
        )

        assert resp.status_code == 500
        assert "import_failed" in resp.json()["detail"]

    def test_missing_storage_key_returns_422(self) -> None:
        client = TestClient(create_app())

        resp = client.post("/work/import", json={"import_id": "imp-1"})

        assert resp.status_code == 422

    def test_admission_keys_are_bound_to_durable_task_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.tasks.worker_claims import TargetIntentResult

        reservation_key = "c" * 64
        claim = AsyncMock(return_value=TargetIntentResult("completed"))
        process = AsyncMock(side_effect=AssertionError("completed delivery must not run import"))
        monkeypatch.setattr(worker_entry_module, "claim_target_intent", claim)
        monkeypatch.setattr(worker_entry_module, "process_stored_import", process)
        client = TestClient(create_app())

        resp = client.post(
            "/work/import",
            json={
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
                "admitted_reservation_keys": [reservation_key],
                "outbox_id": "job-1",
                "generation": 1,
            },
        )

        assert resp.status_code == 200
        claim.assert_awaited_once()
        assert claim.await_args.kwargs["payload_fields"]["admitted_reservation_keys"] == [
            reservation_key
        ]
        process.assert_not_awaited()


class TestLocalDispatcherImport:
    async def test_import_branch_routes_to_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(return_value=ImportOutcome(ok=True))
        monkeypatch.setattr(worker_entry_module, "process_stored_import", mock)
        TestWorkerImport._allow_exact_intent(monkeypatch)
        reservation_key = "b" * 64

        await dispatch_local(
            "import",
            {
                "import_id": "imp-1",
                "user_id": _TEST_USER_ID,
                "storage_key": f"{_TEST_USER_ID}/imp-1.zip",
                "admitted_reservation_keys": [reservation_key],
                "outbox_id": "job-1",
                "generation": 1,
            },
        )

        mock.assert_awaited_once()
        assert mock.await_args.args == (
            "imp-1", _TEST_USER_ID, f"{_TEST_USER_ID}/imp-1.zip", {reservation_key}
        )
        assert mock.await_args.kwargs["execution"].identity.outbox_id == "job-1"

    async def test_import_branch_missing_fields_noops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(ig_pipeline_module, "process_stored_import", mock)

        await dispatch_local("import", {"import_id": "imp-1"})

        mock.assert_not_awaited()
