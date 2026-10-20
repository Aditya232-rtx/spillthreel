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
  * one `ingest` outbox intent per newly inserted item, committed with the
    item and import counters; prompt dispatch happens only after that commit;
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
  * transient DB failure → import marked `failed` best-effort, then
    re-raised; outbox delivery errors leave durable intents pending for the
    due-job reconciler and do not turn accepted items into request failures.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, replace
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
from app.services.notifications.intents import record_import_summary_intent
from app.services.imports.entries import (
    MediaOccurrence,
    build_occurrences,
    create_import_entry,
    ensure_import_collections,
    persist_occurrences,
    save_import_progress,
    sync_import_collection_memberships,
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
from app.tasks.outbox import dispatch_after_commit, ensure_existing_intent, record_task_intent
from app.tasks.worker_claims import TargetExecution, target_intent_is_current_in_session

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


class ImportLeaseLostError(RuntimeError):
    """The import target lease or live-account fence no longer belongs to this worker."""


async def process_import(
    import_id: str,
    user_id: str,
    source: ParsedExport | Path | bytes,
    admitted_reservation_keys: set[str] | None = None,
    *,
    already_reserved_reservation_keys: set[str] | None = None,
    execution: TargetExecution,
) -> ImportOutcome:
    """Parse (if needed), upsert items, enqueue one ingest task per item.

    `source` accepts the parser's input forms: an already-parsed
    `ParsedExport` (inline path), a ZIP `Path` (worker path), or raw ZIP
    `bytes`. Owns its own `session_scope` so both the API request path
    and the worker path can share it.
    """
    # Parsing can be slow. Do it before opening a transaction or taking the
    # account/target/outbox locks used by the execution fence.
    parse_error: Exception | None = None
    parsed: ParsedExport | None = None
    try:
        parsed = _resolve_parsed(source)
    except (ParserError, zipfile.BadZipFile, OSError) as exc:
        parse_error = exc

    now = datetime.now(UTC)
    async with session_scope() as session:
        await _require_current_execution(session, execution)
        import_row = await _load_import(session, import_id, user_id)
        if import_row is None:
            raise ImportNotFoundError(f"import {import_id} not found")

        if parse_error is not None:
            reason = f"parse_failed: {parse_error}"[:500]
            await _require_current_execution(session, execution)
            _mark_failed(import_row, reason, now)
            await record_import_summary_intent(
                session, owner_id=user_id, import_id=import_id
            )
            await session.commit()
            _logger.warning("ig_pipeline.parse_failed", import_id=import_id, reason=reason)
            return ImportOutcome(ok=False)

        assert parsed is not None
        try:
            return await _run(
                session,
                import_row,
                parsed,
                user_id,
                now,
                execution,
                admitted_reservation_keys=(
                    admitted_reservation_keys if import_row.admission_enforced else None
                ),
                already_reserved_reservation_keys=(
                    (already_reserved_reservation_keys or set())
                    if import_row.admission_enforced
                    else None
                ),
            )
        except ImportLeaseLostError:
            raise
        except Exception as e:
            # Transient (DB / enqueue). Best-effort failed mark, then
            # re-raise so Cloud Tasks retries; re-entry is idempotent.
            try:
                await _require_current_execution(session, execution)
                _mark_failed(import_row, str(e)[:500] or "import_failed", datetime.now(UTC))
                await session.commit()
            except ImportLeaseLostError:
                raise
            except Exception:
                _logger.exception("ig_pipeline.mark_failed_failed", import_id=import_id)
            raise


async def process_stored_import(
    import_id: str,
    user_id: str,
    storage_key: str,
    admitted_reservation_keys: set[str] | None = None,
    *,
    already_reserved_reservation_keys: set[str] | None = None,
    execution: TargetExecution,
) -> ImportOutcome:
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
        return await process_import(
            import_id,
            user_id,
            dest,
            admitted_reservation_keys=admitted_reservation_keys,
            already_reserved_reservation_keys=already_reserved_reservation_keys,
            execution=execution,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _filter_admitted_entries(parsed: ParsedExport, allowed_keys: set[str]) -> ParsedExport:
    """Constrain staged worker execution to the admissions committed by intake."""
    from app.services.imports.admission import (
        reservation_key_for_url,
        saved_audio_identity_url,
    )

    def allowed(url: str) -> bool:
        return reservation_key_for_url(url) in allowed_keys

    collections = tuple(
        filtered
        for collection in parsed.collections
        if (filtered := replace(
            collection,
            media=tuple(post for post in collection.media if allowed(post.url)),
        )).media
    )
    audio = tuple(
        entry
        for entry in parsed.audio
        if reservation_key_for_url(
            saved_audio_identity_url(entry.title, entry.artist, entry.saved_at)
        ) in allowed_keys
    )
    return replace(
        parsed,
        posts=tuple(post for post in parsed.posts if allowed(post.url)),
        collections=collections,
        audio=audio,
    )


async def _run(
    session: AsyncSession,
    import_row: Import,
    parsed: ParsedExport,
    user_id: str,
    now: datetime,
    execution: TargetExecution,
    *,
    admitted_reservation_keys: set[str] | None,
    already_reserved_reservation_keys: set[str] | None,
) -> ImportOutcome:
    occurrences, unique_candidates = build_occurrences(
        parsed,
        import_id=import_row.id,
        owner_id=user_id,
        admitted_reservation_keys=admitted_reservation_keys,
        already_reserved_reservation_keys=already_reserved_reservation_keys,
    )
    await ensure_import_collections(
        session,
        import_id=import_row.id,
        owner_id=user_id,
        occurrences=occurrences,
    )
    total_parsed = len(occurrences)
    audio_by_position = {
        occurrence.values["source_position"]: occurrence.audio
        for occurrence in occurrences
        if occurrence.audio is not None
    }
    audio_saved = 0
    queued = 0
    duplicates = sum(
        1
        for occurrence in occurrences
        if occurrence.values["source_kind"] == "media"
    ) - len(unique_candidates)
    skipped = sum(
        1
        for occurrence in occurrences
        if occurrence.values["source_kind"] == "unsupported"
    )
    dispatch_ids: list[str] = []
    for start in range(0, total_parsed, _CHUNK_SIZE):
        await _require_current_execution(session, execution)
        chunk_occurrences = [
            occurrence
            for occurrence in occurrences
            if start <= occurrence.values["source_position"] < start + _CHUNK_SIZE
        ]
        for candidate in unique_candidates.values():
            if not (start <= candidate.source_position < start + _CHUNK_SIZE):
                continue
            entry = await _existing_import_entry(
                session, import_id=import_row.id, user_id=user_id, candidate=candidate
            )
            if entry is not None:
                if entry.item_id is not None:
                    item_result = await session.execute(
                        select(Item).where(Item.id == entry.item_id, Item.user_id == user_id)
                    )
                    linked_item = item_result.scalar_one_or_none()
                    if linked_item is not None:
                        _refresh_entry_state(entry, linked_item)
                        if linked_item.state == "queued" and not candidate.already_reserved:
                            outbox_row = await ensure_existing_intent(
                                session,
                                "ingest",
                                {"item_id": linked_item.id, "user_id": user_id},
                                target_type="item",
                                target_id=linked_item.id,
                            )
                            dispatch_ids.append(outbox_row.id)
                # A persisted membership survives item deletion. Never mint a
                # replacement item or work on replay.
                continue

            if candidate.already_reserved:
                existing_result = await session.execute(
                    select(Item).where(
                        Item.user_id == user_id,
                        Item.source_url_norm == candidate.source_url_norm,
                    )
                )
                existing_item = existing_result.scalar_one_or_none()
                state, _ = _entry_state_for_existing_item(existing_item, now)
                entry = await create_import_entry(
                    session,
                    import_id=import_row.id,
                    owner_id=user_id,
                    occurrence=candidate,
                    item_id=existing_item.id if existing_item is not None else None,
                    state="retryable_failed" if existing_item is None else state,
                    work_queued_at=None,
                    last_error_code=("reserved_item_pending" if existing_item is None else None),
                )
                if existing_item is not None:
                    entry.item_id = existing_item.id
                    entry.state = state
                    entry.work_queued_at = None
                else:
                    entry.state = "retryable_failed"
                    entry.last_error_code = "reserved_item_pending"
                    # Close the narrow race where the original reservation's
                    # Item appeared after the first lookup but before this
                    # membership was created. The owner Profile lock in the
                    # worker fences serializes this with normal ingestion.
                    raced_item = (
                        await session.execute(
                            select(Item)
                            .where(
                                Item.user_id == user_id,
                                Item.source_url_norm == candidate.source_url_norm,
                            )
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if raced_item is not None:
                        raced_state, _ = _entry_state_for_existing_item(raced_item, now)
                        entry.item_id = raced_item.id
                        entry.state = raced_state
                        entry.last_error_code = None
                duplicates += 1
                continue

            values = _build_item_values(candidate.post, user_id, now)
            if values is None:
                skipped += 1
                await create_import_entry(
                    session,
                    import_id=import_row.id,
                    owner_id=user_id,
                    occurrence=candidate,
                    item_id=None,
                    state="terminal_failed",
                    work_queued_at=None,
                    last_error_code="item_unrepresentable",
                )
                continue
            inserted_id = await _insert_item_once(session, values)
            if inserted_id is not None:
                entry = await create_import_entry(
                    session,
                    import_id=import_row.id,
                    owner_id=user_id,
                    occurrence=candidate,
                    item_id=inserted_id,
                    state="queued",
                    work_queued_at=now,
                )
                entry.item_id = inserted_id
                entry.state = "queued"
                entry.work_queued_at = now
                queued += 1
                outbox_row = await record_task_intent(
                    session,
                    "ingest",
                    {"item_id": inserted_id, "user_id": user_id},
                    target_type="item",
                    target_id=inserted_id,
                )
                dispatch_ids.append(outbox_row.id)
                continue

            duplicates += 1
            existing_result = await session.execute(
                select(Item).where(
                    Item.user_id == user_id,
                    Item.source_url_norm == candidate.source_url_norm,
                )
            )
            existing_item = existing_result.scalar_one_or_none()
            state, work_queued_at = _entry_state_for_existing_item(existing_item, now)
            entry = await create_import_entry(
                session,
                import_id=import_row.id,
                owner_id=user_id,
                occurrence=candidate,
                item_id=existing_item.id if existing_item is not None else None,
                state=state,
                work_queued_at=work_queued_at,
            )
            if existing_item is not None:
                entry.item_id = existing_item.id
                entry.state = state
                entry.work_queued_at = work_queued_at
            if existing_item is not None and existing_item.state == "queued":
                outbox_row = await ensure_existing_intent(
                    session,
                    "ingest",
                    {"item_id": existing_item.id, "user_id": user_id},
                    target_type="item",
                    target_id=existing_item.id,
                )
                dispatch_ids.append(outbox_row.id)

        # Each admitted occurrence has an FK to its unique ImportEntry. Persist
        # entries first, then occurrences, within this same fenced transaction.
        newly_persisted_positions = await persist_occurrences(session, chunk_occurrences)
        await sync_import_collection_memberships(
            session, import_id=import_row.id, owner_id=user_id
        )
        new_audio = [
            audio_by_position[position]
            for position in newly_persisted_positions
            if position in audio_by_position
        ]
        if new_audio:
            audio_saved += await _insert_audio_rows(session, user_id, tuple(new_audio), now)

        await save_import_progress(
            session,
            import_row,
            total_parsed=total_parsed,
            parser_schema_hash=parsed.parser_schema_hash,
        )
        await session.commit()
        if dispatch_ids:
            # Dispatch is only a latency hint over durable item intents. Check
            # the import lease again after the data commit and release the
            # fence transaction before making the external queue call.
            await _require_current_execution(session, execution)
            await session.commit()
            await dispatch_after_commit(list(dispatch_ids))
        dispatch_ids.clear()

    # The final state/counter write has its own transaction and therefore its
    # own fence, including exports with no media candidates.
    await _require_current_execution(session, execution)
    await save_import_progress(
        session,
        import_row,
        total_parsed=total_parsed,
        parser_schema_hash=parsed.parser_schema_hash,
        finalize_manifest=True,
    )
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


async def _existing_import_entry(
    session: AsyncSession,
    *,
    import_id: str,
    user_id: str,
    candidate: MediaOccurrence,
) -> Any | None:
    from app.services.imports.entries import lock_import_entry

    return await lock_import_entry(
        session,
        import_id=import_id,
        owner_id=user_id,
        reservation_key=candidate.reservation_key,
    )


def _entry_state_for_existing_item(item: Item | None, now: datetime) -> tuple[str, datetime | None]:
    if item is None:
        return "terminal_failed", None
    state = {
        "queued": "queued",
        "downloading": "queued",
        "analyzing": "queued",
        "indexing": "queued",
        "text_indexed": "text_indexed",
        "fully_indexed": "completed",
        "failed": "retryable_failed",
        "source_gone": "terminal_failed",
    }.get(item.state, "terminal_failed")
    return state, now if item.state == "queued" else None


def _refresh_entry_state(entry: Any, item: Item) -> None:
    state = {
        "queued": "queued",
        "downloading": "queued",
        "analyzing": "queued",
        "indexing": "queued",
        "text_indexed": "text_indexed",
        "fully_indexed": "completed",
        "failed": "retryable_failed",
        "source_gone": "terminal_failed",
    }.get(item.state, "terminal_failed")
    entry.state = state


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


async def _require_current_execution(
    session: AsyncSession, execution: TargetExecution
) -> None:
    if not await target_intent_is_current_in_session(session, execution):
        raise ImportLeaseLostError("import execution lease is no longer current")
