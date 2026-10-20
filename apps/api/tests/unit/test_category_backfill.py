"""Unit tests for the category backfill job (F-02).

No DB, no network, no creds: the matcher is pure and the DB layer is
faked by dispatching on the statement text. Style mirrors tests/unit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.services.categories.backfill as backfill_module
from app.db.models import Category, Item
from app.main import create_app
from app.services.categories.backfill import backfill_category, item_matches_category
from app.tasks.local_dispatcher import dispatch_local

_TEST_USER_ID = "user-1"
_TEST_CATEGORY_ID = "cat-1"


class TestItemMatchesCategory:
    def test_canonical_uses_keyword_classifier(self) -> None:
        assert item_matches_category(
            "RECIPES",
            title=None,
            caption="chicken recipe with 5 ingredients for dinner",
            transcript=None,
            on_screen_text=None,
            hashtags=None,
        )
        assert not item_matches_category(
            "RECIPES",
            title="vibes only",
            caption="just vibing on a random tuesday afternoon",
            transcript=None,
            on_screen_text=None,
            hashtags=None,
        )

    def test_custom_label_token_match(self) -> None:
        assert item_matches_category(
            "MEAL PREP",
            title="Sunday meal prep bowls",
            caption=None,
            transcript=None,
            on_screen_text=None,
            hashtags=None,
        )
        assert not item_matches_category(
            "MEAL PREP",
            title="gym day",
            caption="leg day workout",
            transcript=None,
            on_screen_text=None,
            hashtags=None,
        )

    def test_custom_label_hashtag_match(self) -> None:
        assert item_matches_category(
            "MARATHON",
            title="weekend run",
            caption="long run",
            transcript=None,
            on_screen_text=None,
            hashtags=["marathon", "running"],
        )

    def test_empty_name_never_matches(self) -> None:
        assert not item_matches_category(
            "   ",
            title="anything",
            caption="anything",
            transcript=None,
            on_screen_text=None,
            hashtags=None,
        )


def _make_item(item_id: str, caption: str | None, hashtags: list[str] | None = None) -> Item:
    return Item(
        id=item_id,
        user_id=_TEST_USER_ID,
        source_url=f"https://www.instagram.com/reel/{item_id}/",
        source_url_norm=f"https://instagram.com/reel/{item_id}",
        platform="instagram",
        state="fully_indexed",
        saved_at=datetime.now(UTC),
        source_type="share_extension",
        caption=caption,
        hashtags=hashtags or [],
    )


def _make_category(name: str = "MEAL PREP") -> Category:
    return Category(
        id=_TEST_CATEGORY_ID,
        user_id=_TEST_USER_ID,
        name=name,
        source="user",
        bg_color="#E8AC3D",
    )


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeResult:
    def __init__(self, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._row

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """Dispatches on statement text: item_categories → categories → items.

    Order matters: "item_categories" contains "items" as a substring, so
    it must be checked first.
    """

    def __init__(
        self,
        category: Category | None,
        items: list[Item],
        assigned: list[str] | None = None,
    ) -> None:
        self.category = category
        self.items = items
        self.assigned = assigned or []
        self.inserts: list[Any] = []
        self.commits = 0

    async def execute(self, stmt: Any) -> _FakeResult:
        text = str(stmt)
        if "item_categories" in text:
            if text.strip().upper().startswith("INSERT"):
                self.inserts.append(stmt)
                return _FakeResult()
            return _FakeResult(rows=list(self.assigned))
        if "FROM categories" in text or "categories" in text:
            return _FakeResult(row=self.category)
        return _FakeResult(rows=list(self.items))

    async def commit(self) -> None:
        self.commits += 1


def _patch_scope(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    import app.db.engine as engine_module

    @asynccontextmanager
    async def _scope() -> AsyncIterator[_FakeSession]:
        yield session

    monkeypatch.setattr(engine_module, "session_scope", _scope)


class TestBackfillCategory:
    async def test_tags_matching_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(
            _make_category("MEAL PREP"),
            [
                _make_item("item-1", "Sunday meal prep bowls for the week"),
                _make_item("item-2", "leg day workout at the gym"),
            ],
        )
        _patch_scope(monkeypatch, session)

        result = await backfill_category(_TEST_CATEGORY_ID, _TEST_USER_ID)

        assert result["status"] == "ok"
        assert result["scanned"] == 2
        assert result["matched"] == 1
        assert len(session.inserts) == 1
        assert session.commits == 1

    async def test_canonical_name_reuses_classifier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(
            _make_category("RECIPES"),
            [_make_item("item-1", "chicken recipe with 5 ingredients for dinner")],
        )
        _patch_scope(monkeypatch, session)

        result = await backfill_category(_TEST_CATEGORY_ID, _TEST_USER_ID)

        assert result["matched"] == 1

    async def test_skips_already_assigned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(
            _make_category("MEAL PREP"),
            [_make_item("item-1", "Sunday meal prep bowls")],
            assigned=["item-1"],
        )
        _patch_scope(monkeypatch, session)

        result = await backfill_category(_TEST_CATEGORY_ID, _TEST_USER_ID)

        assert result["matched"] == 0
        assert session.inserts == []
        assert session.commits == 0

    async def test_missing_category_is_not_retryable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session = _FakeSession(None, [])
        _patch_scope(monkeypatch, session)

        result = await backfill_category("nope", _TEST_USER_ID)

        assert result["status"] == "missing"


class TestWorkerBackfill:
    @staticmethod
    def _allow_exact_intent(monkeypatch: pytest.MonkeyPatch) -> None:
        import app.tasks.worker_entry as worker_entry
        from app.tasks.worker_claims import TargetIntentResult

        monkeypatch.setattr(
            worker_entry,
            "claim_target_intent",
            AsyncMock(return_value=TargetIntentResult("claimed", "token")),
        )
        monkeypatch.setattr(
            worker_entry, "complete_target_intent", AsyncMock(return_value=True)
        )

    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(
            return_value={"status": "ok", "category_id": _TEST_CATEGORY_ID, "matched": 2}
        )
        monkeypatch.setattr(backfill_module, "backfill_category", mock)
        self._allow_exact_intent(monkeypatch)
        client = TestClient(create_app())

        resp = client.post(
            "/work/category_backfill",
            json={"category_id": _TEST_CATEGORY_ID, "user_id": _TEST_USER_ID, "outbox_id": "job-1", "generation": 1},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock.assert_awaited_once_with(_TEST_CATEGORY_ID, _TEST_USER_ID)

    def test_error_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(side_effect=RuntimeError("db down"))
        monkeypatch.setattr(backfill_module, "backfill_category", mock)
        self._allow_exact_intent(monkeypatch)
        client = TestClient(create_app())

        resp = client.post(
            "/work/category_backfill",
            json={"category_id": _TEST_CATEGORY_ID, "user_id": _TEST_USER_ID, "outbox_id": "job-1", "generation": 1},
        )

        assert resp.status_code == 500
        assert "category_backfill_failed" in resp.json()["detail"]


class TestLocalDispatcherBackfill:
    async def test_routes_to_backfill(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(backfill_module, "backfill_category", mock)
        TestWorkerBackfill._allow_exact_intent(monkeypatch)

        await dispatch_local(
            "category_backfill",
            {"category_id": _TEST_CATEGORY_ID, "user_id": _TEST_USER_ID, "outbox_id": "job-1", "generation": 1},
        )

        mock.assert_awaited_once_with(_TEST_CATEGORY_ID, _TEST_USER_ID)

    async def test_missing_fields_no_crash(self) -> None:
        await dispatch_local("category_backfill", {})  # must not raise
