"""IG bulk-import pipeline — `process_import(import_id, user_id, source)`.

Shared by both intake paths (see `app/api/v1/imports.py`):

  * inline — POST /v1/imports with ≤ 50 entries parses the upload and
    calls `process_import` directly with the `ParsedExport`;
  * async — larger uploads are staged to the `exports` Storage bucket and
    a single `import` Cloud Task fires `POST /work/import`, which calls
    `process_stored_import` → `process_import` with the downloaded ZIP.

Per entry the pipeline mirrors POST /v1/saves exactly:

  * dedup via `INSERT ... ON CONFLICT DO NOTHING` on
    `items.(user_id, source_url_norm)` (same constraint, same
    `index_elements`) — within-batch dupes and pre-existing rows both
    land as conflicts and are counted, never re-enqueued;
  * one `ingest` task per newly inserted item, same payload shape and
    `dedup_key=f"ingest-{item_id}"` as saves, so Cloud Tasks retries and
    worker re-entry stay idempotent;
  * `Item.source_type` is always `'instagram_import'`.

Counter semantics (honest scope):

  * `total_parsed` = every parsed media entry offered to the pipeline
    (`posts` + flattened collection `media`) plus every audio entry —
    i.e. what the export contained, including dupes and skips.
  * `full_queued` = items newly inserted and handed an ingest task.
    Both are flushed to the imports row every 100 entries (TRD §8b) and
    finalized at the end; success leaves state `'indexing'`.
  * Terminal counters (`full_done`, `failed`, `text_indexed_done`) are
    NOT updated here: items carry no `import_id` FK and the ingest
    pipeline (`services/ingest/pipeline.py`) has no per-item import
    hooks, so nothing can attribute completions back to an import.
    Wiring that attribution is deferred work, not silently done.
  * `saved_audio` rows are written on the first attempt only (guarded by
    `total_parsed == 0`) since that table has no dedup constraint —
    best-effort idempotency. Collection folders (`collections` /
    `collection_items`) are intentionally NOT materialized yet; their
    media still become items, the folder structure is dropped (logged).

Failure semantics:

  * deterministic (unparseable ZIP) → import marked `failed`, returned
    `ImportOutcome.ok is False`, NO raise — the worker 200s so Cloud
    Tasks does not retry a poison payload into infinity.
  * transient (DB / enqueue blowup) → import marked `failed`
    best-effort, then re-raised so the worker 500s and Cloud Tasks
    retries; re-entry is safe via the upsert + task dedup keys.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ulid
from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import session_scope
from app.db.models import Import, Item
from app.db.models import SavedAudio as SavedAudioRow
from app.observability.logging import get_logger
from app.services.imports.ig_parser import (
    ParsedExport,
    ParserError,
    SavedPost,
    parse_zip,
)
from app.services.imports.ig_parser import (
    SavedAudio as ParsedAudio,
)
from app.services.ingest.platform_detect import (
    UnsupportedPlatformError,
    detect_platform,
    normalize_url,
)
from app.settings import get_settings
from app.tasks.enqueue import enqueue_task

_logger = get_logger(__name__)

# POST /v1/imports runs the pipeline inline at or below this many parsed
# entries; above it the upload is staged to Storage and a single `import`
# task runs it on the worker.
INLINE_MAX_ENTRIES = 50

# TRD §8b: the worker flushes import counters every 100 entries.
_CHUNK_SIZE = 100


@dataclass
class ImportOutcome:
    """Counts for one `process_import` run."""

    ok: bool
    total_parsed: int = 0
    queued: int = 0
    duplicates: int = 0
    skipped: int = 0
    audio_saved: int = 0


class ImportNotFoundError(RuntimeError):
    """The imports row is missing (or belongs to another user)."""


async def process_import(
    import_id: str, user_id: str, source: ParsedExport | Path | bytes
) -> ImportOutcome:
    """Parse (if needed), upsert items, enqueue one ingest task per item.

    `source` accepts the parser's input forms: an already-parsed
    `ParsedExport` (inline path), a ZIP `Path` (worker path), or raw ZIP
    `bytes`. Owns its own `session_scope` so both the API request path
    and the worker path can share it.
    """
    now = datetime.now(UTC)
    async with session_scope() as session:
        import_row = await _load_import(session, import_id, user_id)
        if import_row is None:
            raise ImportNotFoundError(f"import {import_id} not found")

        try:
            parsed = _resolve_parsed(source)
        except (ParserError, zipfile.BadZipFile, OSError) as e:
            reason = f"parse_failed: {e}"[:500]
            _mark_failed(import_row, reason, now)
            await session.commit()
            _logger.warning("ig_pipeline.parse_failed", import_id=import_id, reason=reason)
            return ImportOutcome(ok=False)

        try:
            return await _run(session, import_row, parsed, user_id, now)
        except Exception as e:
            # Transient (DB / enqueue). Best-effort failed mark, then
            # re-raise so Cloud Tasks retries; re-entry is idempotent.
            try:
                _mark_failed(import_row, str(e)[:500] or "import_failed", datetime.now(UTC))
                await session.commit()
            except Exception:
                _logger.exception("ig_pipeline.mark_failed_failed", import_id=import_id)
            raise


async def process_stored_import(import_id: str, user_id: str, storage_key: str) -> ImportOutcome:
    """Worker/local path: download the staged ZIP, then `process_import`.

    Download errors propagate (transient → caller 500s → retry); parse
    errors are handled inside `process_import` (deterministic → no retry).
    """
    from app.services.media.supabase_storage import download_object

    settings = get_settings()
    tmpdir = Path(tempfile.mkdtemp(prefix="spill-import-"))
    dest = tmpdir / "export.zip"
    try:
        await download_object(settings.supabase_bucket_exports, storage_key, dest)
        return await process_import(import_id, user_id, dest)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def _run(
    session: AsyncSession,
    import_row: Import,
    parsed: ParsedExport,
    user_id: str,
    now: datetime,
) -> ImportOutcome:
    candidates: list[SavedPost] = [
        *parsed.posts,
        *(media for coll in parsed.collections for media in coll.media),
    ]
    if parsed.collections:
        _logger.info(
            "ig_pipeline.collections_flattened",
            import_id=import_row.id,
            collections=len(parsed.collections),
            media=sum(len(c.media) for c in parsed.collections),
            note="folder structure not materialized (deferred)",
        )
    total_parsed = len(candidates) + len(parsed.audio)

    audio_saved = 0
    # saved_audio has no dedup constraint — first attempt only.
    if parsed.audio and import_row.total_parsed == 0:
        audio_saved = await _insert_audio_rows(session, user_id, parsed.audio, now)

    queued = 0
    duplicates = 0
    skipped = 0
    for start in range(0, len(candidates), _CHUNK_SIZE):
        for post in candidates[start : start + _CHUNK_SIZE]:
            values = _build_item_values(post, user_id, now)
            if values is None:
                skipped += 1
                continue
            inserted_id = await _insert_item_once(session, values)
            if inserted_id is None:
                duplicates += 1
                continue
            queued += 1
            await enqueue_task(
                "ingest",
                {"item_id": inserted_id, "user_id": user_id},
                dedup_key=f"ingest-{inserted_id}",
            )
        import_row.total_parsed = total_parsed
        import_row.full_queued = queued
        await session.commit()

    import_row.total_parsed = total_parsed
    import_row.full_queued = queued
    import_row.parser_schema_hash = parsed.parser_schema_hash
    import_row.state = "indexing"
    await session.commit()

    _logger.info(
        "ig_pipeline.done",
        import_id=import_row.id,
        total_parsed=total_parsed,
        queued=queued,
        duplicates=duplicates,
        skipped=skipped,
        audio_saved=audio_saved,
    )
    return ImportOutcome(
        ok=True,
        total_parsed=total_parsed,
        queued=queued,
        duplicates=duplicates,
        skipped=skipped,
        audio_saved=audio_saved,
    )


def _resolve_parsed(source: ParsedExport | Path | bytes) -> ParsedExport:
    """Accept the parser's input forms — export object, ZIP path, or bytes."""
    if isinstance(source, ParsedExport):
        return source
    if isinstance(source, bytes):
        fd, tmp_name = tempfile.mkstemp(suffix=".zip", prefix="spill-import-")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fp:
                fp.write(source)
            return parse_zip(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    return parse_zip(source)


def _build_item_values(post: SavedPost, user_id: str, now: datetime) -> dict[str, Any] | None:
    """Map one parsed post to an items-row dict, or None to skip it.

    Skips (counted, never fatal): empty URLs and unsupported platforms —
    same 400-class as saves, but a single bad row must not fail a bulk
    import. Parser timestamps are naive Meta-local; treated as UTC per
    the parser docstring so asyncpg (timestamptz) never sees naive input.
    """
    url = (post.url or "").strip()
    if not url:
        return None
    try:
        platform = detect_platform(url)
    except UnsupportedPlatformError:
        _logger.info("ig_pipeline.unsupported_url", url=url)
        return None
    saved_at = post.saved_at
    if saved_at is None:
        saved_at = now
    elif saved_at.tzinfo is None:
        saved_at = saved_at.replace(tzinfo=UTC)
    owner = post.owner
    return {
        "id": str(ulid.new()),
        "user_id": user_id,
        "source_url": url,
        "source_url_norm": normalize_url(url),
        "platform": platform,
        "state": "queued",
        "saved_at": saved_at,
        "source_type": "instagram_import",
        "caption": post.caption,
        "hashtags": list(post.hashtags),
        "owner_name": owner.name if owner else None,
        "owner_username": owner.username if owner else None,
        "owner_url": owner.url if owner else None,
    }


async def _insert_item_once(session: AsyncSession, values: dict[str, Any]) -> str | None:
    """Upsert-with-dedup identical to POST /v1/saves.

    `INSERT ... ON CONFLICT DO NOTHING` on (user_id, source_url_norm);
    None means conflict (pre-existing row or within-batch dupe).
    """
    stmt = (
        pg_insert(Item)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["user_id", "source_url_norm"])
        .returning(Item.id)
    )
    result = await session.execute(stmt)
    inserted = result.scalar_one_or_none()
    return str(inserted) if inserted is not None else None


async def _insert_audio_rows(
    session: AsyncSession,
    user_id: str,
    audio: tuple[ParsedAudio, ...],
    now: datetime,
) -> int:
    """Persist saved-music entries. No dedup constraint exists, so the
    caller gates this to first attempts only (best-effort idempotency)."""
    rows: list[dict[str, Any]] = []
    for entry in audio:
        saved_at = entry.saved_at
        if saved_at is not None and saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=UTC)
        rows.append(
            {
                "id": str(ulid.new()),
                "user_id": user_id,
                "source_type": "instagram_import",
                "title": entry.title,
                "artist": entry.artist,
                "saved_at": saved_at,
            }
        )
    if rows:
        await session.execute(insert(SavedAudioRow).values(rows))
    _logger.info("ig_pipeline.audio_saved", count=len(rows))
    return len(rows)


async def _load_import(session: AsyncSession, import_id: str, user_id: str) -> Import | None:
    stmt = select(Import).where(Import.id == import_id, Import.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


def _mark_failed(import_row: Import, reason: str, now: datetime) -> None:
    import_row.state = "failed"
    import_row.failure_reason = reason
    import_row.finished_at = now
