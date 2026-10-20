from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator

import pytest

from app.services.memory import pgvector_store
from app.services.memory.base import IndexedItem
from app.services.memory.pgvector_store import PgVectorStore, _lease_current_raw
from app.tasks.worker_claims import WorkerIdentity, WorkerLease


class _FenceConnection:
    def __init__(self, *, now: datetime, lease_until: datetime) -> None:
        self.now = now
        self.profile = {"deleted_at": None}
        self.item = {
            "user_id": "owner",
            "worker_lease_token": "token-that-must-not-leak",
            "worker_lease_until": lease_until,
            "worker_task_type": "ingest",
            "worker_generation": 4,
        }
        self.job = {
            "task_type": "ingest",
            "target_type": "item",
            "target_id": "item",
            "generation": 4,
            "execution_status": "pending",
        }
        self.calls: list[str] = []

    async def fetchrow(self, query: str, *_args: Any) -> dict[str, Any] | None:
        self.calls.append(query)
        if "FROM profiles" in query:
            return self.profile
        if "FROM items" in query:
            return self.item
        if "FROM task_outbox" in query:
            return self.job
        raise AssertionError("unexpected fence query")

    async def fetchval(self, query: str, *_args: Any) -> Any:
        self.calls.append(query)
        if "max(generation)" in query:
            return 4
        if "clock_timestamp()" in query:
            return self.now
        raise AssertionError("unexpected fence scalar query")


def _lease() -> WorkerLease:
    return WorkerLease(
        identity=WorkerIdentity(
            outbox_id="outbox",
            task_type="ingest",
            target_type="item",
            target_id="item",
            generation=4,
        ),
        item_id="item",
        user_id="owner",
        token="token-that-must-not-leak",
    )


@pytest.mark.asyncio
async def test_raw_fence_accepts_live_lease_using_database_clock() -> None:
    database_now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    conn = _FenceConnection(now=database_now, lease_until=database_now + timedelta(minutes=5))

    valid, failed = await _lease_current_raw(conn, _lease(), "owner", "item")

    assert valid is True
    assert failed == ()
    assert any("FOR UPDATE" in query and "profiles" in query for query in conn.calls)
    assert any("FOR UPDATE" in query and "items" in query for query in conn.calls)
    assert any("FOR UPDATE" in query and "task_outbox" in query for query in conn.calls)
    assert "SELECT clock_timestamp()" in conn.calls


@pytest.mark.asyncio
async def test_raw_fence_reports_only_failed_predicate_names() -> None:
    database_now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    conn = _FenceConnection(now=database_now, lease_until=database_now - timedelta(seconds=1))

    valid, failed = await _lease_current_raw(conn, _lease(), "owner", "item")

    assert valid is False
    assert failed == ("lease_expired",)
    diagnostic = ", ".join(failed)
    assert "token-that-must-not-leak" not in diagnostic
    assert "owner" not in diagnostic
    assert "item" not in diagnostic


@pytest.mark.asyncio
async def test_publication_failure_keeps_predicates_out_of_persisted_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Connection:
        def __init__(self) -> None:
            self.writes = 0

        @asynccontextmanager
        async def transaction(self) -> AsyncIterator[None]:
            yield

        async def execute(self, *_args: Any) -> None:
            self.writes += 1

    class Pool:
        def __init__(self) -> None:
            self.connection = Connection()

        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[Connection]:
            yield self.connection

    pool = Pool()
    store = PgVectorStore(pool)  # type: ignore[arg-type]

    async def embed(_texts: list[str]) -> list[list[float]]:
        return [[0.0] * 768]

    async def fence(*_args: Any) -> tuple[bool, tuple[str, ...]]:
        return False, ("lease_token_mismatch",)

    async def register(_conn: Any) -> None:
        return None

    monkeypatch.setattr(store, "_embed_texts", embed)
    monkeypatch.setattr(pgvector_store, "_lease_current_raw", fence)
    monkeypatch.setattr(pgvector_store, "register_vector", register)

    with pytest.raises(RuntimeError, match="^worker lease lost before vector publication$") as exc:
        await store.write(
            "owner",
            IndexedItem(item_id="item", text_corpus="private content", metadata={}),
            worker_lease=_lease(),
        )

    assert "lease_token_mismatch" not in str(exc.value)
    assert "token-that-must-not-leak" not in str(exc.value)
    assert pool.connection.writes == 0
