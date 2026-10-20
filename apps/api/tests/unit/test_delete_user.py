"""Unit tests for account deletion (F-01): storage → vectors → DB → auth.

No DB, no network, no creds: storage/admin/pool/session are all faked or
mocked. Style mirrors tests/unit (plain fakes + monkeypatch).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.services.users.deletion as deletion_module
from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Profile
from app.main import create_app
from app.tasks.local_dispatcher import dispatch_local

_TEST_USER_ID = "123e4567-e89b-12d3-a456-426614174000"


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHttpClient:
    """Stands in for httpx.AsyncClient (async context manager)."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.deleted_urls: list[str] = []

    async def __aenter__(self) -> _FakeHttpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def delete(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        self.deleted_urls.append(url)
        return self._response


class _FakeSession:
    def __init__(self) -> None:
        self.executed: list[Any] = []
        self.commits = 0

    async def execute(self, stmt: Any) -> Any:
        self.executed.append(stmt)
        return None

    async def commit(self) -> None:
        self.commits += 1


class TestDeleteAuthUser:
    async def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeHttpClient(_FakeResponse(200, "{}"))
        monkeypatch.setattr(deletion_module.httpx, "AsyncClient", lambda **kw: client)

        await deletion_module.delete_auth_user(_TEST_USER_ID)

        assert len(client.deleted_urls) == 1
        assert client.deleted_urls[0].endswith(f"/auth/v1/admin/users/{_TEST_USER_ID}")

    async def test_404_is_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeHttpClient(_FakeResponse(404, "not found"))
        monkeypatch.setattr(deletion_module.httpx, "AsyncClient", lambda **kw: client)

        await deletion_module.delete_auth_user(_TEST_USER_ID)  # must not raise

    async def test_500_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeHttpClient(_FakeResponse(500, "boom"))
        monkeypatch.setattr(deletion_module.httpx, "AsyncClient", lambda **kw: client)

        with pytest.raises(RuntimeError, match="auth admin delete failed"):
            await deletion_module.delete_auth_user(_TEST_USER_ID)


class TestWorkerDeleteUser:
    @staticmethod
    def _allow_exact_intent(monkeypatch: pytest.MonkeyPatch) -> None:
        import app.tasks.worker_entry as worker_entry
        from app.tasks.worker_claims import TargetIntentResult

        monkeypatch.setattr(
            worker_entry, "claim_target_intent", AsyncMock(return_value=TargetIntentResult("claimed", "token"))
        )
        monkeypatch.setattr(worker_entry, "complete_target_intent", AsyncMock(return_value=True))

    def test_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(return_value={"status": "ok", "user_id": _TEST_USER_ID})
        # The handler resolves delete_user_account lazily from the service
        # module at call time — patch there.
        monkeypatch.setattr(deletion_module, "delete_user_account", mock)
        self._allow_exact_intent(monkeypatch)
        client = TestClient(create_app())

        resp = client.post("/work/delete_user", json={"user_id": _TEST_USER_ID, "outbox_id": "job-1", "generation": 1})

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "user_id": _TEST_USER_ID}
        assert mock.await_args.args[0] == _TEST_USER_ID
        execution = mock.await_args.args[1]
        assert execution.token == "token"
        assert execution.identity.outbox_id == "job-1"

    def test_error_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock(side_effect=RuntimeError("storage down"))
        monkeypatch.setattr(deletion_module, "delete_user_account", mock)
        self._allow_exact_intent(monkeypatch)
        client = TestClient(create_app())

        resp = client.post("/work/delete_user", json={"user_id": _TEST_USER_ID, "outbox_id": "job-1", "generation": 1})

        assert resp.status_code == 500
        assert "delete_user_failed" in resp.json()["detail"]


class TestLocalDispatcherDeleteUser:
    async def test_routes_to_purge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mock = AsyncMock()
        monkeypatch.setattr(deletion_module, "delete_user_account", mock)
        TestWorkerDeleteUser._allow_exact_intent(monkeypatch)

        await dispatch_local("delete_user", {"user_id": _TEST_USER_ID, "outbox_id": "job-1", "generation": 1})

        assert mock.await_args.args[0] == _TEST_USER_ID
        assert mock.await_args.args[1].identity.outbox_id == "job-1"

    async def test_missing_user_id_no_crash(self) -> None:
        await dispatch_local("delete_user", {})  # must not raise


class TestDeleteMeRoute:
    def test_enqueues_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.api.v1.users as users_module

        profile = Profile(id=_TEST_USER_ID, display_name="Test User")
        session = _FakeSession()

        async def _override_user() -> Profile:
            return profile

        async def _override_session() -> AsyncIterator[_FakeSession]:
            yield session  # type: ignore[misc]

        from types import SimpleNamespace

        mock_enqueue = AsyncMock()
        monkeypatch.setattr(users_module, "dispatch_after_commit", mock_enqueue)
        monkeypatch.setattr(
            users_module,
            "record_task_intent",
            AsyncMock(return_value=SimpleNamespace(id="outbox")),
        )

        app = create_app()
        app.dependency_overrides[_current_user] = _override_user
        app.dependency_overrides[get_session] = _override_session
        client = TestClient(app)

        resp = client.delete("/v1/me")

        assert resp.status_code == 202
        assert resp.json() == {"status": "scheduled"}
        assert profile.deleted_at is not None
        mock_enqueue.assert_awaited_once_with(["outbox"])
