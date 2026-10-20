"""Durable import membership and source-occurrence records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Collection,
    CollectionItem,
    Import,
    ImportCollection,
    ImportEntry,
    ImportEntryOccurrence,
    Item,
)
from app.services.imports.admission import reservation_key_for_saved_audio, reservation_key_for_url
from app.services.imports.ig_parser import ParsedExport, SavedAudio, SavedPost
from app.services.notifications.intents import record_import_summary_intent
from app.services.ingest.platform_detect import UnsupportedPlatformError, detect_platform, normalize_url


@dataclass(frozen=True, slots=True)
class MediaOccurrence:
    source_position: int
    post: SavedPost
    reservation_key: str
    source_url_norm: str
    source_bucket: str
    already_reserved: bool
    collection_key: str | None
    collection_name: str | None


@dataclass(frozen=True, slots=True)
class ParsedOccurrence:
    values: dict[str, Any]
    audio: SavedAudio | None = None


@dataclass(frozen=True, slots=True)
class ImportProgress:
    total_parsed: int
    full_queued: int
    text_indexed_done: int
    full_done: int
    failed: int


def build_occurrences(
    parsed: ParsedExport,
    *,
    import_id: str,
    owner_id: str,
    admitted_reservation_keys: set[str] | None = None,
    already_reserved_reservation_keys: set[str] | None = None,
) -> tuple[list[ParsedOccurrence], dict[str, MediaOccurrence]]:
    """Build stable occurrence rows and one unique media candidate per URL key."""
    rows: list[ParsedOccurrence] = []
    unique: dict[str, MediaOccurrence] = {}
    position = 0
    admission_filtering = (
        admitted_reservation_keys is not None or already_reserved_reservation_keys is not None
    )
    admitted_reservation_keys = admitted_reservation_keys or set()
    already_reserved_reservation_keys = already_reserved_reservation_keys or set()

    def add_media(
        post: SavedPost, collection_key: str | None, collection_name: str | None
    ) -> None:
        nonlocal position
        source_url_norm: str | None = None
        key: str | None = None
        bucket: str | None = None
        try:
            source_url_norm = normalize_url(post.url)
            platform = detect_platform(post.url)
            key = reservation_key_for_url(post.url)
            bucket = "instagram" if platform == "instagram" else "other"
        except (UnsupportedPlatformError, ValueError):
            pass
        if key is None or source_url_norm is None or bucket is None:
            values = _occurrence_values(
                import_id=import_id,
                owner_id=owner_id,
                position=position,
                kind="unsupported",
                outcome="unsupported",
                entry_key=None,
                source_url_norm=None,
                collection_key=collection_key,
                collection_name=collection_name,
                saved_at=post.saved_at,
            )
        else:
            already_reserved = key in already_reserved_reservation_keys
            admitted = key in admitted_reservation_keys
            eligible = admitted or already_reserved or not admission_filtering
            values = _occurrence_values(
                import_id=import_id,
                owner_id=owner_id,
                position=position,
                kind="media",
                outcome=("already_reserved" if already_reserved else "admitted")
                if eligible
                else "excluded",
                entry_key=key if eligible else None,
                source_url_norm=source_url_norm,
                collection_key=collection_key,
                collection_name=collection_name,
                saved_at=post.saved_at,
            )
            if eligible:
                unique.setdefault(
                    key,
                    MediaOccurrence(
                        source_position=position,
                        post=post,
                        reservation_key=key,
                        source_url_norm=source_url_norm,
                        source_bucket=bucket,
                        already_reserved=already_reserved,
                        collection_key=collection_key,
                        collection_name=collection_name,
                    ),
                )
        rows.append(ParsedOccurrence(values))
        position += 1

    for post in parsed.posts:
        add_media(post, None, None)
    for collection_index, collection in enumerate(parsed.collections):
        collection_key = _collection_key(collection_index, collection.name, collection.type)
        for post in collection.media:
            add_media(post, collection_key, collection.name)

    for audio in parsed.audio:
        audio_key = reservation_key_for_saved_audio(audio.title, audio.artist, audio.saved_at)
        audio_eligible = (
            audio_key in admitted_reservation_keys
            or audio_key in already_reserved_reservation_keys
            or not admission_filtering
        )
        rows.append(
            ParsedOccurrence(
                _occurrence_values(
                    import_id=import_id,
                    owner_id=owner_id,
                    position=position,
                    kind="audio",
                    outcome="audio_metadata",
                    entry_key=None,
                    source_url_norm=None,
                    collection_key=None,
                    collection_name=None,
                    saved_at=audio.saved_at,
                ),
                audio=audio if audio_eligible else None,
            )
        )
        position += 1
    return rows, unique


async def persist_occurrences(
    session: AsyncSession, occurrences: list[ParsedOccurrence]
) -> set[int]:
    """Persist occurrences idempotently; return newly persisted source positions."""
    if not occurrences:
        return set()
    stmt = (
        pg_insert(ImportEntryOccurrence)
        .values([occurrence.values for occurrence in occurrences])
        .on_conflict_do_nothing()
        .returning(ImportEntryOccurrence.source_position)
    )
    result = await session.execute(stmt)
    return {int(position) for position in result.scalars().all()}


async def ensure_import_collections(
    session: AsyncSession,
    *,
    import_id: str,
    owner_id: str,
    occurrences: list[ParsedOccurrence],
) -> None:
    """Create stable, import-owned folder mappings without name-based merging.

    Every imported folder gets its own deterministic Collection ID and an
    explicit ImportCollection mapping. The display name includes the import
    and folder identity, so a user-created collection with the source folder's
    name is never silently reused.
    """
    folders: dict[str, str] = {}
    for occurrence in occurrences:
        values = occurrence.values
        key = values.get("collection_key")
        name = values.get("collection_name")
        if isinstance(key, str) and isinstance(name, str) and name.strip():
            previous = folders.setdefault(key, name.strip())
            if previous != name.strip():
                raise RuntimeError("import_collection_identity_conflict")

    for collection_key, source_name in sorted(folders.items()):
        collection_id = _import_collection_id(import_id, collection_key)
        display_name = _import_collection_display_name(
            source_name, import_id=import_id, collection_key=collection_key
        )
        await session.execute(
            pg_insert(Collection)
            .values(
                id=collection_id,
                user_id=owner_id,
                name=display_name,
                privacy="private",
                source="instagram_import",
            )
            .on_conflict_do_nothing()
        )
        await session.execute(
            pg_insert(ImportCollection)
            .values(
                import_id=import_id,
                owner_id=owner_id,
                collection_key=collection_key,
                collection_id=collection_id,
                collection_name=source_name,
            )
            .on_conflict_do_update(
                index_elements=[ImportCollection.import_id, ImportCollection.collection_key],
                set_={"collection_id": collection_id, "collection_name": source_name},
            )
        )


async def sync_import_collection_memberships(
    session: AsyncSession, *, import_id: str, owner_id: str
) -> None:
    """Materialize every known folder/item edge once, scoped to one owner."""
    rows = (
        await session.execute(
            select(
                ImportCollection.collection_id,
                ImportEntry.item_id,
            )
            .select_from(ImportEntryOccurrence)
            .join(
                ImportEntry,
                (ImportEntry.import_id == ImportEntryOccurrence.import_id)
                & (ImportEntry.owner_id == ImportEntryOccurrence.owner_id)
                & (ImportEntry.reservation_key == ImportEntryOccurrence.entry_reservation_key),
            )
            .join(
                ImportCollection,
                (ImportCollection.import_id == ImportEntryOccurrence.import_id)
                & (ImportCollection.owner_id == ImportEntryOccurrence.owner_id)
                & (ImportCollection.collection_key == ImportEntryOccurrence.collection_key),
            )
            .where(
                ImportEntryOccurrence.import_id == import_id,
                ImportEntryOccurrence.owner_id == owner_id,
                ImportEntryOccurrence.collection_key.is_not(None),
                ImportEntry.item_id.is_not(None),
            )
            .distinct()
        )
    ).all()
    memberships = [
        {"collection_id": collection_id, "item_id": item_id, "owner_id": owner_id}
        for collection_id, item_id in rows
    ]
    if memberships:
        await session.execute(
            pg_insert(CollectionItem).values(memberships).on_conflict_do_nothing()
        )


async def lock_import_entry(
    session: AsyncSession,
    *,
    import_id: str,
    owner_id: str,
    reservation_key: str,
) -> ImportEntry | None:
    return (
        await session.execute(
            select(ImportEntry)
            .where(
                ImportEntry.import_id == import_id,
                ImportEntry.owner_id == owner_id,
                ImportEntry.reservation_key == reservation_key,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def create_import_entry(
    session: AsyncSession,
    *,
    import_id: str,
    owner_id: str,
    occurrence: MediaOccurrence,
    item_id: str | None,
    state: str,
    work_queued_at: datetime | None,
    last_error_code: str | None = None,
) -> ImportEntry:
    """Insert or lock a unique membership row; the caller owns the transaction."""
    stmt = (
        pg_insert(ImportEntry)
        .values(
            import_id=import_id,
            reservation_key=occurrence.reservation_key,
            owner_id=owner_id,
            source_url_norm=occurrence.source_url_norm,
            source_bucket=occurrence.source_bucket,
            item_id=item_id,
            state=state,
            work_queued_at=work_queued_at,
            last_error_code=last_error_code,
        )
        .on_conflict_do_nothing()
    )
    await session.execute(stmt)
    entry = await lock_import_entry(
        session,
        import_id=import_id,
        owner_id=owner_id,
        reservation_key=occurrence.reservation_key,
    )
    if entry is None:
        raise RuntimeError("import_entry_missing_after_upsert")
    if entry.source_url_norm != occurrence.source_url_norm or entry.source_bucket != occurrence.source_bucket:
        raise RuntimeError("import_entry_identity_conflict")
    return entry


async def derive_import_progress(
    session: AsyncSession, *, import_id: str, total_parsed: int
) -> ImportProgress:
    """Calculate durable counters from saved occurrences and unique memberships."""
    occurrence_total = await session.scalar(
        select(func.count())
        .select_from(ImportEntryOccurrence)
        .where(ImportEntryOccurrence.import_id == import_id)
    )
    queued = await session.scalar(
        select(func.count())
        .select_from(ImportEntry)
        .where(ImportEntry.import_id == import_id, ImportEntry.work_queued_at.is_not(None))
    )
    text_indexed = await session.scalar(
        select(func.count())
        .select_from(ImportEntry)
        .where(ImportEntry.import_id == import_id, ImportEntry.state.in_(("text_indexed", "completed")))
    )
    completed = await session.scalar(
        select(func.count())
        .select_from(ImportEntry)
        .where(ImportEntry.import_id == import_id, ImportEntry.state == "completed")
    )
    failed = await session.scalar(
        select(func.count())
        .select_from(ImportEntry)
        .where(ImportEntry.import_id == import_id, ImportEntry.state.in_(("retryable_failed", "terminal_failed")))
    )
    return ImportProgress(
        total_parsed=int(occurrence_total if occurrence_total is not None else total_parsed),
        full_queued=int(queued or 0),
        text_indexed_done=int(text_indexed or 0),
        full_done=int(completed or 0),
        failed=int(failed or 0),
    )


async def save_import_progress(
    session: AsyncSession,
    import_row: Import,
    *,
    total_parsed: int,
    parser_schema_hash: str,
    finalize_manifest: bool = False,
) -> ImportProgress:
    progress = await derive_import_progress(
        session, import_id=import_row.id, total_parsed=total_parsed
    )
    pending = await session.scalar(
        select(func.count())
        .select_from(ImportEntry)
        .where(
            ImportEntry.import_id == import_row.id,
            ImportEntry.state.not_in(("completed", "terminal_failed")),
        )
    )
    terminal = finalize_manifest and int(pending or 0) == 0
    state = (
        "failed" if terminal and progress.full_done == 0 and progress.failed > 0
        else "complete" if terminal
        else "indexing" if finalize_manifest
        else import_row.state
    )
    finished_at = datetime.now(UTC) if terminal else None
    await session.execute(
        update(Import)
        .where(Import.id == import_row.id, Import.user_id == import_row.user_id)
        .values(
            total_parsed=progress.total_parsed,
            full_queued=progress.full_queued,
            text_indexed_done=progress.text_indexed_done,
            full_done=progress.full_done,
            failed=progress.failed,
            parser_schema_hash=parser_schema_hash,
            state=state,
            finished_at=finished_at,
        )
    )
    import_row.total_parsed = progress.total_parsed
    import_row.full_queued = progress.full_queued
    import_row.text_indexed_done = progress.text_indexed_done
    import_row.full_done = progress.full_done
    import_row.failed = progress.failed
    import_row.parser_schema_hash = parser_schema_hash
    import_row.state = state
    import_row.finished_at = finished_at
    if terminal and state in {"complete", "failed"}:
        await record_import_summary_intent(
            session, owner_id=import_row.user_id, import_id=import_row.id
        )
    return progress


async def sync_import_entries_for_item(
    session: AsyncSession,
    *,
    item_id: str,
    owner_id: str,
    item_state: str,
    error_code: str | None = None,
) -> None:
    """Atomically mirror a fenced Item transition to every import membership.

    Call from the same transaction as the Item update. Lock membership rows
    before Import rows, matching the import pipeline's entry→progress order.
    """
    state = {
        "queued": "queued",
        "downloading": "queued",
        "analyzing": "queued",
        "indexing": "queued",
        "text_indexed": "text_indexed",
        "fully_indexed": "completed",
        "failed": "retryable_failed",
        "source_gone": "terminal_failed",
    }.get(item_state, "terminal_failed")
    item_url_norm = await session.scalar(
        select(Item.source_url_norm).where(Item.id == item_id, Item.user_id == owner_id)
    )
    if item_url_norm is None:
        return
    import_ids = sorted(
        (
            await session.execute(
                select(ImportEntry.import_id)
                .where(
                    ImportEntry.owner_id == owner_id,
                    (ImportEntry.item_id == item_id)
                    | (
                        ImportEntry.item_id.is_(None)
                        & (ImportEntry.source_url_norm == item_url_norm)
                    ),
                )
                .distinct()
            )
        ).scalars().all()
    )
    if not import_ids:
        return
    imports = (
        await session.execute(
            select(Import)
            .where(Import.id.in_(import_ids), Import.user_id == owner_id)
            .order_by(Import.id)
            .with_for_update()
        )
    ).scalars().all()
    # Lock order is import→entry, matching the importer’s execution-fence
    # transaction. A completion racing with a chunk can therefore wait on the
    # Import row instead of deadlocking while each holds the other row.
    entries = (
        await session.execute(
            select(ImportEntry)
            .where(
                ImportEntry.owner_id == owner_id,
                (ImportEntry.item_id == item_id)
                | (
                    ImportEntry.item_id.is_(None)
                    & (ImportEntry.source_url_norm == item_url_norm)
                ),
                ImportEntry.import_id.in_(import_ids),
            )
            .order_by(ImportEntry.import_id)
            .with_for_update()
        )
    ).scalars().all()
    if not entries:
        return
    for entry in entries:
        entry.item_id = item_id
        entry.state = state
        entry.last_error_code = error_code if state in {"retryable_failed", "terminal_failed"} else None
        entry.updated_at = datetime.now(UTC)
    for import_row in imports:
        await sync_import_collection_memberships(
            session, import_id=import_row.id, owner_id=owner_id
        )
        progress = await derive_import_progress(
            session, import_id=import_row.id, total_parsed=import_row.total_parsed
        )
        pending = await session.scalar(
            select(func.count())
            .select_from(ImportEntry)
            .where(
                ImportEntry.import_id == import_row.id,
                ImportEntry.state.not_in(("completed", "terminal_failed")),
            )
        )
        terminal = int(pending or 0) == 0
        final_state = (
            "failed" if terminal and progress.full_done == 0 and progress.failed > 0
            else "complete" if terminal
            else "indexing"
        )
        # The importer can still be adding later chunks. Item workers may
        # finish early, so only the importer finalizer may close its manifest.
        if import_row.state == "processing":
            final_state = "processing"
        await session.execute(
            update(Import)
            .where(Import.id == import_row.id, Import.user_id == owner_id)
            .values(
                total_parsed=progress.total_parsed,
                full_queued=progress.full_queued,
                text_indexed_done=progress.text_indexed_done,
                full_done=progress.full_done,
                failed=progress.failed,
                state=final_state,
                finished_at=(datetime.now(UTC) if terminal and final_state != "processing" else None),
            )
        )
        import_row.total_parsed = progress.total_parsed
        import_row.full_queued = progress.full_queued
        import_row.text_indexed_done = progress.text_indexed_done
        import_row.full_done = progress.full_done
        import_row.failed = progress.failed
        import_row.state = final_state
        import_row.finished_at = datetime.now(UTC) if terminal and final_state != "processing" else None
        if terminal and final_state in {"complete", "failed"}:
            await record_import_summary_intent(
                session, owner_id=owner_id, import_id=import_row.id
            )


def _occurrence_values(
    *,
    import_id: str,
    owner_id: str,
    position: int,
    kind: str,
    outcome: str,
    entry_key: str | None,
    source_url_norm: str | None,
    collection_key: str | None,
    collection_name: str | None,
    saved_at: datetime | None,
) -> dict[str, Any]:
    identity = "\0".join(
        (import_id, str(position), kind, outcome, entry_key or "", collection_key or "")
    )
    occurrence_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    if saved_at is not None and saved_at.tzinfo is None:
        saved_at = saved_at.replace(tzinfo=UTC)
    return {
        "import_id": import_id,
        "occurrence_key": occurrence_key,
        "owner_id": owner_id,
        "source_position": position,
        "source_kind": kind,
        "outcome": outcome,
        "entry_reservation_key": entry_key,
        "source_url_norm": source_url_norm,
        "collection_key": collection_key,
        "collection_name": collection_name,
        "saved_at": saved_at,
    }


def _collection_key(index: int, name: str, collection_type: str | None) -> str:
    stable = "\0".join((str(index), name, collection_type or ""))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _import_collection_id(import_id: str, collection_key: str) -> str:
    return "import-" + hashlib.sha256(f"{import_id}\0{collection_key}".encode()).hexdigest()[:32]


def _import_collection_display_name(
    source_name: str,
    *,
    import_id: str,
    collection_key: str,
) -> str:
    stable_identity = hashlib.sha256(f"{import_id}\0{collection_key}".encode()).hexdigest()[:24]
    suffix = f" · import {stable_identity}"
    return f"{source_name[: max(1, 240 - len(suffix))]}{suffix}"
