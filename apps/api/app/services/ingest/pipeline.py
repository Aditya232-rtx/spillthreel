"""Ingestion pipeline orchestrator — `ingest_item(item_id)`.

Called from the worker HTTP endpoint after a Cloud Task fires. Runs the
canonical 11-step flow documented in architecture.md §4.1:

  1. SELECT items.state, transition to 'downloading'.
  2. Extractor registry: try Cobalt → yt-dlp.
  3. ffmpeg: probe, sample frames, extract audio, thumbnail.
  4. Long-audio branch: Groq Whisper transcribe if duration ≥ 5 min.
  5. Gemini multimodal summarize (short) or transcript-informed (long).
  6. Upload thumbnail to Supabase Storage under `{user_id}/{item_id}.jpg`.
  7. MemoryStore.write() into user's Cognee namespace.
  8. UPDATE items: state='fully_indexed', hydrate summary/transcript/etc.
  9. INSERT ingestion_events row.
 10. Emit SSE event on the user's channel (Phase 3 — stubbed here).
 11. Send Expo push if user is backgrounded (Phase 3 — stubbed here).

Any step can raise. All failures land in `items.failure_reason` and drive
state → 'failed' after the queue's 3 retries are exhausted.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from app.db.engine import session_scope
from app.db.models import IngestionEvent, Item
from app.observability.logging import get_logger
from app.services.extractors.registry import extract as extractor_extract
from app.services.llm.base import AudioTrack, Frame
from app.services.llm.gemini import GeminiSummaryModel
from app.services.llm.groq_whisper import GroqWhisperModel
from app.services.media import ffmpeg
from app.services.media.supabase_storage import (
    signed_url as storage_signed_url,
    upload_object as storage_upload,
)
from app.services.memory.base import IndexedItem
from app.services.memory.cognee_cloud import CogneeCloudStore
from app.settings import get_settings

_logger = get_logger(__name__)

# PRD F2.3: content ≥ this duration branches to Groq for transcription
# instead of shipping the audio to Gemini as multimodal input.
LONG_AUDIO_THRESHOLD_SECONDS = 300.0

# Long-form YT branch — one frame every 10s instead of every 2s (F2.4).
LONG_FRAME_INTERVAL_SECONDS = 10.0


class PipelineError(RuntimeError):
    """Any step failed. Caller writes the message to `failure_reason`."""


@dataclass(frozen=True)
class _RunContext:
    item_id: str
    user_id: str
    workdir: Path


async def ingest_item(item_id: str) -> None:
    """Full pipeline for a single item id.

    Idempotent-ish: if the item is already `fully_indexed` we no-op.
    Failures let the exception propagate so Cloud Tasks retries at the
    queue level (exponential backoff, 3 attempts before failed).
    """
    async with session_scope() as session:
        item = await _lock_and_transition_downloading(session, item_id)

    workdir = Path(tempfile.mkdtemp(prefix=f"spill-ingest-{item.id}-"))
    ctx = _RunContext(item_id=item.id, user_id=item.user_id, workdir=workdir)

    try:
        await _run(ctx, item)
    except Exception as e:
        _logger.exception("ingest.failed", item_id=item.id, err=str(e))
        await _mark_failed(item.id, str(e)[:500])
        raise
    finally:
        # TRD §15.7 — source video destroyed within 15 min. Workdir has
        # ffmpeg intermediate frames + audio too; nuke it.
        shutil.rmtree(workdir, ignore_errors=True)


async def _run(ctx: _RunContext, item: Item) -> None:
    settings = get_settings()

    # Step 2: extract media.
    await _emit_event(item.id, "downloading", "analyzing")
    result = await extractor_extract(item.source_url)
    if not result.video_path:
        raise PipelineError("extractor returned no video path")

    # Step 3: probe + frame sample + audio extract + thumbnail.
    probe = await ffmpeg.probe(result.video_path)

    is_long = probe.duration_seconds >= LONG_AUDIO_THRESHOLD_SECONDS
    frame_interval = LONG_FRAME_INTERVAL_SECONDS if is_long else ffmpeg.DEFAULT_FRAME_INTERVAL_SECONDS
    frames_dir = ctx.workdir / "frames"
    frame_paths = await ffmpeg.sample_frames(
        result.video_path, frames_dir, interval_seconds=frame_interval
    )
    frames = [Frame(path=p, timestamp_seconds=i * frame_interval)
              for i, p in enumerate(frame_paths)]

    audio_path = ctx.workdir / "audio.wav" if probe.has_audio else None
    audio_track: AudioTrack | None = None
    if audio_path is not None:
        await ffmpeg.extract_audio(result.video_path, audio_path)
        audio_track = AudioTrack(path=audio_path, duration_seconds=probe.duration_seconds)

    thumb_path = ctx.workdir / "thumb.jpg"
    await ffmpeg.make_thumbnail(result.video_path, thumb_path)

    # Step 4-5: long-audio branch → Whisper transcribe; short → Gemini
    # multimodal handles ASR inline.
    pretranscript: str | None = None
    if is_long and audio_track is not None:
        pretranscript = await GroqWhisperModel().transcribe(audio_track)
        # Long content: pass transcript as text, drop the raw audio from
        # the Gemini payload to control cost.
        summary_audio: AudioTrack | None = None
    else:
        summary_audio = audio_track

    summary = await GeminiSummaryModel().summarize(
        frames=frames, audio=summary_audio, transcript=pretranscript
    )

    # Step 6: upload thumbnail to Supabase Storage.
    #   Object key: {user_id}/{item_id}.jpg — the Storage RLS policies
    #   require this exact prefix for cross-user isolation.
    thumb_key = f"{ctx.user_id}/{ctx.item_id}.jpg"
    await storage_upload(
        bucket=settings.supabase_bucket_media,
        object_key=thumb_key,
        source=thumb_path,
        content_type="image/jpeg",
    )
    thumbnail_url = await storage_signed_url(
        bucket=settings.supabase_bucket_media,
        object_key=thumb_key,
        ttl_seconds=60 * 60 * 24 * 30,  # 30d — matches Cloud CDN cache TTL
    )

    # Step 7: write into Cognee under the user's namespace.
    text_corpus = "\n".join(
        filter(
            None,
            [
                summary.title,
                summary.summary,
                summary.transcript,
                summary.on_screen_text,
                result.original_caption or "",
                "Objects: " + ", ".join(summary.objects) if summary.objects else "",
                "Scenes: " + " | ".join(summary.scenes) if summary.scenes else "",
                "Topics: " + ", ".join(summary.topics) if summary.topics else "",
                f"Owner: {result.original_owner.name}" if result.original_owner else "",
            ],
        )
    )
    cognee_id = await CogneeCloudStore().write(
        user_id=ctx.user_id,
        item=IndexedItem(
            item_id=ctx.item_id,
            text_corpus=text_corpus,
            metadata={
                "item_id": ctx.item_id,
                "platform": item.platform,
                "saved_at": item.saved_at.isoformat(),
                "duration_seconds": probe.duration_seconds,
                "sentiment": summary.sentiment.value,
                "primary_language": summary.primary_language,
                "topics": summary.topics,
                "owner_username": (
                    result.original_owner.username if result.original_owner else None
                ),
            },
        ),
    )

    # Step 8-9: hydrate the row + append audit event.
    await _mark_fully_indexed(
        item_id=ctx.item_id,
        summary=summary,
        thumbnail_url=thumbnail_url,
        owner_name=result.original_owner.name if result.original_owner else None,
        owner_username=result.original_owner.username if result.original_owner else None,
        owner_url=result.original_owner.url if result.original_owner else None,
        caption=result.original_caption,
        cognee_id=cognee_id,
    )

    # Steps 10-11 (SSE + push): Phase 3 — logging placeholder here so
    # transitions are still visible during pre-Phase-3 dev.
    _logger.info(
        "ingest.completed",
        item_id=ctx.item_id,
        user_id=ctx.user_id,
        duration_seconds=probe.duration_seconds,
        model_frames=len(frames),
        transcript_source="groq" if pretranscript else "gemini",
    )


# ---------------------------------------------------------------------------
# DB helpers — each one runs in its own short transaction so the pipeline's
# expensive middle steps don't hold a row lock while ffmpeg + Gemini run.
# ---------------------------------------------------------------------------


async def _lock_and_transition_downloading(session: AsyncSession, item_id: str) -> Item:
    stmt = select(Item).where(Item.id == item_id).with_for_update()
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise PipelineError(f"item {item_id} not found")
    if item.state == "fully_indexed":
        # Idempotent — return without doing anything.
        return item

    prev_state = item.state
    item.state = "downloading"
    session.add(
        IngestionEvent(
            id=str(ULID()),
            item_id=item.id,
            from_state=prev_state,
            to_state="downloading",
            detail=None,
        )
    )
    await session.commit()
    return item


async def _emit_event(item_id: str, from_state: str, to_state: str, detail: dict[str, Any] | None = None) -> None:
    async with session_scope() as session:
        # Also flip items.state at the same time — the state machine is
        # kept in-sync with the audit trail per architecture.md §4.1.
        item = (await session.execute(select(Item).where(Item.id == item_id))).scalar_one()
        item.state = to_state
        session.add(
            IngestionEvent(
                id=str(ULID()),
                item_id=item.id,
                from_state=from_state,
                to_state=to_state,
                detail=detail,
            )
        )
        await session.commit()


async def _mark_failed(item_id: str, reason: str) -> None:
    async with session_scope() as session:
        item = (await session.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
        if item is None:
            return
        prev_state = item.state
        item.state = "failed"
        item.failure_reason = reason
        session.add(
            IngestionEvent(
                id=str(ULID()),
                item_id=item.id,
                from_state=prev_state,
                to_state="failed",
                detail={"reason": reason},
            )
        )
        await session.commit()


async def _mark_fully_indexed(
    item_id: str,
    summary: Any,
    thumbnail_url: str,
    owner_name: str | None,
    owner_username: str | None,
    owner_url: str | None,
    caption: str | None,
    cognee_id: str,
) -> None:
    async with session_scope() as session:
        item = (await session.execute(select(Item).where(Item.id == item_id))).scalar_one()
        prev_state = item.state
        item.state = "fully_indexed"
        item.title = summary.title
        item.summary = summary.summary
        item.transcript = summary.transcript
        item.on_screen_text = summary.on_screen_text
        item.caption = item.caption or caption
        item.owner_name = item.owner_name or owner_name
        item.owner_username = item.owner_username or owner_username
        item.owner_url = item.owner_url or owner_url
        item.duration_seconds = float(summary.duration_seconds or 0.0)
        item.thumbnail_url = thumbnail_url
        item.cognee_id = cognee_id
        item.updated_at = datetime.now(UTC)
        session.add(
            IngestionEvent(
                id=str(ULID()),
                item_id=item.id,
                from_state=prev_state,
                to_state="fully_indexed",
                detail={"cognee_id": cognee_id},
            )
        )
        await session.commit()
