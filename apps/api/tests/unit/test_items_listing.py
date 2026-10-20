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
from app.api.v1.items import _decode_items_cursor, _encode_items_cursor
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
    def __init__(
        self,
        rows: list[Item],
        *,
        total_count: int | None = None,
        page_results: list[list[Item]] | None = None,
        count_rows: list[Item] | None = None,
    ) -> None:
        self.rows = rows
        self.total_count = len(rows) if total_count is None else total_count
        self.page_results = page_results
        self.count_rows = count_rows
        self.page_index = 0
        self.statements: list[Any] = []
        self.count_statements: list[Any] = []

    async def execute(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        if self.page_results is None:
            rows = list(self.rows)
        else:
            rows = list(self.page_results[self.page_index])
            self.page_index += 1
        return _FakeResult(rows)

    async def scalar(self, stmt: Any) -> int:
        self.count_statements.append(stmt)
        if self.count_rows is not None:
            return sum(row.user_id == _TEST_USER_ID for row in self.count_rows)
        return self.total_count


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
        assert body["total_count"] == 1

    def test_malformed_cursor_returns_422(self) -> None:
        client = _client_with(_FakeSession([_make_item()]))

        resp = client.get("/v1/items", params={"cursor": "💥"})

        assert resp.status_code == 422
        assert resp.json()["detail"] == "invalid cursor"

    def test_cursor_is_stable_timestamp_and_id_keyset(self) -> None:
        saved_at = datetime(2026, 10, 2, tzinfo=UTC)
        cursor = _encode_items_cursor(saved_at, "item-z")
        decoded_at, decoded_id = _decode_items_cursor(cursor)
        assert (decoded_at, decoded_id) == (saved_at, "item-z")

        session = _FakeSession([])
        client = _client_with(session)

        resp = client.get("/v1/items", params={"cursor": cursor})

        assert resp.status_code == 200
        assert resp.json() == {"items": [], "next_cursor": None, "total_count": 0}
        rendered = str(session.statements[0])
        assert "saved_at" in rendered
        assert "items.id" in rendered
        assert "ORDER BY items.saved_at DESC, items.id DESC" in rendered

    def test_next_cursor_contains_tie_breaker(self) -> None:
        first = _make_item("item-z")
        second = _make_item("item-y")
        client = _client_with(_FakeSession([first, second]))

        resp = client.get("/v1/items", params={"limit": 1})

        assert resp.status_code == 200
        assert resp.json()["next_cursor"] == _encode_items_cursor(first.saved_at, first.id)

    def test_filtered_total_count_is_stable_across_cursor_pages_and_owner_scoped(self) -> None:
        first = _make_item("item-z")
        second = _make_item("item-y")
        other_owner = _make_item("foreign")
        other_owner.user_id = "user-2"
        session = _FakeSession(
            [first, second],
            total_count=2,
            page_results=[[first, second], [second]],
            count_rows=[first, second, other_owner],
        )
        client = _client_with(session)

        first_page = client.get(
            "/v1/items",
            params={"limit": 1, "platform": "instagram", "state": "fully_indexed"},
        )
        assert first_page.status_code == 200
        first_body = first_page.json()
        assert first_body["total_count"] == 2
        assert first_body["items"][0]["id"] == "item-z"

        second_page = client.get(
            "/v1/items",
            params={
                "limit": 1,
                "platform": "instagram",
                "state": "fully_indexed",
                "cursor": first_body["next_cursor"],
            },
        )
        assert second_page.status_code == 200
        assert second_page.json()["total_count"] == 2
        assert second_page.json()["items"][0]["id"] == "item-y"

        count_sql = str(session.count_statements[0])
        assert "items.user_id" in count_sql
        assert "items.platform" in count_sql
        assert "items.state" in count_sql
        assert "items.saved_at" not in count_sql
        assert "foreign" not in count_sql

    def test_empty_filtered_result_has_zero_count(self) -> None:
        client = _client_with(_FakeSession([], total_count=0))

        response = client.get("/v1/items", params={"category_id": "missing-category"})

        assert response.status_code == 200
        assert response.json() == {"items": [], "next_cursor": None, "total_count": 0}


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
