"""Unit tests for GET /v1/items listing (F-05 + F-06).

Covers: malformed cursor → 422 (never 500), and the newly implemented
`category_id` / `collection_id` filters (EXISTS over item_categories /
collection_items). No DB, no creds: faked session records the SQL so the
filter wiring itself is asserted, not just the status code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Item, Profile
from app.main import create_app

_TEST_USER_ID = "user-1"


def _make_item(item_id: str = "item-1") -> Item:
    return Item(
        id=item_id,
        user_id=_TEST_USER_ID,
        source_url="https://www.instagram.com/reel/DbM7eWINqAy/",
        source_url_norm="https://instagram.com/reel/DbM7eWINqAy",
        platform="instagram",
        state="fully_indexed",
        saved_at=datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC),
        source_type="share_extension",
        title="Test reel",
    )


class _FakeScalars:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows: list[Item]) -> None:
        self.rows = rows
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult(list(self.rows))


def _client_with(session: _FakeSession) -> TestClient:
    async def _override_user() -> Profile:
        return Profile(id=_TEST_USER_ID, display_name="Test User")

    async def _override_session() -> AsyncIterator[_FakeSession]:
        yield session

    app = create_app()
    app.dependency_overrides[_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    return TestClient(app)


class TestListItemsCursor:
    def test_happy_path(self) -> None:
        client = _client_with(_FakeSession([_make_item()]))

        resp = client.get("/v1/items")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["id"] == "item-1"

    def test_malformed_cursor_returns_422(self) -> None:
        client = _client_with(_FakeSession([_make_item()]))

        resp = client.get("/v1/items", params={"cursor": "not-a-datetime"})

        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid cursor"

    def test_valid_cursor_filters(self) -> None:
        session = _FakeSession([])
        client = _client_with(session)

        resp = client.get("/v1/items", params={"cursor": "2026-10-02T00:00:00+00:00"})

        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_cursor": None}
        assert "saved_at" in str(session.statements[0])


class TestListItemsFilters:
    def test_category_filter_hits_item_categories(self) -> None:
        session = _FakeSession([_make_item()])
        client = _client_with(session)

        resp = client.get("/v1/items", params={"category_id": "cat-1"})

        assert resp.status_code == 200
        assert "item_categories" in str(session.statements[0])

    def test_collection_filter_hits_collection_items(self) -> None:
        session = _FakeSession([_make_item()])
        client = _client_with(session)

        resp = client.get("/v1/items", params={"collection_id": "col-1"})

        assert resp.status_code == 200
        assert "collection_items" in str(session.statements[0])

    def test_no_filters_no_joins(self) -> None:
        session = _FakeSession([_make_item()])
        client = _client_with(session)

        resp = client.get("/v1/items")

        assert resp.status_code == 200
        rendered = str(session.statements[0])
        assert "item_categories" not in rendered
        assert "collection_items" not in rendered
