"""Durable membership regressions for import replay and folder attribution."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.imports.entries as entries
import app.services.imports.ig_pipeline as pipeline
from app.db.models import Import
from app.services.imports.ig_parser import Owner, ParsedExport, SavedCollection, SavedPost

_OWNER = "11111111-1111-1111-1111-111111111111"
_URL = "https://www.instagram.com/reel/DbM7eWINqAy/"
_URL_DUP = "https://www.instagram.com/reel/DbM7eWINqAy/?igsh=abc"
_URL_B = "https://www.instagram.com/p/C8xYz123456/"


def _post(url: str) -> SavedPost:
    return SavedPost(
        url=url,
        saved_at=datetime(2026, 8, 2, 3, 3),
        owner=Owner(username="chef"),
    )


def _parsed(
    *, posts: tuple[SavedPost, ...] = (), collections: tuple[SavedCollection, ...] = ()
) -> ParsedExport:
    return ParsedExport(
        posts=posts,
        collections=collections,
        audio=(),
        parser_schema_hash="schema-v1",
        unknown_label_count=0,
        entries_with_unknowns=0,
    )


class _Session:
    def __init__(self, row: Import, item_state_by_norm: dict[str, str]) -> None:
        self.row = row
        self.item_state_by_norm = item_state_by_norm
        self.commits = 0

    async def execute(self, statement: object) -> object:
        from app.db.models import Item

        descriptions = getattr(statement, "column_descriptions", [])
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is Import:
            return _Result(self.row)
        if entity is Item:
            params = statement.compile().params
            source_norm = next(
                (value for value in params.values() if isinstance(value, str) and value.startswith("https://")),
                None,
            )
            item_id = next(
                (value for key, value in params.items() if key.endswith("id_1") and isinstance(value, str)),
                None,
            )
            if item_id is not None:
                for norm, item in self.item_state_by_norm.items():
                    if item.id == item_id:
                        return _Result(item)
            if source_norm is not None:
                item = self.item_state_by_norm.get(source_norm)
                return _Result(item)
            return _Result(None)
        return _Result(None)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _MemoryMembership:
    def __init__(self) -> None:
        self.occurrences: dict[tuple[str, int], dict[str, object]] = {}
        self.entries: dict[tuple[str, str], SimpleNamespace] = {}
        self.items: dict[str, SimpleNamespace] = {}
        self.intent_ids: dict[str, str] = {}
        self.collections: dict[tuple[str, str], SimpleNamespace] = {}
        self.collection_items: set[tuple[str, str, str]] = set()
        self.next_item = 0
        self.intent_calls = 0

    async def persist(self, _session: object, rows: list[entries.ParsedOccurrence]) -> set[int]:
        new_positions: set[int] = set()
        for occurrence in rows:
            values = occurrence.values
            key = (str(values["import_id"]), int(values["source_position"]))
            if key not in self.occurrences:
                self.occurrences[key] = values
                new_positions.add(key[1])
        return new_positions

    async def ensure_collections(
        self,
        _session: object,
        *,
        import_id: str,
        owner_id: str,
        occurrences: list[entries.ParsedOccurrence],
    ) -> None:
        for occurrence in occurrences:
            key = occurrence.values["collection_key"]
            name = occurrence.values["collection_name"]
            if isinstance(key, str) and isinstance(name, str):
                self.collections.setdefault(
                    (import_id, key),
                    SimpleNamespace(
                        collection_id=entries._import_collection_id(import_id, key),
                        owner_id=owner_id,
                        source_name=name,
                        display_name=entries._import_collection_display_name(
                            name, import_id=import_id, collection_key=key
                        ),
                    ),
                )

    async def sync_collection_items(
        self, _session: object, *, import_id: str, owner_id: str
    ) -> None:
        for (occurrence_import_id, _position), occurrence in self.occurrences.items():
            if occurrence_import_id != import_id or occurrence["owner_id"] != owner_id:
                continue
            key = occurrence["collection_key"]
            entry_key = occurrence["entry_reservation_key"]
            if not isinstance(key, str) or not isinstance(entry_key, str):
                continue
            entry = self.entries.get((import_id, entry_key))
            collection = self.collections.get((import_id, key))
            if entry is not None and entry.item_id is not None and collection is not None:
                self.collection_items.add((collection.collection_id, str(entry.item_id), owner_id))

    async def lock_entry(
        self, _session: object, *, import_id: str, reservation_key: str, **_kwargs: object
    ) -> SimpleNamespace | None:
        return self.entries.get((import_id, reservation_key))

    async def create_entry(
        self,
        _session: object,
        *,
        import_id: str,
        owner_id: str,
        occurrence: entries.MediaOccurrence,
        item_id: str | None,
        state: str,
        work_queued_at: datetime | None,
        last_error_code: str | None = None,
    ) -> SimpleNamespace:
        key = (import_id, occurrence.reservation_key)
        return self.entries.setdefault(
            key,
            SimpleNamespace(
                import_id=import_id,
                owner_id=owner_id,
                reservation_key=occurrence.reservation_key,
                source_url_norm=occurrence.source_url_norm,
                source_bucket=occurrence.source_bucket,
                item_id=item_id,
                state=state,
                work_queued_at=work_queued_at,
                last_error_code=last_error_code,
            ),
        )

    async def save_progress(
        self,
        _session: object,
        import_row: Import,
        *,
        total_parsed: int,
        finalize_manifest: bool = False,
        **_kwargs: object,
    ) -> entries.ImportProgress:
        durable_queued = sum(
            entry.work_queued_at is not None
            for (import_id, _key), entry in self.entries.items()
            if import_id == import_row.id
        )
        durable_done = sum(
            entry.state == "completed"
            for (import_id, _key), entry in self.entries.items()
            if import_id == import_row.id
        )
        import_row.total_parsed = sum(
            import_id == import_row.id for import_id, _position in self.occurrences
        )
        import_row.full_queued = durable_queued
        import_row.full_done = durable_done
        import_row.text_indexed_done = durable_done
        import_row.failed = sum(
            entry.state in {"retryable_failed", "terminal_failed"}
            for (import_id, _key), entry in self.entries.items()
            if import_id == import_row.id
        )
        import_row.state = (
            "failed"
            if finalize_manifest and durable_done == 0 and import_row.failed > 0
            else "complete"
            if finalize_manifest and durable_done + import_row.failed >= len(self.entries)
            else "indexing"
            if finalize_manifest
            else "processing"
        )
        import_row.finished_at = datetime.now() if finalize_manifest else None
        import_row.parser_schema_hash = "schema-v1"
        return entries.ImportProgress(
            import_row.total_parsed,
            durable_queued,
            import_row.text_indexed_done,
            durable_done,
            import_row.failed,
        )

    async def insert_item(self, _session: object, values: dict[str, object]) -> str | None:
        norm = str(values["source_url_norm"])
        if norm in self.items:
            return None
        self.next_item += 1
        item_id = f"item-{self.next_item}"
        self.items[norm] = SimpleNamespace(id=item_id, state="queued", user_id=_OWNER)
        return item_id

    async def record_intent(self, *_args: object, **kwargs: object) -> SimpleNamespace:
        self.intent_calls += 1
        item_id = str(kwargs["target_id"])
        outbox_id = f"outbox-{self.intent_calls}"
        self.intent_ids[item_id] = outbox_id
        return SimpleNamespace(id=outbox_id)

    async def ensure_intent(self, *_args: object, **kwargs: object) -> SimpleNamespace:
        item_id = str(kwargs["target_id"])
        if item_id not in self.intent_ids:
            self.intent_calls += 1
            self.intent_ids[item_id] = f"outbox-{self.intent_calls}"
        return SimpleNamespace(id=self.intent_ids[item_id])


def _install(
    monkeypatch: pytest.MonkeyPatch,
    row: Import,
    memory: _MemoryMembership,
) -> _Session:
    session = _Session(row, memory.items)

    @asynccontextmanager
    async def scope() -> AsyncIterator[_Session]:
        yield session

    monkeypatch.setattr(pipeline, "session_scope", scope)
    monkeypatch.setattr(pipeline, "target_intent_is_current_in_session", AsyncMock(return_value=True))
    monkeypatch.setattr(pipeline, "persist_occurrences", memory.persist)
    monkeypatch.setattr(pipeline, "ensure_import_collections", memory.ensure_collections)
    monkeypatch.setattr(pipeline, "sync_import_collection_memberships", memory.sync_collection_items)
    monkeypatch.setattr(entries, "lock_import_entry", memory.lock_entry)
    monkeypatch.setattr(pipeline, "create_import_entry", memory.create_entry)
    monkeypatch.setattr(pipeline, "save_import_progress", memory.save_progress)
    monkeypatch.setattr(pipeline, "_insert_item_once", memory.insert_item)
    monkeypatch.setattr(pipeline, "record_task_intent", memory.record_intent)
    monkeypatch.setattr(pipeline, "ensure_existing_intent", memory.ensure_intent)
    monkeypatch.setattr(pipeline, "dispatch_after_commit", AsyncMock())
    return session


def _import_row() -> Import:
    return Import(id="01JIMPORTENTRIES00000000000", user_id=_OWNER, state="processing")


def _execution() -> SimpleNamespace:
    return SimpleNamespace(owner_id=_OWNER)


@pytest.mark.asyncio
async def test_two_queued_entries_remain_two_after_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _MemoryMembership()
    row = _import_row()
    _install(monkeypatch, row, memory)
    parsed = _parsed(posts=(_post(_URL),), collections=(SavedCollection("folder", None, None, None, (_post(_URL_B),)),))

    await pipeline.process_import(row.id, _OWNER, parsed, execution=_execution())  # type: ignore[arg-type]
    assert row.full_queued == 2
    assert memory.intent_calls == 2

    for item in memory.items.values():
        item.state = "fully_indexed"
    await pipeline.process_import(row.id, _OWNER, parsed, execution=_execution())  # type: ignore[arg-type]

    assert row.full_queued == 2
    assert row.full_done == 2
    assert memory.intent_calls == 2


@pytest.mark.asyncio
async def test_same_url_occurrences_keep_folder_attribution_once(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _MemoryMembership()
    row = _import_row()
    _install(monkeypatch, row, memory)
    parsed = _parsed(
        posts=(_post(_URL),),
        collections=(
            SavedCollection("Dinner", None, None, None, (_post(_URL_DUP),)),
            SavedCollection("Weekend", None, None, None, (_post(_URL),)),
        ),
    )

    await pipeline.process_import(row.id, _OWNER, parsed, execution=_execution())  # type: ignore[arg-type]
    await pipeline.process_import(row.id, _OWNER, parsed, execution=_execution())  # type: ignore[arg-type]

    assert len(memory.occurrences) == 3
    assert len(memory.entries) == 1
    collections = {
        row["collection_key"]
        for row in memory.occurrences.values()
        if row["collection_key"] is not None
    }
    assert len(collections) == 2
    assert memory.intent_calls == 1
    assert len(memory.collections) == 2
    assert len(memory.collection_items) == 2
    assert all(collection.owner_id == _OWNER for collection in memory.collections.values())
    assert all(" · import " in collection.display_name for collection in memory.collections.values())


@pytest.mark.asyncio
async def test_folder_membership_is_added_when_reserved_item_appears_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _MemoryMembership()
    row = _import_row()
    row.admission_enforced = True
    _install(monkeypatch, row, memory)
    parsed = _parsed(
        collections=(SavedCollection("Dinner", None, None, None, (_post(_URL),)),)
    )
    _, candidates = entries.build_occurrences(parsed, import_id=row.id, owner_id=_OWNER)
    key = next(iter(candidates))

    await pipeline.process_import(
        row.id,
        _OWNER,
        parsed,
        admitted_reservation_keys=set(),
        already_reserved_reservation_keys={key},
        execution=_execution(),  # type: ignore[arg-type]
    )
    assert memory.collection_items == set()

    item_id = "original-item"
    norm = pipeline.normalize_url(_URL)
    memory.items[norm] = SimpleNamespace(id=item_id, state="queued", user_id=_OWNER)
    entry = memory.entries[(row.id, key)]
    entry.item_id = item_id
    await memory.sync_collection_items(None, import_id=row.id, owner_id=_OWNER)
    await memory.sync_collection_items(None, import_id=row.id, owner_id=_OWNER)

    folder_key = next(
        occurrence.values["collection_key"]
        for occurrence in entries.build_occurrences(parsed, import_id=row.id, owner_id=_OWNER)[0]
        if occurrence.values["collection_key"] is not None
    )
    collection_id = memory.collections[(row.id, str(folder_key))].collection_id
    assert memory.collection_items == {(collection_id, item_id, _OWNER)}


@pytest.mark.asyncio
async def test_completed_preexisting_item_is_membership_only(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = _MemoryMembership()
    norm = pipeline.normalize_url(_URL)
    memory.items[norm] = SimpleNamespace(id="existing-complete", state="fully_indexed", user_id=_OWNER)
    row = _import_row()
    _install(monkeypatch, row, memory)

    await pipeline.process_import(
        row.id, _OWNER, _parsed(posts=(_post(_URL),)), execution=_execution()  # type: ignore[arg-type]
    )

    assert len(memory.entries) == 1
    entry = next(iter(memory.entries.values()))
    assert entry.item_id == "existing-complete"
    assert entry.state == "completed"
    assert entry.work_queued_at is None
    assert memory.intent_calls == 0
    assert row.full_queued == 0


@pytest.mark.asyncio
async def test_already_reserved_key_never_mints_replacement_item_or_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _MemoryMembership()
    row = _import_row()
    row.admission_enforced = True
    _install(monkeypatch, row, memory)
    parsed = _parsed(posts=(_post(_URL),))
    _, candidates = entries.build_occurrences(parsed, import_id=row.id, owner_id=_OWNER)
    key = next(iter(candidates))

    await pipeline.process_import(
        row.id,
        _OWNER,
        parsed,
        admitted_reservation_keys=set(),
        already_reserved_reservation_keys={key},
        execution=_execution(),  # type: ignore[arg-type]
    )

    assert len(memory.entries) == 1
    entry = next(iter(memory.entries.values()))
    assert entry.item_id is None
    assert entry.state == "retryable_failed"
    assert entry.last_error_code == "reserved_item_pending"
    assert entry.work_queued_at is None
    assert memory.items == {}
    assert memory.intent_calls == 0


@pytest.mark.asyncio
async def test_admission_filter_excludes_unselected_archive_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _MemoryMembership()
    row = _import_row()
    row.admission_enforced = True
    _install(monkeypatch, row, memory)
    parsed = _parsed(posts=(_post(_URL), _post(_URL_B)))
    _, candidates = entries.build_occurrences(parsed, import_id=row.id, owner_id=_OWNER)
    selected_key = next(iter(candidates))

    await pipeline.process_import(
        row.id,
        _OWNER,
        parsed,
        admitted_reservation_keys={selected_key},
        already_reserved_reservation_keys=set(),
        execution=_execution(),  # type: ignore[arg-type]
    )

    assert set(memory.entries) == {(row.id, selected_key)}
    assert len(memory.items) == 1
    assert memory.intent_calls == 1
    assert row.total_parsed == 2  # Both source occurrences are recorded for truthful progress.
    outcomes = [occurrence["outcome"] for occurrence in memory.occurrences.values()]
    assert outcomes.count("admitted") == 1
    assert outcomes.count("excluded") == 1


@pytest.mark.asyncio
async def test_unrepresentable_admitted_candidate_is_recorded_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = _MemoryMembership()
    row = _import_row()
    _install(monkeypatch, row, memory)
    monkeypatch.setattr(pipeline, "_build_item_values", lambda *_args: None)

    await pipeline.process_import(
        row.id,
        _OWNER,
        _parsed(posts=(_post(_URL),)),
        execution=_execution(),  # type: ignore[arg-type]
    )

    assert len(memory.entries) == 1
    entry = next(iter(memory.entries.values()))
    assert entry.state == "terminal_failed"
    assert entry.last_error_code == "item_unrepresentable"
    assert row.failed == 1
    assert row.state == "failed"
