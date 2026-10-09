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
from pydantic import ValidationError

from app.api.v1.events import event_stream
from app.api.v1.notifications import TokenRequest, register_token
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


def _session_with(*batches: list[Any]) -> AsyncMock:
    """AsyncMock session whose execute() returns one batch per call, then empty."""
    session = AsyncMock()
    queue = [list(batch) for batch in batches]

    def _execute(*args: Any, **kwargs: Any) -> _FakeResult:
        if queue:
            return _FakeResult(queue.pop(0))
        return _FakeResult([])

    session.execute.side_effect = _execute
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


class TestRegisterToken:
    async def test_upsert_returns_ok_and_commits(self) -> None:
        session = _session_with([])
        resp = await register_token(
            body=TokenRequest(token="ExponentPushToken[abc]", platform="expo"),
            user=_user(),
            session=session,  # type: ignore[arg-type]
        )
        assert resp.ok is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()

    async def test_upsert_targets_user_token_conflict(self) -> None:
        session = _session_with([])
        await register_token(
            body=TokenRequest(token="tok-1", platform="ios"),
            user=_user(),
            session=session,  # type: ignore[arg-type]
        )
        stmt = session.execute.call_args[0][0]
        sql = str(stmt).upper()
        assert "ON CONFLICT" in sql and "DO UPDATE" in sql
        assert "PUSH_TOKENS" in sql


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
