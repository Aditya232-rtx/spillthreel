"""Ingestion pipeline orchestrator — `ingest_item(item_id)`.

Called from the worker HTTP endpoint after a Cloud Task fires. Runs the
canonical 11-step flow documented in architecture.md §4.1:

  1. SELECT items.state, transition to 'downloading'.
   2. Extractor registry: per-platform chain
      (scraper → yt-dlp → gallery-dl; YouTube goes native first).
  3. ffmpeg: probe, sample frames, extract audio, thumbnail.
  4. Long-audio branch: Groq Whisper transcribe if duration ≥ 5 min.
  5. Gemini multimodal summarize (short) or transcript-informed (long).
  6. Upload thumbnail to Supabase Storage under `{user_id}/{item_id}.jpg`.
  7. MemoryStore.write() into pgvector (user-scoped embedding row).
  8. UPDATE items: state='fully_indexed', hydrate summary/transcript/etc.
  9. INSERT ingestion_events row.
 10. Emit SSE event on the user's channel (Phase 3 — stubbed here).
 11. Send Expo push if user is backgrounded (Phase 3 — stubbed here).

Any step can raise. All failures land in `items.failure_reason` and drive
state → 'failed' after the queue's 3 retries are exhausted.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import ulid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_pg_pool, session_scope
from app.db.models import IngestionEvent, Item
from app.observability.logging import get_logger
from app.services.extractors.base import ExtractorError, ExtractResult
from app.services.extractors.registry import extract as extractor_extract
from app.services.extractors.scraper.base import SourceGoneError
from app.services.ingest.platform_detect import is_shorts_url
from app.services.llm.base import AudioTrack, Frame, StructuredSummary
from app.services.llm.gemini import GeminiSummaryModel
from app.services.llm.groq_whisper import GroqWhisperModel
from app.services.media import ffmpeg
from app.services.media.supabase_storage import (
    signed_url as storage_signed_url,
)
from app.services.media.supabase_storage import (
    upload_object as storage_upload,
)
from app.services.memory.base import IndexedItem
from app.services.memory.pgvector_store import PgVectorStore
from app.settings import get_settings

_logger = get_logger(__name__)

# PRD F2.3: content ≥ this duration branches to Groq for transcription
# instead of shipping the audio to Gemini as multimodal input.
LONG_AUDIO_THRESHOLD_SECONDS = 300.0

# Long-form YT branch — one frame every 10s instead of every 2s (F2.4).
LONG_FRAME_INTERVAL_SECONDS = 10.0


# ---------------------------------------------------------------------------
# YouTube native path (Part E) — no download, no ffmpeg, no registry.
#
# Gemini takes a PUBLIC YouTube URL as a FileData video part, so for
# youtube items we skip extraction/transcoding entirely: oEmbed (+ Data
# API v3 when keyed) for metadata/guardrails, thumbnail via the shared
# downloader, summarize_youtube for the multimodal pass, then the SHARED
# _finish_indexed tail below.
# ---------------------------------------------------------------------------

# Default cap when the duration is known (Data API). Shorts are ≤ 3 min
# by platform rule, so the default comfortably covers them.
_YOUTUBE_DEFAULT_MAX_DURATION_SECONDS = 600.0

_YOUTUBE_HTTP_TIMEOUT_SECONDS = 10.0
_YOUTUBE_OEMBED_URL = "https://www.youtube.com/oembed"
_YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}\Z")


class PipelineError(RuntimeError):
    """Any step failed. Caller writes the message to `failure_reason`."""


class _YouTubeSourceGoneError(PipelineError):
    """The video is private/unlisted/deleted/age-restricted.

    Caught in _run → state 'source_gone' (not 'failed', no point retrying:
    the bytes will never become fetchable). Message is ALWAYS the
    machine-readable code "youtube_not_public" so sibling D's failure
    mapping can match on it.
    """


@dataclass(frozen=True)
class _RunContext:
    item_id: str
    user_id: str
    workdir: Path


@dataclass(frozen=True)
class _YouTubeMetadata:
    title: str | None
    channel: str | None
    channel_url: str | None
    description: str | None
    duration_seconds: float | None


def _youtube_native_enabled() -> bool:
    """Whether YouTube items take the native path (default true).

    Backed by settings ``youtube_native_enabled`` (env
    ``YOUTUBE_NATIVE_ENABLED``). `false` falls through to the extractor
    registry below — for YouTube that chain is ``[YtDlpExtractor]``.
    """
    raw = getattr(get_settings(), "youtube_native_enabled", None)
    if raw is None:
        raw = os.getenv("YOUTUBE_NATIVE_ENABLED", "true")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _youtube_api_key() -> str | None:
    """Data API v3 key (optional), from settings ``youtube_api_key``."""
    raw = getattr(get_settings(), "youtube_api_key", None)
    if raw:
        return str(raw)
    return os.getenv("YOUTUBE_API_KEY") or None


def _youtube_max_duration_seconds() -> float:
    """Cost cap from settings ``youtube_max_duration_seconds`` (default 600)."""
    raw = getattr(get_settings(), "youtube_max_duration_seconds", None)
    if raw is None:
        raw = os.getenv("YOUTUBE_MAX_DURATION_SECONDS", "600")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _YOUTUBE_DEFAULT_MAX_DURATION_SECONDS


def _extract_youtube_id(url: str) -> str | None:
    """Best-effort video-id parse for watch / shorts / youtu.be / embed / live."""
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    host = parsed.netloc.lower().rsplit("@", 1)[-1].split(":", 1)[0]
    segments = [s for s in parsed.path.split("/") if s]

    candidate: str | None = None
    if re.search(r"(?:^|\.)youtube\.com$", host):
        values = parse_qs(parsed.query).get("v")
        if values and values[0] and _YT_ID_RE.match(values[0].strip()):
            return values[0].strip()
        if len(segments) >= 2 and segments[0] in ("shorts", "embed", "live", "v"):
            candidate = segments[1]
        elif len(segments) >= 2 and segments[0] == "watch":
            candidate = None
    elif re.search(r"(?:^|\.)youtu\.be$", host):
        if segments:
            candidate = segments[0]

    if candidate is not None:
        candidate = candidate.strip()
        if _YT_ID_RE.match(candidate):
            return candidate
    return None


_ISO8601_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)


def _parse_iso8601_duration(raw: Any) -> float | None:
    """Parse a Data API contentDetails.duration (e.g. "PT4M30S") to seconds."""
    if not isinstance(raw, str) or not raw:
        return None
    match = _ISO8601_DURATION_RE.match(raw.strip())
    if match is None:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


async def _fetch_oembed(canonical_url: str) -> _YouTubeMetadata:
    """Title/channel via oEmbed — no API key needed.

    401/403/404 from oEmbed means the video is not publicly resolvable
    (private/deleted) → source_gone. Anything else non-2xx is a
    transport-side failure → PipelineError (retryable via failed state).
    """
    async with httpx.AsyncClient(timeout=_YOUTUBE_HTTP_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.get(
                _YOUTUBE_OEMBED_URL, params={"url": canonical_url, "format": "json"}
            )
        except httpx.HTTPError as e:
            raise PipelineError(f"youtube oembed request failed: {e}") from e
    if resp.status_code in (401, 403, 404):
        raise _YouTubeSourceGoneError("youtube_not_public")
    if resp.status_code >= 400:
        raise PipelineError(f"youtube oembed failed: HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise PipelineError(f"youtube oembed returned invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise PipelineError("youtube oembed returned unexpected payload")
    return _YouTubeMetadata(
        title=data.get("title"),
        channel=data.get("author_name"),
        channel_url=data.get("author_url"),
        description=None,
        duration_seconds=None,
    )


async def _fetch_video_details(video_id: str, api_key: str) -> _YouTubeMetadata | None:
    """videos.list(snippet,contentDetails,status) — duration/description/privacy.

    Returns None when the API itself is unreachable (quota/transport) so
    the caller degrades to the oEmbed-only unknown-duration path. An EMPTY
    items list is definitive (deleted/private/inaccessible) → source_gone.
    Fields per the videos.list docs: snippet.title/description/channelTitle,
    contentDetails.duration (ISO 8601), status.privacyStatus/uploadStatus.
    """
    async with httpx.AsyncClient(timeout=_YOUTUBE_HTTP_TIMEOUT_SECONDS) as client:
        try:
            resp = await client.get(
                _YOUTUBE_VIDEOS_URL,
                params={
                    "part": "snippet,contentDetails,status",
                    "id": video_id,
                    "key": api_key,
                },
            )
        except httpx.HTTPError as e:
            _logger.warning("youtube.data_api_unreachable", err=str(e))
            return None
    if resp.status_code >= 400:
        _logger.warning(
            "youtube.data_api_error",
            status=resp.status_code,
            body=resp.text[:200] if hasattr(resp, "text") else "",
        )
        return None
    try:
        data = resp.json()
    except ValueError as e:
        _logger.warning("youtube.data_api_bad_json", err=str(e))
        return None
    items = data.get("items", []) if isinstance(data, dict) else []
    if not items:
        raise _YouTubeSourceGoneError("youtube_not_public")
    video = items[0] if isinstance(items[0], dict) else {}

    status = video.get("status", {})
    if not isinstance(status, dict):
        status = {}
    privacy = status.get("privacyStatus")
    upload = status.get("uploadStatus")
    # Only "public" videos are summarizable via the Gemini YouTube-URL
    # input (docs: "Public YouTube videos"); unlisted/private/deleted or
    # failed/rejected uploads can never resolve → source_gone.
    if privacy != "public" or upload in ("deleted", "failed", "rejected"):
        raise _YouTubeSourceGoneError("youtube_not_public")

    content = video.get("contentDetails", {})
    if not isinstance(content, dict):
        content = {}
    rating = content.get("contentRating", {})
    # Age-gated videos are not playable anonymously and Gemini refuses
    # them as URL input — same terminal bucket.
    if isinstance(rating, dict) and rating.get("ytRating") == "ytAgeRestricted":
        raise _YouTubeSourceGoneError("youtube_not_public")

    snippet = video.get("snippet", {})
    if not isinstance(snippet, dict):
        snippet = {}
    channel_id = snippet.get("channelId")
    return _YouTubeMetadata(
        title=snippet.get("title"),
        channel=snippet.get("channelTitle"),
        channel_url=f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
        description=snippet.get("description"),
        duration_seconds=_parse_iso8601_duration(content.get("duration")),
    )


async def _download_youtube_thumbnail(thumb_url: str, dest_dir: Path) -> Path:
    """Thumbnail fetch via the CTO-decreed shared downloader.

    CTO contract (sibling C: services/media/download.py)::

        from services.media.download import download_media
        download_media(url, dest_dir, *, kind="video", max_mb=None, timeout_s=60.0) -> Path

    Import path + kwarg shape match exactly. One deliberate deviation:
    ``kind="image"`` (not "video") — download_media enforces the response
    content-type by kind, and YouTube thumbnails serve image/jpeg, so
    kind="video" would reject every thumbnail. Static import is deferred
    into this function so the non-YouTube pipeline never imports the
    downloader at module load.

    NOTE (allow-list, sibling C): the downloader's default allow-list
    already covers the YouTube thumbnail CDNs (ytimg.com, youtube.com,
    youtu.be, ggpht.com) — keep them allow-listed or every native-path
    item fails here.
    """
    from app.services.media.download import download_media

    try:
        res: Any = download_media(thumb_url, dest_dir, kind="image", max_mb=None, timeout_s=60.0)
        # download_media is async in the sibling-C implementation; the
        # isawaitable branch keeps us compatible if that ever changes.
        thumb_path: Path = await res if inspect.isawaitable(res) else res
        return thumb_path
    except ExtractorError as e:
        raise PipelineError(f"youtube_thumbnail_failed: {e}") from e


async def _run_youtube_native(ctx: _RunContext, item: Item) -> None:
    """YouTube native ingest: metadata → guardrails → thumbnail → Gemini URL input."""
    source_url = item.source_url
    video_id = _extract_youtube_id(source_url)
    # Gemini's docs use the canonical watch form; shorts/youtu.be ids
    # resolve to the same video, so normalize before sending.
    canonical_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else source_url

    await _emit_event(item.id, "downloading", "analyzing")

    oembed = await _fetch_oembed(canonical_url)

    details: _YouTubeMetadata | None = None
    api_key = _youtube_api_key()
    if api_key and video_id:
        details = await _fetch_video_details(video_id, api_key)
    elif api_key and not video_id:
        _logger.warning("youtube.no_video_id_for_data_api", url=source_url)

    title = (details.title if details else None) or oembed.title
    channel = (details.channel if details else None) or oembed.channel
    channel_url = (details.channel_url if details else None) or oembed.channel_url
    description = details.description if details else None
    duration = details.duration_seconds if details else None

    max_duration = _youtube_max_duration_seconds()
    if duration is not None and duration > max_duration:
        raise PipelineError(
            f"youtube_too_long: video is {duration:.0f}s, "
            f"cap is {max_duration:.0f}s (YOUTUBE_MAX_DURATION_SECONDS)"
        )
    if duration is None and not is_shorts_url(source_url):
        # Without the Data API we cannot meter cost before the paid Gemini
        # call — only /shorts/ links are provably short (≤ 3 min platform
        # cap, under the default 600s budget), so anything else fails loud.
        raise PipelineError(
            "youtube_duration_unknown: no YOUTUBE_API_KEY to verify duration "
            "and URL is not a /shorts/ link"
        )

    if not video_id:
        raise PipelineError("youtube_no_video_id: could not parse a video id from URL")

    thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    thumb_path = await _download_youtube_thumbnail(thumb_url, ctx.workdir)

    summary = await GeminiSummaryModel().summarize_youtube(
        canonical_url,
        title=title,
        channel=channel,
        description=description,
        duration_seconds=duration,
    )
    if duration is not None:
        # Pin the row to the metered duration (not the model's estimate)
        # so cost/cap accounting stays exact.
        summary.duration_seconds = duration

    await _finish_indexed(
        ctx,
        item=item,
        summary=summary,
        thumb_path=thumb_path,
        caption=description,
        owner_name=channel,
        owner_username=None,
        owner_url=channel_url,
        duration_seconds=duration if duration is not None else float(summary.duration_seconds),
        transcript_source="gemini-youtube",
        model_frames=0,
    )


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
    # YouTube native path FIRST: no download, no ffmpeg, no extractor
    # registry. Gemini reads the public URL server-side.
    #
    # NOTE on the fallback below: yt-dlp from datacenter/cloud IPs is
    # unreliable for YouTube (bot-check walls / HTTP 403 / "Sign in to
    # confirm you're not a bot", no cookies or PO tokens in v1) — which
    # is exactly why the native path exists. YOUTUBE_NATIVE_ENABLED=false
    # keeps the registry chain (for YouTube: [YtDlpExtractor], verified
    # in test_flag_off_falls_back_to_legacy_chain) for debugging, not
    # for production traffic.
    if item.platform == "youtube" and _youtube_native_enabled():
        try:
            await _run_youtube_native(ctx, item)
        except _YouTubeSourceGoneError as e:
            await _mark_source_gone(item.id, str(e) or "youtube_not_public")
        return

    # Step 2: extract media.
    await _emit_event(item.id, "downloading", "analyzing")
    try:
        result = await extractor_extract(item.source_url)
    except SourceGoneError as e:
        # Part D: deleted/private/not-found → terminal `source_gone`
        # (not `failed`; Cloud Tasks must not retry it into existence).
        await _mark_source_gone(item.id, str(e)[:500] or "source_gone")
        return
    if result.video_path is None and result.image_paths:
        # Image branch (scraper / gallery-dl photo posts): analyze the
        # images as Gemini frames, then rejoin the shared tail below.
        await _run_image_branch(ctx, item, result)
        return
    if not result.video_path:
        raise PipelineError("extractor returned no video path")

    # Step 3: probe + frame sample + audio extract + thumbnail.
    probe = await ffmpeg.probe(result.video_path)

    is_long = probe.duration_seconds >= LONG_AUDIO_THRESHOLD_SECONDS
    frame_interval = (
        LONG_FRAME_INTERVAL_SECONDS if is_long else ffmpeg.DEFAULT_FRAME_INTERVAL_SECONDS
    )
    frames_dir = ctx.workdir / "frames"
    frame_paths = await ffmpeg.sample_frames(
        result.video_path, frames_dir, interval_seconds=frame_interval
    )
    frames = [
        Frame(path=p, timestamp_seconds=i * frame_interval) for i, p in enumerate(frame_paths)
    ]

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

    await _finish_indexed(
        ctx,
        item=item,
        summary=summary,
        thumb_path=thumb_path,
        caption=result.original_caption,
        owner_name=result.original_owner.name if result.original_owner else None,
        owner_username=result.original_owner.username if result.original_owner else None,
        owner_url=result.original_owner.url if result.original_owner else None,
        duration_seconds=probe.duration_seconds,
        transcript_source="groq" if pretranscript else "gemini",
        model_frames=len(frames),
    )


# Carousel cap for the image branch: the scraper / gallery-dl extractors
# already cap at 10 (TRD §7.5 bounds tmpfs + Gemini frame cost); re-cap
# defensively so a future extractor can't blow up the multimodal payload.
_MAX_IMAGE_FRAMES = 10


async def _run_image_branch(ctx: _RunContext, item: Item, result: ExtractResult) -> None:
    """Photo-post ingest: images → Gemini frames → shared indexed tail.

    Covers single photos and carousels (up to ``_MAX_IMAGE_FRAMES``).
    No ffmpeg (nothing to probe/sample), no Whisper (no audio track) —
    Gemini summarizes the frames directly with ``audio=None`` /
    ``transcript=None``. The first image doubles as the thumbnail for the
    shared tail (known limitation: the tail uploads it as image/jpeg even
    when the source is png/webp).
    """
    image_paths = list(result.image_paths[:_MAX_IMAGE_FRAMES])
    frames = [Frame(path=p, timestamp_seconds=float(i)) for i, p in enumerate(image_paths)]

    summary = await GeminiSummaryModel().summarize(frames=frames, audio=None, transcript=None)

    duration = result.duration_seconds or 0.0
    if not duration:
        duration = float(summary.duration_seconds or 0.0)

    await _finish_indexed(
        ctx,
        item=item,
        summary=summary,
        thumb_path=image_paths[0],
        caption=result.original_caption,
        owner_name=result.original_owner.name if result.original_owner else None,
        owner_username=result.original_owner.username if result.original_owner else None,
        owner_url=result.original_owner.url if result.original_owner else None,
        duration_seconds=duration,
        transcript_source="gemini",
        model_frames=len(frames),
    )


async def _finish_indexed(
    ctx: _RunContext,
    *,
    item: Item,
    summary: StructuredSummary,
    thumb_path: Path,
    caption: str | None,
    owner_name: str | None,
    owner_username: str | None,
    owner_url: str | None,
    duration_seconds: float,
    transcript_source: str,
    model_frames: int,
) -> None:
    """SHARED tail for both ingest branches (downloaded + YouTube native).

    Steps 6-11 of the canonical flow: thumbnail upload → pgvector
    embedding write → row hydration → audit event → SSE/push placeholder
    logging. Callers differ only in how they produced `summary`,
    `thumb_path`, and the owner/caption metadata.
    """
    settings = get_settings()

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

    # Step 7: embed + write into pgvector, scoped to the user.
    text_corpus = "\n".join(
        filter(
            None,
            [
                summary.title,
                summary.summary,
                summary.transcript,
                summary.on_screen_text,
                caption or "",
                "Objects: " + ", ".join(summary.objects) if summary.objects else "",
                "Scenes: " + " | ".join(summary.scenes) if summary.scenes else "",
                "Topics: " + ", ".join(summary.topics) if summary.topics else "",
                f"Owner: {owner_name}" if owner_name else "",
            ],
        )
    )
    await PgVectorStore(await get_pg_pool()).write(
        user_id=ctx.user_id,
        item=IndexedItem(
            item_id=ctx.item_id,
            text_corpus=text_corpus,
            metadata={
                "item_id": ctx.item_id,
                "platform": item.platform,
                "saved_at": item.saved_at.isoformat(),
                "duration_seconds": duration_seconds,
                "sentiment": summary.sentiment.value,
                "primary_language": summary.primary_language,
                "topics": summary.topics,
                "owner_username": owner_username,
            },
        ),
    )

    # Step 8-9: hydrate the row + append audit event.
    await _mark_fully_indexed(
        item_id=ctx.item_id,
        summary=summary,
        thumbnail_url=thumbnail_url,
        owner_name=owner_name,
        owner_username=owner_username,
        owner_url=owner_url,
        caption=caption,
    )

    # Steps 10-11 (SSE + push): Phase 3 — logging placeholder here so
    # transitions are still visible during pre-Phase-3 dev.
    _logger.info(
        "ingest.completed",
        item_id=ctx.item_id,
        user_id=ctx.user_id,
        duration_seconds=duration_seconds,
        model_frames=model_frames,
        transcript_source=transcript_source,
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
            id=str(ulid.new()),
            item_id=item.id,
            from_state=prev_state,
            to_state="downloading",
            detail=None,
        )
    )
    await session.commit()
    return item


async def _emit_event(
    item_id: str, from_state: str, to_state: str, detail: dict[str, Any] | None = None
) -> None:
    async with session_scope() as session:
        # Also flip items.state at the same time — the state machine is
        # kept in-sync with the audit trail per architecture.md §4.1.
        item = (await session.execute(select(Item).where(Item.id == item_id))).scalar_one()
        item.state = to_state
        session.add(
            IngestionEvent(
                id=str(ulid.new()),
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
                id=str(ulid.new()),
                item_id=item.id,
                from_state=prev_state,
                to_state="failed",
                detail={"reason": reason},
            )
        )
        await session.commit()


async def _mark_source_gone(item_id: str, reason: str) -> None:
    """Terminal non-retryable state for unresolvable sources (YouTube native).

    Unlike _mark_failed, callers return normally afterwards — Cloud Tasks
    must NOT retry a private/deleted video into existence.
    """
    async with session_scope() as session:
        item = (await session.execute(select(Item).where(Item.id == item_id))).scalar_one_or_none()
        if item is None:
            return
        prev_state = item.state
        item.state = "source_gone"
        item.failure_reason = reason
        session.add(
            IngestionEvent(
                id=str(ulid.new()),
                item_id=item.id,
                from_state=prev_state,
                to_state="source_gone",
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
        item.updated_at = datetime.now(UTC)
        session.add(
            IngestionEvent(
                id=str(ulid.new()),
                item_id=item.id,
                from_state=prev_state,
                to_state="fully_indexed",
                detail={},
            )
        )
        await session.commit()
