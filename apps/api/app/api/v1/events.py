"""GET /v1/events — SSE stream of ingestion state transitions.

Contract (v1):
  * JWT user only; streams `ingestion_events` for the user's own items.
    Ownership is enforced in SQL (JOIN items + user_id filter), mirroring
    the RLS policy `ingestion_events_via_item` — defense-in-depth next to
    the `request.jwt.claim.sub` claim set by the auth middleware.
  * Replay-first: the most recent `limit` rows (default 20, cap 50) are
    emitted oldest→newest, then the connection stays open and polls for
    new rows (`at > last_seen`) every ~2s.
  * Heartbeat `: heartbeat` comment every ~15s keeps idle connections
    (and load-balancer idle timeouts) happy.
  * Event frame: `id:` + `data:` JSON
    {id, item_id, from_state, to_state, detail?, at}.
  * Client disconnect ends the generator cleanly (CancelledError catch +
    `request.is_disconnected()` check each poll).

Follow-ups (not v1): Last-Event-ID resume, `retry:` hint, push fan-out.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import IngestionEvent, Item
from app.observability.logging import get_logger

router = APIRouter()
_logger = get_logger(__name__)

POLL_INTERVAL_SECONDS = 2.0
HEARTBEAT_INTERVAL_SECONDS = 15.0
DEFAULT_REPLAY_LIMIT = 20
MAX_REPLAY_LIMIT = 50

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class IngestionEventOut(BaseModel):
    id: str
    item_id: str
    from_state: str
    to_state: str
    detail: dict[str, Any] | None = None
    at: datetime


def _format_event(row: IngestionEvent) -> str:
    payload = IngestionEventOut(
        id=row.id,
        item_id=row.item_id,
        from_state=row.from_state,
        to_state=row.to_state,
        detail=row.detail,
        at=row.at,
    ).model_dump(mode="json")
    return f"id: {row.id}\ndata: {json.dumps(payload)}\n\n"


async def _fetch_recent(
    session: AsyncSession, user_id: str, since: datetime | None, limit: int
) -> list[IngestionEvent]:
    stmt = (
        select(IngestionEvent)
        .join(Item, Item.id == IngestionEvent.item_id)
        .where(Item.user_id == user_id)
        .order_by(desc(IngestionEvent.at))
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(IngestionEvent.at > since)
    rows = (await session.execute(stmt)).scalars().all()
    # Newest-first for the LIMIT, then flip so the client sees time order.
    return list(reversed(rows))


async def _fetch_newer(
    session: AsyncSession, user_id: str, last_seen: datetime
) -> list[IngestionEvent]:
    stmt = (
        select(IngestionEvent)
        .join(Item, Item.id == IngestionEvent.item_id)
        .where(Item.user_id == user_id, IngestionEvent.at > last_seen)
        .order_by(asc(IngestionEvent.at))
    )
    return list((await session.execute(stmt)).scalars().all())


async def event_stream(
    user_id: str,
    session: AsyncSession,
    *,
    since: datetime | None = None,
    limit: int = DEFAULT_REPLAY_LIMIT,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for `user_id`'s ingestion events.

    Public (not nested in the endpoint) so tests can drive it directly
    with short `poll_interval` / `heartbeat_interval` values.
    """
    if since is not None and since.tzinfo is None:
        # Treat a naive `since` as UTC so it always compares cleanly
        # against the TIMESTAMPTZ `at` column.
        since = since.replace(tzinfo=UTC)
    try:
        replay = await _fetch_recent(session, user_id, since, limit)
        if replay:
            last_seen = max(row.at for row in replay)
            for row in replay:
                yield _format_event(row)
        elif since is not None:
            last_seen = since
        else:
            # No history and no cursor: only future rows are live-polled.
            # (Microsecond race vs. the replay query above is accepted v1.)
            last_seen = datetime.now(UTC)

        last_beat = time.monotonic()
        while True:
            if is_disconnected is not None and await is_disconnected():
                _logger.info("events.client_gone", user_id=user_id)
                return
            for row in await _fetch_newer(session, user_id, last_seen):
                last_seen = max(last_seen, row.at)
                yield _format_event(row)
            if time.monotonic() - last_beat >= heartbeat_interval:
                yield ": heartbeat\n\n"
                last_beat = time.monotonic()
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        # Client went away mid-stream (Starlette cancels the generator) —
        # log and end cleanly instead of bubbling the error.
        _logger.info("events.disconnect", user_id=user_id)
        return


@router.get("/events")
async def stream_events(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_REPLAY_LIMIT)] = DEFAULT_REPLAY_LIMIT,
) -> StreamingResponse:
    stream = event_stream(
        user.id,
        session,
        since=since,
        limit=limit,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(stream, media_type="text/event-stream", headers=_SSE_HEADERS)
