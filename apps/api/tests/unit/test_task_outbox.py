"""Outbox identity and transaction-boundary unit checks."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import pytest

from app.tasks import enqueue, outbox
from app.tasks.enqueue import logical_task_name_key
from app.tasks.outbox import dispatch_after_commit, dispatch_outbox_job, record_task_intent


class _Result:
    def __init__(self, inserted_id: str | None = None, row: Any = None) -> None:
        self.inserted_id = inserted_id
        self.row = row

    def scalar_one_or_none(self) -> str | None:
        return self.inserted_id

    def scalar_one(self) -> Any:
        return self.row


class _Session:
    def __init__(self) -> None:
        self.statements: list[Any] = []
        self.commits = 0
        self.row = object()

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        if str(statement).lstrip().startswith("INSERT"):
            return _Result(inserted_id="outbox-1")
        return _Result(row=self.row)

    def add(self, _row: Any) -> None:
        raise AssertionError("outbox insert should use a conflict-safe statement")

    async def commit(self) -> None:
        self.commits += 1


class _Claim:
    id = "outbox-1"
    task_type = "ingest"
    target_type = "item"
    target_id = "item-7"
    generation = 4
    payload: ClassVar[dict[str, str]] = {"item_id": "item-7", "user_id": "user-2"}
    attempt_count = 1


class _Context:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


class _DispatchSession:
    def __init__(self, results: list[Any]) -> None:
        self.results = iter(results)
        self.statements: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> Any:
        self.statements.append(statement)
        return next(self.results)

    async def commit(self) -> None:
        self.commits += 1


class _ScalarResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar_one_or_none(self) -> Any:
        return self.value


class _RowsResult:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def scalar_one_or_none(self) -> Any:
        return self.values[0] if self.values else None

    def scalars(self) -> _RowsResult:
        return self

    def all(self) -> list[Any]:
        return self.values


class _DueJobsSession:
    def __init__(self, pending_at: Any, ids: list[str]) -> None:
        self.results = iter([_RowsResult([pending_at]), _RowsResult(ids)])

    async def execute(self, _statement: Any) -> _RowsResult:
        return next(self.results)


def test_logical_task_name_includes_generation() -> None:
    name = logical_task_name_key("ingest", "item", "item-7", 1)

    assert name == logical_task_name_key("ingest", "item", "item-7", 1)
    assert name != logical_task_name_key("ingest", "item", "item-7", 2)
    assert name != logical_task_name_key(
        "ingest", "item", "item-7", 1, delivery_attempt=2
    )


@pytest.mark.asyncio
async def test_retry_generation_locks_profile_then_item_before_reading_outbox() -> None:
    class Scalar:
        def __init__(self, value: Any) -> None:
            self.value = value

        def scalar_one_or_none(self) -> Any:
            return self.value

    class Session:
        def __init__(self) -> None:
            self.statements: list[Any] = []
            self.results = iter(
                [
                    Scalar("user-2"),
                    Scalar(SimpleNamespace(deleted_at=None)),
                    Scalar(SimpleNamespace(id="item-7")),
                    Scalar(4),
                ]
            )

        async def execute(self, statement: Any) -> Scalar:
            self.statements.append(statement)
            return next(self.results)

    session = Session()
    generation = await outbox.next_generation(session, "ingest", "item", "item-7")
    sql = [str(statement) for statement in session.statements]

    assert generation == 5
    assert "FROM profiles" in sql[1]
    assert "FROM items" in sql[2]
    assert "FROM task_outbox" in sql[3]


@pytest.mark.asyncio
async def test_intent_is_written_without_committing_callers_transaction() -> None:
    session = _Session()

    row = await record_task_intent(
        session,  # type: ignore[arg-type]
        "ingest",
        {"item_id": "item-7", "user_id": "user-2", "generation": 99},
        target_type="item",
        target_id="item-7",
        generation=3,
    )

    insert_params = session.statements[0].compile().params
    assert insert_params["payload"] == {
        "item_id": "item-7",
        "user_id": "user-2",
        "generation": 3,
    }
    assert row is session.row
    assert session.commits == 0


@pytest.mark.asyncio
async def test_local_prompt_dispatch_returns_while_runner_handler_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_prompt_delivery(*_args: Any) -> bool:
        raise AssertionError("request path must not execute local handlers")

    monkeypatch.setattr(outbox, "uses_local_transport", lambda: True)
    monkeypatch.setattr(outbox, "dispatch_outbox_job", unexpected_prompt_delivery)

    await asyncio.wait_for(dispatch_after_commit(["outbox-1"]), timeout=0.05)


@pytest.mark.asyncio
async def test_stale_success_finalization_cannot_clear_reclaimed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim_session = _DispatchSession([_ScalarResult(_Claim())])
    final_session = _DispatchSession([_ScalarResult(None)])
    sessions = iter([claim_session, final_session])
    monkeypatch.setattr(outbox, "session_scope", lambda: _Context(next(sessions)))
    monkeypatch.setattr(outbox, "uses_local_transport", lambda: False)
    delivered: dict[str, Any] = {}

    async def capture_delivery(_task_type: str, payload: dict[str, Any], **kwargs: Any) -> None:
        delivered.update(payload)
        delivered.update(kwargs)

    monkeypatch.setattr(outbox, "enqueue_task", capture_delivery)
    monkeypatch.setattr(outbox.ulid, "new", lambda: "lease-a")

    # Transport accepted the delivery, but the stale lease could not finalize
    # it. This is contention, not a transport failure.
    assert await dispatch_outbox_job("outbox-1") is None
    assert claim_session.commits == 1
    assert delivered["outbox_id"] == "outbox-1"
    assert delivered["generation"] == 4
    assert delivered["logical_key"] == logical_task_name_key(
        "ingest", "item", "item-7", 4, delivery_attempt=2
    )
    assert final_session.commits == 0
    assert "lease_token" in str(final_session.statements[0])
    assert "UPDATE" in str(final_session.statements[0]).upper()


@pytest.mark.asyncio
async def test_batch_records_row_failure_and_drains_blocked_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sibling_started = asyncio.Event()
    failure_started = asyncio.Event()
    release_sibling = asyncio.Event()
    sibling_finished = asyncio.Event()
    monkeypatch.setattr(
        outbox,
        "session_scope",
        lambda: _Context(_DueJobsSession(datetime.now(UTC), ["fails", "blocked"])),
    )

    async def dispatch(job_id: str) -> bool | None:
        if job_id == "fails":
            failure_started.set()
            raise RuntimeError("transient database failure")
        sibling_started.set()
        await release_sibling.wait()
        sibling_finished.set()
        return True

    monkeypatch.setattr(outbox, "dispatch_outbox_job", dispatch)
    batch = asyncio.create_task(outbox.dispatch_due_jobs(limit=2))
    try:
        await asyncio.wait_for(failure_started.wait(), timeout=1)
        await asyncio.wait_for(sibling_started.wait(), timeout=1)
        # Let the failing child raise. The batch must still own and await the
        # sibling before returning to a caller that may close the DB pool.
        await asyncio.sleep(0)
        assert not batch.done()
        assert not sibling_finished.is_set()
    finally:
        release_sibling.set()

    stats = await asyncio.wait_for(batch, timeout=1)
    assert sibling_finished.is_set()
    assert stats.scanned == 2
    assert stats.failed == 1
    assert stats.dispatched == 1
    assert stats.skipped == 0


@pytest.mark.asyncio
async def test_batch_cancellation_drains_children_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = 0
    all_started = asyncio.Event()
    all_drained = asyncio.Event()
    release = asyncio.Event()
    drained = 0
    monkeypatch.setattr(
        outbox,
        "session_scope",
        lambda: _Context(_DueJobsSession(datetime.now(UTC), ["first", "second"])),
    )

    async def dispatch(_job_id: str) -> bool:
        nonlocal started, drained
        started += 1
        if started == 2:
            all_started.set()
        try:
            await release.wait()
            return True
        finally:
            drained += 1
            if drained == 2:
                all_drained.set()

    monkeypatch.setattr(outbox, "dispatch_outbox_job", dispatch)
    batch = asyncio.create_task(outbox.dispatch_due_jobs(limit=2))
    await asyncio.wait_for(all_started.wait(), timeout=1)
    batch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(batch, timeout=1)
    assert all_drained.is_set()


@pytest.mark.asyncio
async def test_dispatch_claim_contention_is_not_reported_as_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim_session = _DispatchSession([_ScalarResult(None)])
    monkeypatch.setattr(outbox, "session_scope", lambda: _Context(claim_session))

    assert await dispatch_outbox_job("already-claimed") is None
    assert claim_session.commits == 0


@pytest.mark.asyncio
async def test_queue_failure_records_exponential_backoff_and_keeps_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim_session = _DispatchSession([_ScalarResult(_Claim())])
    failure_session = _DispatchSession([_ScalarResult(2), _ScalarResult("outbox-1")])
    sessions = iter([claim_session, failure_session])
    monkeypatch.setattr(outbox, "session_scope", lambda: _Context(next(sessions)))
    monkeypatch.setattr(outbox, "uses_local_transport", lambda: False)

    async def fail_push(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(outbox, "enqueue_task", fail_push)

    assert await dispatch_outbox_job("outbox-1") is False
    failure_update = failure_session.statements[-1]
    assert "lease_token" in str(failure_update)
    assert "attempt_count" in str(failure_update)
    assert failure_session.commits == 1


@pytest.mark.asyncio
async def test_cloud_already_exists_is_success_with_bounded_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AlreadyExistsError(Exception):
        pass

    observed: dict[str, Any] = {}

    class Client:
        def create_task(self, request: Any, **kwargs: Any) -> None:
            observed["request"] = request
            observed.update(kwargs)
            raise AlreadyExistsError()

    api_core = ModuleType("google.api_core")
    api_core.__path__ = []  # type: ignore[attr-defined]
    exceptions = ModuleType("google.api_core.exceptions")
    exceptions.AlreadyExists = AlreadyExistsError  # type: ignore[attr-defined]
    retry_module = ModuleType("google.api_core.retry")
    retry_module.Retry = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    cloud = ModuleType("google.cloud")
    cloud.__path__ = []  # type: ignore[attr-defined]
    tasks_v2 = ModuleType("google.cloud.tasks_v2")
    tasks_v2.CloudTasksClient = Client  # type: ignore[attr-defined]
    tasks_v2.HttpMethod = SimpleNamespace(POST="POST")  # type: ignore[attr-defined]
    tasks_v2.CreateTaskRequest = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    google = ModuleType("google")
    google.__path__ = []  # type: ignore[attr-defined]
    protobuf = ModuleType("google.protobuf")
    protobuf.__path__ = []  # type: ignore[attr-defined]
    duration_pb2 = ModuleType("google.protobuf.duration_pb2")
    duration_pb2.Duration = lambda **kwargs: SimpleNamespace(**kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "google.api_core.retry", retry_module)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.tasks_v2", tasks_v2)
    monkeypatch.setitem(sys.modules, "google.protobuf", protobuf)
    monkeypatch.setitem(sys.modules, "google.protobuf.duration_pb2", duration_pb2)
    monkeypatch.setattr(
        enqueue,
        "get_settings",
        lambda: SimpleNamespace(
            environment="staging",
            worker_base_url="https://worker.example",
            gcp_project_id="test-project",
            cloud_tasks_oidc_sa_email="tasks@example.iam.gserviceaccount.com",
            cloud_tasks_queue_ingest="ingest",
            cloud_tasks_queue_import="imports",
            cloud_tasks_queue_enhance="enhance",
            gcp_region="us-central1",
        ),
    )

    await enqueue._enqueue_cloud_task(
        "ingest", {"item_id": "item-7", "generation": 4}, logical_key="ingest-item-7-g4"
    )

    assert observed["timeout"] == 5.0
    assert observed["retry"].deadline == 3.0
    assert observed["request"].task["name"].endswith("/ingest-item-7-g4")
    assert observed["request"].task["dispatch_deadline"].seconds == 1800


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    return None
