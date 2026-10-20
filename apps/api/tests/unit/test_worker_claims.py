from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.tasks import worker_claims
from app.tasks.worker_claims import WorkerIdentity, claim_worker


async def test_missing_or_non_item_identity_is_ack_only() -> None:
    # Invalid identities are rejected before opening a DB session.
    result = await claim_worker(
        WorkerIdentity(
            outbox_id="", task_type="ingest", target_type="item", target_id="x", generation=1
        )
    )
    assert result.status == "invalid"
    assert result.lease is None


async def test_non_media_task_type_is_rejected() -> None:
    result = await claim_worker(
        WorkerIdentity(
            outbox_id="job",
            task_type="delete_user",
            target_type="item",
            target_id="x",
            generation=1,
        )
    )
    assert result.status == "invalid"


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _ClaimSession:
    def __init__(self, *, active_count: int, item_has_live_lease: bool = False) -> None:
        self.user_id = "owner-1"
        self.item = SimpleNamespace(
            id="item-1",
            user_id=self.user_id,
            worker_lease_token="existing" if item_has_live_lease else None,
            worker_lease_until=(
                datetime.now(UTC) + timedelta(minutes=1) if item_has_live_lease else None
            ),
            worker_task_type="ingest" if item_has_live_lease else None,
            worker_generation=1 if item_has_live_lease else None,
        )
        self.profile = SimpleNamespace(id=self.user_id, deleted_at=None)
        self.outbox = SimpleNamespace(
            id="job-1",
            task_type="ingest",
            target_type="item",
            target_id="item-1",
            generation=1,
            execution_status="pending",
            attempt_count=1,
            worker_attempt_count=0,
            worker_last_error_code=None,
            dispatched_at=datetime.now(UTC),
            available_at=datetime.now(UTC),
            lease_token="dispatch-token",
            lease_until=datetime.now(UTC) + timedelta(minutes=1),
        )
        self.scalar_values = [1, active_count]
        self.scalar_statements = []
        self.committed = False

    async def __aenter__(self) -> _ClaimSession:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, statement: object) -> _Result:
        statement_text = str(statement)
        if "profiles" in statement_text:
            return _Result(self.profile)
        if statement_text.startswith("SELECT items.user_id"):
            return _Result(self.user_id)
        if "FROM items" in statement_text:
            return _Result(self.item)
        return _Result(self.outbox)

    async def scalar(self, statement: object) -> int:
        self.scalar_statements.append(statement)
        return self.scalar_values.pop(0)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.parametrize("active_count", [2, 4])
async def test_claim_returns_capacity_at_owner_active_lease_limit(
    monkeypatch: pytest.MonkeyPatch, active_count: int
) -> None:
    session = _ClaimSession(active_count=active_count)

    @asynccontextmanager
    async def fake_session_scope():
        yield session

    monkeypatch.setattr(worker_claims, "session_scope", fake_session_scope)
    monkeypatch.setattr(
        worker_claims,
        "get_settings",
        lambda: SimpleNamespace(worker_max_active_items_per_owner=2),
    )
    result = await claim_worker(WorkerIdentity("job-1", "ingest", "item", "item-1", 1))

    assert result.status == "capacity"
    assert result.lease is None
    assert session.item.worker_lease_token is None
    assert session.outbox.worker_attempt_count == 0
    assert session.outbox.dispatched_at is None
    assert session.outbox.available_at > datetime.now(UTC)
    assert session.outbox.lease_token is None
    assert session.outbox.lease_until is None
    assert session.committed
    capacity_query = str(session.scalar_statements[1])
    assert "items.user_id" in capacity_query
    assert "worker_lease_token IS NOT NULL" in capacity_query
    assert "worker_lease_until" in capacity_query


async def test_claim_below_owner_capacity_still_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ClaimSession(active_count=1)

    @asynccontextmanager
    async def fake_session_scope():
        yield session

    monkeypatch.setattr(worker_claims, "session_scope", fake_session_scope)
    result = await claim_worker(
        WorkerIdentity("job-1", "ingest", "item", "item-1", 1),
        max_active_items=2,
    )

    assert result.status == "claimed"
    assert result.lease is not None
    assert session.item.worker_lease_token == result.lease.token
    assert session.outbox.worker_attempt_count == 1
    assert session.committed


async def test_duplicate_delivery_remains_busy_before_capacity_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _ClaimSession(active_count=2, item_has_live_lease=True)

    @asynccontextmanager
    async def fake_session_scope():
        yield session

    monkeypatch.setattr(worker_claims, "session_scope", fake_session_scope)
    result = await claim_worker(
        WorkerIdentity("job-1", "ingest", "item", "item-1", 1),
        max_active_items=2,
    )

    assert result.status == "busy"
    assert len(session.scalar_statements) == 1  # latest-generation check only
    assert not session.committed
