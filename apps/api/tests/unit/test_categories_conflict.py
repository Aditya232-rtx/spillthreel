"""Unit tests for POST /v1/categories error mapping (F-04).

IntegrityError (duplicate name) → 409; any other failure → 500 with the
original exception chained. No DB, no creds: faked session + mocked
enqueue. Style mirrors tests/unit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

import app.api.v1.categories as categories_module
from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Profile
from app.main import create_app

_TEST_USER_ID = "user-1"


class _FakeResult:
    def __init__(self, value: Any = 0) -> None:
        self._value = value

    def scalar_one(self) -> Any:
        return self._value


class _FakeSession:
    """Commit behavior is scripted per test via `commit_error`."""

    def __init__(self, commit_error: BaseException | None = None) -> None:
        self.commit_error = commit_error
        self.added: list[Any] = []
        self.rollbacks = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def execute(self, stmt: Any) -> _FakeResult:
        return _FakeResult(0)

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def refresh(self, obj: Any) -> None:
        return None


def _client_with(session: _FakeSession, monkeypatch: Any) -> tuple[TestClient, AsyncMock]:
    async def _override_user() -> Profile:
        return Profile(id=_TEST_USER_ID, display_name="Test User")

    async def _override_session() -> AsyncIterator[_FakeSession]:
        yield session

    mock_enqueue = AsyncMock()
    monkeypatch.setattr(categories_module, "enqueue_task", mock_enqueue)

    app = create_app()
    app.dependency_overrides[_current_user] = _override_user
    app.dependency_overrides[get_session] = _override_session
    return TestClient(app), mock_enqueue


class TestCreateCategoryErrors:
    def test_duplicate_name_returns_409(self, monkeypatch: Any) -> None:
        session = _FakeSession(
            commit_error=IntegrityError("INSERT ...", {"name": "X"}, Exception("duplicate"))
        )
        client, mock_enqueue = _client_with(session, monkeypatch)

        resp = client.post("/v1/categories", json={"name": "Dups"})

        assert resp.status_code == 409
        assert resp.json()["detail"] == "category name already exists"
        assert session.rollbacks == 1
        mock_enqueue.assert_not_awaited()

    def test_unexpected_error_returns_500(self, monkeypatch: Any) -> None:
        session = _FakeSession(commit_error=RuntimeError("connection lost"))
        client, mock_enqueue = _client_with(session, monkeypatch)

        resp = client.post("/v1/categories", json={"name": "Boom"})

        assert resp.status_code == 500
        assert resp.json()["detail"] == "category create failed"
        assert session.rollbacks == 1
        mock_enqueue.assert_not_awaited()

    def test_happy_path_still_201_and_backfills(self, monkeypatch: Any) -> None:
        session = _FakeSession()
        client, mock_enqueue = _client_with(session, monkeypatch)

        resp = client.post("/v1/categories", json={"name": "Hobby"})

        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "HOBBY"
        assert body["item_count"] == 0
        mock_enqueue.assert_awaited_once()
        args, _kwargs = mock_enqueue.call_args
        assert args[0] == "category_backfill"
        assert args[1]["user_id"] == _TEST_USER_ID
