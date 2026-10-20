"""Enhance worker — re-run the analyze→index stages for an existing item.

Lighter than the full ingest pipeline (`pipeline.py`): no download /
extract, no ffmpeg, no thumbnail upload. The row already carries text
(transcript / on_screen_text / caption / summary) — we re-summarize it
with Gemini, upsert the embedding row, and flip state → `fully_indexed`.

Mirrors `pipeline.py`'s stage shape and error handling:
  * row lock + transition to `indexing` (idempotent no-op when already
    `fully_indexed` — covers duplicate delivery after completion)
  * any failure lands in `items.failure_reason` + state `failed`, and the
    exception re-raises so Cloud Tasks retries with backoff
  * success hydrates the row + appends an `ingestion_events` audit row

Deliberately imports nothing from `pipeline.py`: that module pulls the
extractor registry (yt-dlp) at import time, which the api container
doesn't bundle. Every import here is api-safe; worker-only SDKs
(google-genai) stay deferred inside their own modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import ulid
from sqlalchemy import select

from app.db.engine import get_pg_pool, session_scope
from app.db.models import IngestionEvent, Item
from app.observability.logging import get_logger
from app.services.llm.base import StructuredSummary
from app.services.llm.gemini import GeminiSummaryModel
from app.services.memory.base import IndexedItem
from app.services.memory.pgvector_store import PgVectorStore
from app.tasks.worker_claims import (
    WorkerLease,
    finalize_worker_in_session,
    lease_is_current,
    renew_worker_lease,
)

_logger = get_logger(__name__)


class EnhanceError(RuntimeError):
    """Enhance step failed. Worker writes the message to `failure_reason`."""


@dataclass(frozen=True)
class _EnhanceSnapshot:
    """Detached copy of the row fields enhance needs — read inside the
    lock transaction so the expensive LLM + embed steps that follow don't
    hold a row lock (same reasoning as the pipeline's DB helpers)."""

    item_id: str
    user_id: str
    platform: str
    saved_at_iso: str
    duration_seconds: float | None
    summary: str | None
    transcript: str | None
    on_screen_text: str | None
    caption: str | None
    owner_name: str | None
    owner_username: str | None


async def enhance_item(item_id: str, lease: WorkerLease) -> None:
    """Re-run analyze→index for one item id.

    Idempotent-ish: already `fully_indexed` is a no-op (duplicate
    delivery after completion). Failures mark the row `failed` and
    re-raise so Cloud Tasks retries at the queue level.
    """
    async with session_scope() as session:
        if not await lease_is_current(session, lease):
            return
        stmt = select(Item).where(Item.id == item_id).with_for_update()
        row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise EnhanceError(f"item {item_id} not found")
        if row.state == "fully_indexed":
            # Duplicate delivery after a completed enhance — no-op.
            return
        prev_state = row.state
        row.state = "indexing"
        row.failure_reason = None
        session.add(
            IngestionEvent(
                id=str(ulid.new()),
                item_id=row.id,
                from_state=prev_state,
                to_state="indexing",
                detail={"trigger": "enhance"},
            )
        )
        await session.commit()
        snapshot = _EnhanceSnapshot(
            item_id=row.id,
            user_id=row.user_id,
            platform=row.platform,
            saved_at_iso=row.saved_at.isoformat(),
            duration_seconds=row.duration_seconds,
            summary=row.summary,
            transcript=row.transcript,
            on_screen_text=row.on_screen_text,
            caption=row.caption,
            owner_name=row.owner_name,
            owner_username=row.owner_username,
        )

    try:
        await _run(snapshot, lease)
    except Exception as e:
        _logger.exception("enhance.failed", item_id=item_id, err=str(e))
        await _mark_failed(item_id, str(e)[:500], lease)
        raise


async def _run(snapshot: _EnhanceSnapshot, lease: WorkerLease) -> None:
    # Analyze: the row's existing text is the whole input — no media to
    # re-download. Empty rows (nothing extracted yet) fail loudly with a
    # debuggable reason instead of embedding an empty corpus.
    source_text = "\n".join(
        part
        for part in [
            snapshot.transcript,
            snapshot.on_screen_text,
            snapshot.caption,
            snapshot.summary,
        ]
        if part
    )
    if not source_text.strip():
        raise EnhanceError("no text to enhance")
    if not await renew_worker_lease(lease):
        raise EnhanceError("worker lease lost")
    summary: StructuredSummary = await GeminiSummaryModel().summarize(
        frames=[], audio=None, transcript=source_text
    )

    # Index: same corpus shape as pipeline step 7 (minus extractor-only
    # fields — caption/owner come from the row snapshot instead).
    corpus_parts = [
        summary.title,
        summary.summary,
        summary.transcript,
        summary.on_screen_text,
        snapshot.caption or "",
    ]
    if summary.objects:
        corpus_parts.append("Objects: " + ", ".join(summary.objects))
    if summary.scenes:
        corpus_parts.append("Scenes: " + " | ".join(summary.scenes))
    if summary.topics:
        corpus_parts.append("Topics: " + ", ".join(summary.topics))
    if snapshot.owner_name:
        corpus_parts.append(f"Owner: {snapshot.owner_name}")
    text_corpus = "\n".join(part for part in corpus_parts if part)

    await PgVectorStore(await get_pg_pool()).write(
        user_id=snapshot.user_id,
        item=IndexedItem(
            item_id=snapshot.item_id,
            text_corpus=text_corpus,
            metadata={
                "item_id": snapshot.item_id,
                "platform": snapshot.platform,
                "saved_at": snapshot.saved_at_iso,
                "duration_seconds": float(
                    summary.duration_seconds or snapshot.duration_seconds or 0.0
                ),
                "sentiment": summary.sentiment.value,
                "primary_language": summary.primary_language,
                "topics": summary.topics,
                "owner_username": snapshot.owner_username,
            },
        ),
        worker_lease=lease,
    )

    # Hydrate the row + audit event (mirrors pipeline._mark_fully_indexed,
    # minus the thumbnail upload which enhance skips).
    async with session_scope() as session:
        if not await lease_is_current(session, lease):
            raise EnhanceError("worker lease lost before completion")
        item = (await session.execute(select(Item).where(Item.id == snapshot.item_id))).scalar_one()
        item_prev = item.state
        item.state = "fully_indexed"
        item.title = summary.title
        item.summary = summary.summary
        item.transcript = summary.transcript
        item.on_screen_text = summary.on_screen_text
        item.caption = item.caption or snapshot.caption
        item.duration_seconds = float(summary.duration_seconds or snapshot.duration_seconds or 0.0)
        item.failure_reason = None
        item.updated_at = datetime.now(UTC)
        session.add(
            IngestionEvent(
                id=str(ulid.new()),
                item_id=item.id,
                from_state=item_prev,
                to_state="fully_indexed",
                detail={"trigger": "enhance"},
            )
        )
        if not await finalize_worker_in_session(session, lease, status="completed"):
            raise EnhanceError("worker lease lost before completion")
        await session.commit()

    _logger.info("enhance.completed", item_id=snapshot.item_id, user_id=snapshot.user_id)


async def _mark_failed(item_id: str, reason: str, lease: WorkerLease) -> None:
    """Mirror of pipeline._mark_failed — kept local so this module never
    imports pipeline (which would drag yt-dlp onto the api image)."""
    async with session_scope() as session:
        if not await lease_is_current(session, lease):
            return
        item = (await session.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
        if item is None:
            return
        prev_state = item.state
        item.state = "failed"
        item.failure_reason = reason
        session.add(
            IngestionEvent(
                id=str(ulid.new()),
                item_id=item.id,
                from_state=prev_state,
                to_state="failed",
                detail={"reason": reason, "trigger": "enhance"},
            )
        )
        await session.commit()
