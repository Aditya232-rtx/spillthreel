"""Unit tests for GET /v1/events (SSE) + POST /v1/notifications/token.

Style: endpoint functions are called directly with a stub Profile and an
AsyncMock session — no live DB, no creds. (There is no pre-existing
router-test pattern in tests/unit — router coverage lives in
tests/integration via TestClient — so this mirrors the repo's fast,
no-I/O unit style instead.)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from app.api.v1.events import event_stream
from app.api.v1.notifications import (
    TokenRequest,
    TokenUnregisterRequest,
    register_token,
    unregister_token,
)
from app.db.models import IngestionEvent, Profile

_USER_ID = "11111111-1111-1111-1111-111111111111"


def _user() -> Profile:
    return Profile(id=_USER_ID, display_name="Test User")


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

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


def _session_with(*batches: list[Any]) -> AsyncMock:
    """AsyncMock session whose execute() returns one batch per call, then empty."""
    session = AsyncMock()
    queue = [list(batch) for batch in batches]

    def _execute(*args: Any, **kwargs: Any) -> _FakeResult:
        if queue:
            return _FakeResult(queue.pop(0))
        return _FakeResult([])

    session.execute.side_effect = _execute
    session.scalar.return_value = _USER_ID
    return session


def _event(**kwargs: Any) -> IngestionEvent:
    row = IngestionEvent(
        id="evt-1",
        item_id="item-1",
        from_state="queued",
        to_state="downloading",
    )
    row.at = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
    for key, value in kwargs.items():
        setattr(row, key, value)
    return row


class TestTokenRequestValidation:
    def test_valid_platforms(self) -> None:
        for platform in ("expo", "android", "ios"):
            body = TokenRequest(token="ExponentPushToken[abc]", platform=platform)  # type: ignore[arg-type]
            assert body.platform == platform

    def test_empty_token_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenRequest(token="", platform="expo")

    def test_oversize_token_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenRequest(token="x" * 256, platform="ios")

    def test_max_length_token_accepted(self) -> None:
        assert len(TokenRequest(token="x" * 255, platform="ios").token) == 255

    def test_unknown_platform_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenRequest(token="abc", platform="web")  # type: ignore[arg-type]

    def test_invalid_previous_token_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TokenRequest(token="new", platform="ios", previous_token="")


class TestRegisterToken:
    async def test_upsert_returns_ok_and_commits(self) -> None:
        session = _session_with([(_USER_ID, "ExponentPushToken[abc]")])
        resp = await register_token(
            body=TokenRequest(token="ExponentPushToken[abc]", platform="expo"),
            user=_user(),
            session=session,  # type: ignore[arg-type]
        )
        assert resp.ok is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_upsert_targets_global_token_without_cross_owner_transfer(self) -> None:
        session = _session_with([(_USER_ID, "tok-1")])
        await register_token(
            body=TokenRequest(token="tok-1", platform="ios"),
            user=_user(),
            session=session,  # type: ignore[arg-type]
        )
        stmt = session.execute.call_args[0][0]
        compiled = stmt.compile(dialect=postgresql.dialect())
        sql = str(compiled).lower()
        assert "on conflict (token) do update" in sql
        assert "user_id =" in sql
        assert "platform =" in sql
        assert "created_at =" in sql
        assert "where push_tokens.user_id =" in sql
        assert "on conflict (user_id, token)" not in sql
        # The insert owner is the authenticated caller, while the conflict
        # predicate permits refresh only when the existing row has that owner.
        assert _USER_ID in compiled.params.values()

    async def test_rotation_removes_only_the_calling_users_old_token(self) -> None:
        session = _session_with([], [(_USER_ID, "ExponentPushToken[new]")])
        response = await register_token(
            body=TokenRequest(
                token="ExponentPushToken[new]",
                previous_token="ExponentPushToken[old]",
                platform="ios",
            ),
            user=_user(),
            session=session,  # type: ignore[arg-type]
        )

        assert response.ok is True
        assert session.execute.await_count == 2
        delete_sql = str(session.execute.call_args_list[0].args[0]).lower()
        assert "delete from push_tokens" in delete_sql
        assert "push_tokens.user_id" in delete_sql
        assert "push_tokens.token" in delete_sql
        upsert = session.execute.call_args_list[1].args[0]
        upsert_sql = str(upsert.compile(dialect=postgresql.dialect())).lower()
        assert "on conflict (token) do update" in upsert_sql
        session.commit.assert_awaited_once()

    async def test_same_token_rotation_does_not_delete_before_upsert(self) -> None:
        session = _session_with([(_USER_ID, "tok-1")])
        await register_token(
            body=TokenRequest(
                token="tok-1", previous_token="tok-1", platform="android"
            ),
            user=_user(),
            session=session,  # type: ignore[arg-type]
        )
        assert session.execute.await_count == 1

    async def test_conflict_owned_by_another_account_returns_409_and_rolls_back(self) -> None:
        session = _session_with([])
        with pytest.raises(HTTPException) as error:
            await register_token(
                body=TokenRequest(
                    token="tok-owned-by-someone-else",
                    previous_token="old-token",
                    platform="ios",
                ),
                user=_user(),
                session=session,  # type: ignore[arg-type]
            )

        assert error.value.status_code == 409
        assert error.value.detail["code"] == "push_token_owned_by_another_account"
        assert session.execute.await_count == 2
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()

    async def test_rejects_a_deleted_account_before_mutating_tokens(self) -> None:
        session = _session_with([])
        session.scalar.return_value = None
        with pytest.raises(HTTPException) as error:
            await register_token(
                body=TokenRequest(token="tok-1", platform="ios"),
                user=_user(),
                session=session,  # type: ignore[arg-type]
            )
        assert error.value.status_code == 403
        session.execute.assert_not_awaited()
        session.commit.assert_not_awaited()


class TestUnregisterToken:
    async def test_unregister_is_owner_scoped_and_idempotent(self) -> None:
        session = _session_with([])
        response = await unregister_token(
            body=TokenUnregisterRequest(token="ExponentPushToken[abc]"),
            user=_user(),
            session=session,  # type: ignore[arg-type]
        )

        assert response.ok is True
        stmt = session.execute.call_args.args[0]
        sql = str(stmt).lower()
        assert "delete from push_tokens" in sql
        assert "push_tokens.user_id" in sql
        assert "push_tokens.token" in sql
        session.commit.assert_awaited_once()

    async def test_missing_account_rejects_unregister(self) -> None:
        session = _session_with([])
        session.scalar.return_value = None
        with pytest.raises(HTTPException) as error:
            await unregister_token(
                body=TokenUnregisterRequest(token="tok-1"),
                user=_user(),
                session=session,  # type: ignore[arg-type]
            )
        assert error.value.status_code == 403
        session.execute.assert_not_awaited()


class TestEventStream:
    async def test_replay_emits_event_frame_first(self) -> None:
        session = _session_with([_event()])
        gen = event_stream(
            _USER_ID,
            session,
            poll_interval=0.01,
            heartbeat_interval=0.05,  # type: ignore[arg-type]
        )
        try:
            with anyio.fail_after(5):
                first = await gen.__anext__()
                second = await gen.__anext__()
        finally:
            await gen.aclose()

        assert first.startswith("id: evt-1\n")
        data_line = next(line for line in first.splitlines() if line.startswith("data:"))
        payload = json.loads(data_line.removeprefix("data:").strip())
        assert payload["id"] == "evt-1"
        assert payload["item_id"] == "item-1"
        assert payload["from_state"] == "queued"
        assert payload["to_state"] == "downloading"
        assert payload["detail"] is None
        assert payload["at"].startswith("2026-10-09T12:00:00")
        assert second == ": heartbeat\n\n"

    async def test_replay_scopes_to_owned_items(self) -> None:
        session = _session_with([_event()])
        gen = event_stream(
            _USER_ID,
            session,
            poll_interval=0.01,
            heartbeat_interval=0.05,  # type: ignore[arg-type]
        )
        try:
            with anyio.fail_after(5):
                await gen.__anext__()
        finally:
            await gen.aclose()

        stmt = session.execute.call_args_list[0][0][0]
        sql = str(stmt).lower()
        # Mirrors RLS `ingestion_events_via_item`: join items, filter owner.
        assert "join" in sql and "items" in sql and "user_id" in sql

    async def test_detail_passes_through(self) -> None:
        session = _session_with([_event(detail={"progress": 0.5})])
        gen = event_stream(
            _USER_ID,
            session,
            poll_interval=0.01,
            heartbeat_interval=0.05,  # type: ignore[arg-type]
        )
        try:
            with anyio.fail_after(5):
                first = await gen.__anext__()
        finally:
            await gen.aclose()

        data_line = next(line for line in first.splitlines() if line.startswith("data:"))
        assert json.loads(data_line.removeprefix("data:").strip())["detail"] == {"progress": 0.5}
