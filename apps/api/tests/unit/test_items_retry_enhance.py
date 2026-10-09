"""Unit tests for POST /v1/items/{id}/retry + /enhance, POST /work/enhance,
the local-dispatcher enhance branch, and the enhance worker itself.

No DB, no network, no creds: auth + session dependencies are overridden
with fakes, and enqueue / LLM / embedding calls are mocked. Mocking
style mirrors tests/unit (plain fakes + monkeypatch, no live fixtures).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.items as items_module
import app.services.ingest.enhance as enhance_module
import app.tasks.worker_entry as worker_entry_module
from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Item, Profile
from app.main import create_app
from app.services.llm.base import StructuredSummary
from app.tasks.local_dispatcher import dispatch_local

_TEST_USER_ID = "user-1"


def _make_item(state: str, item_id: str = "item-1") -> Item:
    return Item(
        id=item_id,
        user_id=_TEST_USER_ID,
        source_url="https://www.instagram.com/reel/DbM7eWINqAy/",
        source_url_norm="https://instagram.com/reel/DbM7eWINqAy",
        platform="instagram",
        state=state,
        saved_at=datetime.now(UTC),
        source_type="share_extension",
        failure_reason="boom" if state == "failed" else None,
    )


class _FakeResult:
    def __init__(self, item: Item | None) -> None:
        self._item = item

    def scalar_one_or_none(self) -> Item | None:
        return self._item

    def scalar_one(self) -> Item:
        assert self._item is not None
        return self._item


class _FakeSession:
    """Stands in for AsyncSession — ignores the SQL text, returns the
    canned row. Owner isolation is covered by the WHERE user_id filter in
    the real query (same pattern as GET/DELETE): None row → 404."""

    def __init__(self, item: Item | None) -> None:
        self.item = item
        self.added: list[Any] = []
        self.commits = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(self.item)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commits += 1


def _client_with(
    item: Item | None, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, _FakeSession, AsyncMock]:
    session = _FakeSession(item)

    async def _override_user() -> Profile:
        return Profile(id=_TEST_USER_ID, display_name="Test User")

    async def _override_session() -> AsyncIterator[_FakeSession]:
        yield session

    app = create_app()
    app.dependency_overrides[_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    mock_enqueue = AsyncMock()
    monkeypatch.setattr(items_module, "enqueue_task", mock_enqueue)
    return TestClient(app), session, mock_enqueue


class TestRetry:
    def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _make_item("failed")
        client, session, mock_enqueue = _client_with(item, monkeypatch)

        resp = client.post("/v1/items/item-1/retry")

        assert resp.status_code == 200
        assert resp.json() == {"item_id": "item-1", "state": "queued"}
        assert item.state == "queued"
        assert item.failure_reason is None
        assert session.commits == 1
        mock_enqueue.assert_awaited_once_with(
            "ingest",
            {"item_id": "item-1", "user_id": _TEST_USER_ID},
            dedup_key="ingest-item-1",
        )

    def test_queued_is_idempotent_no_reenqueue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _, mock_enqueue = _client_with(_make_item("queued"), monkeypatch)

        resp = client.post("/v1/items/item-1/retry")

        assert resp.status_code == 200
        assert resp.json() == {"item_id": "item-1", "state": "queued"}
        mock_enqueue.assert_not_awaited()

    @pytest.mark.parametrize(
        "state",
        ["downloading", "analyzing", "indexing", "text_indexed", "fully_indexed", "source_gone"],
    )
    def test_non_failed_returns_422(self, state: str, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _make_item(state)
        client, _, mock_enqueue = _client_with(item, monkeypatch)

        resp = client.post("/v1/items/item-1/retry")

        assert resp.status_code == 422
        assert item.state == state
        mock_enqueue.assert_not_awaited()

    def test_missing_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _, mock_enqueue = _client_with(None, monkeypatch)

        resp = client.post("/v1/items/nope/retry")

        assert resp.status_code == 404
        mock_enqueue.assert_not_awaited()


class TestEnhance:
    @pytest.mark.parametrize("state", ["text_indexed", "fully_indexed", "failed"])
    def test_happy_path(self, state: str, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _make_item(state)
        client, session, mock_enqueue = _client_with(item, monkeypatch)

        resp = client.post("/v1/items/item-1/enhance")

        assert resp.status_code == 200
        assert resp.json() == {"item_id": "item-1", "state": "indexing"}
        assert item.state == "indexing"
        assert item.failure_reason is None
        assert session.commits == 1
        mock_enqueue.assert_awaited_once_with(
            "enhance",
            {"item_id": "item-1", "user_id": _TEST_USER_ID},
            dedup_key="enhance-item-1",
        )

    @pytest.mark.parametrize(
        "state", ["queued", "downloading", "analyzing", "indexing", "source_gone"]
    )
    def test_bad_state_returns_422(self, state: str, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _make_item(state)
        client, _, mock_enqueue = _client_with(item, monkeypatch)

        resp = client.post("/v1/items/item-1/enhance")

        assert resp.status_code == 422
        assert item.state == state
        mock_enqueue.assert_not_awaited()

    def test_missing_returns_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client, _, mock_enqueue = _client_with(None, monkeypatch)

        resp = client.post("/v1/items/nope/enhance")

        assert resp.status_code == 404
        mock_enqueue.assert_not_awaited()


class TestWorkerEnhance:
    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(worker_entry_module, "enhance_item", mock)
        client = TestClient(create_app())

        resp = client.post("/work/enhance", json={"item_id": "item-1"})

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "item_id": "item-1"}
        mock.assert_awaited_once_with("item-1")

    def test_error_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(side_effect=RuntimeError("llm down"))
        monkeypatch.setattr(worker_entry_module, "enhance_item", mock)
        client = TestClient(create_app())

        resp = client.post("/work/enhance", json={"item_id": "item-1"})

        assert resp.status_code == 500
        assert "enhance_failed" in resp.json()["detail"]


class TestLocalDispatcherEnhance:
    async def test_enhance_branch_routes_to_enhance_item(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(enhance_module, "enhance_item", mock)

        await dispatch_local("enhance", {"item_id": "item-9"})

        mock.assert_awaited_once_with("item-9")


class _FakeSummaryModel:
    def __init__(self) -> None:
        self.transcripts: list[str | None] = []

    async def summarize(self, frames: Any, audio: Any, transcript: str | None) -> StructuredSummary:
        self.transcripts.append(transcript)
        return StructuredSummary(
            title="New title",
            summary="New summary",
            transcript="new transcript",
            on_screen_text="new ost",
            topics=["reel"],
            sentiment="informative",  # type: ignore[arg-type]
            primary_language="en",
            duration_seconds=12.0,
        )


def _patch_enhance_deps(
    monkeypatch: pytest.MonkeyPatch, session: _FakeSession
) -> tuple[_FakeSummaryModel, list[tuple[str, Any]]]:
    @asynccontextmanager
    async def _scope() -> AsyncIterator[_FakeSession]:
        yield session

    model = _FakeSummaryModel()
    writes: list[tuple[str, Any]] = []

    class _FakeStore:
        def __init__(self, pool: Any) -> None:
            self._pool = pool

        async def write(self, user_id: str, item: Any) -> str:
            writes.append((user_id, item))
            return item.item_id

    monkeypatch.setattr(enhance_module, "session_scope", _scope)
    monkeypatch.setattr(enhance_module, "GeminiSummaryModel", lambda: model)
    monkeypatch.setattr(enhance_module, "PgVectorStore", _FakeStore)
    monkeypatch.setattr(enhance_module, "get_pg_pool", AsyncMock(return_value=object()))
    return model, writes


class TestEnhanceItem:
    async def test_success_marks_fully_indexed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _make_item("indexing")
        item.transcript = "spoken words"
        item.on_screen_text = "SALE"
        item.caption = "nice reel"
        session = _FakeSession(item)
        model, writes = _patch_enhance_deps(monkeypatch, session)

        await enhance_module.enhance_item("item-1")

        assert item.state == "fully_indexed"
        assert item.failure_reason is None
        assert item.title == "New title"
        assert item.summary == "New summary"
        assert model.transcripts and "spoken words" in (model.transcripts[0] or "")
        assert len(writes) == 1
        user_id, indexed = writes[0]
        assert user_id == _TEST_USER_ID
        assert indexed.item_id == "item-1"
        assert "New title" in indexed.text_corpus

    async def test_no_text_marks_failed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(_make_item("indexing"))
        model, writes = _patch_enhance_deps(monkeypatch, session)

        with pytest.raises(enhance_module.EnhanceError, match="no text to enhance"):
            await enhance_module.enhance_item("item-1")

        item = session.item
        assert item is not None
        assert item.state == "failed"
        assert item.failure_reason == "no text to enhance"
        assert model.transcripts == []
        assert writes == []
