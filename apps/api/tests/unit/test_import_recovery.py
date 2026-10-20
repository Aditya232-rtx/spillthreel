"""Durable intent regressions for the inline import crash window."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import UTC, datetime, timedelta

import pytest

from app.services.imports import recovery

_IMPORT_ID = "01JIMPORTRECOVERY0000000000"
_USER_ID = "user-test"
_STORAGE_KEY = f"{_USER_ID}/{_IMPORT_ID}.zip"
_KEY_A = "a" * 64
_KEY_B = "b" * 64


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _TransactionContext:
    async def __aenter__(self) -> _TransactionContext:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _ReconciliationSession:
    def __init__(self, results: list[object]) -> None:
        self.results = iter(results)
        self.statements: list[object] = []
        self.connection_options: dict[str, object] | None = None
        self.transaction_started = False

    async def __aenter__(self) -> _ReconciliationSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _TransactionContext:
        self.transaction_started = True
        return _TransactionContext()

    async def connection(self, *, execution_options: dict[str, object]) -> object:
        self.connection_options = execution_options
        return object()

    async def execute(self, statement: object) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult(next(self.results))


class _BasicSession:
    def __init__(self, results: list[object]) -> None:
        self.results = iter(results)
        self.statements: list[object] = []
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, statement: object) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult(next(self.results))

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class _SweepResult(_ScalarResult):
    def __init__(self, value: object, rows: list[object] | None = None) -> None:
        super().__init__(value)
        self.rows = rows or []

    def all(self) -> list[object]:
        return self.rows


class _SweepSession:
    def __init__(self, candidate: tuple[str, str, str, str], marker: object) -> None:
        self.candidate = candidate
        self.marker = marker
        self.execute_count = 0
        self.options: list[dict[str, object]] = []

    def begin(self) -> _TransactionContext:
        return _TransactionContext()

    async def connection(self, *, execution_options: dict[str, object]) -> object:
        self.options.append(execution_options)
        return object()

    async def execute(self, _statement: object) -> _SweepResult:
        self.execute_count += 1
        if self.execute_count == 1:
            return _SweepResult(None, [self.candidate])
        if self.execute_count == 2:
            return _SweepResult(SimpleNamespace(id=self.candidate[1]))
        return _SweepResult(self.marker)

    async def scalar(self, _statement: object) -> None:
        return None


class _TransactionContext:
    async def __aenter__(self) -> _TransactionContext:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _SessionContext:
    def __init__(self, session: object) -> None:
        self.session = session

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None

    def begin(self) -> _TransactionContext:
        return _TransactionContext()


@pytest.mark.asyncio
async def test_retry_after_admission_commit_reuses_durable_processing_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost request after commit must leave one worker intent, not re-admit."""
    # Model the durable logical-job uniqueness enforced by the real outbox.
    intents: dict[tuple[str, str, str, int], SimpleNamespace] = {}

    async def record_intent(
        _session: object,
        task_type: str,
        payload: dict[str, object],
        *,
        target_type: str,
        target_id: str,
        generation: int = 1,
    ) -> SimpleNamespace:
        identity = (task_type, target_type, target_id, generation)
        if identity not in intents:
            intents[identity] = SimpleNamespace(id="outbox-1", payload=payload)
        return intents[identity]

    record_mock = AsyncMock(side_effect=record_intent)
    monkeypatch.setattr(recovery, "record_task_intent", record_mock)
    session = object()  # caller-owned transaction; helper must not commit it

    # Intake has already committed its reservation and Import row. The process
    # crashes before parsing/processing, then reconciliation repeats this call.
    first_id = await recovery.record_import_processing_intent(
        session,
        import_id=_IMPORT_ID,
        user_id=_USER_ID,
        storage_key=_STORAGE_KEY,
        admitted_reservation_keys={_KEY_B, _KEY_A, _KEY_A},
    )
    retry_id = await recovery.record_import_processing_intent(
        session,
        import_id=_IMPORT_ID,
        user_id=_USER_ID,
        storage_key=_STORAGE_KEY,
        admitted_reservation_keys={_KEY_A, _KEY_B},
    )

    assert first_id == retry_id == "outbox-1"
    assert len(intents) == 1
    assert record_mock.await_count == 2
    assert record_mock.await_args.kwargs == {
        "target_type": "import",
        "target_id": _IMPORT_ID,
    }
    assert intents[("import", "import", _IMPORT_ID, 1)].payload == {
        "import_id": _IMPORT_ID,
        "user_id": _USER_ID,
        "storage_key": _STORAGE_KEY,
        "admitted_reservation_keys": [_KEY_A, _KEY_B],
        "already_reserved_reservation_keys": [],
    }


@pytest.mark.asyncio
async def test_import_intent_rejects_unscoped_source_and_malformed_admission_keys() -> None:
    session = object()
    with pytest.raises(ValueError, match="scoped to its owner"):
        await recovery.record_import_processing_intent(
            session,
            import_id=_IMPORT_ID,
            user_id=_USER_ID,
            storage_key="other-user/source.zip",
            admitted_reservation_keys={_KEY_A},
        )

    with pytest.raises(ValueError, match="SHA-256"):
        await recovery.record_import_processing_intent(
            session,
            import_id=_IMPORT_ID,
            user_id=_USER_ID,
            storage_key=_STORAGE_KEY,
            admitted_reservation_keys={"not-a-digest"},
        )

    with pytest.raises(ValueError, match="at least one"):
        await recovery.record_import_processing_intent(
            session,
            import_id=_IMPORT_ID,
            user_id=_USER_ID,
            storage_key=_STORAGE_KEY,
            admitted_reservation_keys=set(),
        )


@pytest.mark.asyncio
async def test_reconcile_absence_requires_fresh_read_committed_owner_locked_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind = object()
    fake_session = _ReconciliationSession([SimpleNamespace(id=_USER_ID), None, None])

    def session_factory(actual_bind: object, **kwargs: object) -> object:
        assert actual_bind is bind
        assert kwargs == {"class_": recovery.AsyncSession, "expire_on_commit": False}
        return lambda: fake_session

    monkeypatch.setattr(recovery, "async_sessionmaker", session_factory)
    outcome = await recovery.reconcile_import_commit(
        bind,  # type: ignore[arg-type]
        import_id=_IMPORT_ID,
        user_id=_USER_ID,
        storage_key=_STORAGE_KEY,
        outbox_id="outbox-1",
    )

    assert outcome is recovery.ImportCommitReconciliation.PROVEN_UNCOMMITTED
    assert fake_session.transaction_started is True
    assert fake_session.connection_options == {"isolation_level": "READ COMMITTED"}
    rendered = [str(statement) for statement in fake_session.statements]
    assert len(rendered) == 3
    assert "FROM profiles" in rendered[0] and "FOR UPDATE" in rendered[0]
    assert "FROM imports" in rendered[1] and "FOR UPDATE" in rendered[1]
    assert "FROM task_outbox" in rendered[2] and "FOR UPDATE" in rendered[2]


@pytest.mark.asyncio
async def test_reconcile_requires_both_matching_import_and_outbox_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind = object()
    outbox = SimpleNamespace(
        id="outbox-1",
        task_type="import",
        target_type="import",
        target_id=_IMPORT_ID,
        payload={
            "import_id": _IMPORT_ID,
            "user_id": _USER_ID,
            "storage_key": _STORAGE_KEY,
        },
    )
    committed = _ReconciliationSession(
        [SimpleNamespace(id=_USER_ID), SimpleNamespace(id=_IMPORT_ID, user_id=_USER_ID), outbox]
    )
    monkeypatch.setattr(
        recovery,
        "async_sessionmaker",
        lambda *_args, **_kwargs: lambda: committed,
    )

    outcome = await recovery.reconcile_import_commit(
        bind,  # type: ignore[arg-type]
        import_id=_IMPORT_ID,
        user_id=_USER_ID,
        storage_key=_STORAGE_KEY,
        outbox_id="outbox-1",
    )
    assert outcome is recovery.ImportCommitReconciliation.COMMITTED


@pytest.mark.asyncio
async def test_staging_marker_is_committed_before_upload_and_owner_locked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker_session = _BasicSession([SimpleNamespace(id=_USER_ID, deleted_at=None)])
    added_at = datetime.now(UTC)
    created = await recovery.create_import_staging_marker(
        marker_session,  # type: ignore[arg-type]
        owner_id=_USER_ID,
        import_id=_IMPORT_ID,
        bucket="exports",
        storage_key=_STORAGE_KEY,
        lease_until=added_at + timedelta(minutes=5),
    )

    assert created is True
    assert marker_session.commits == 1
    assert len(marker_session.added) == 1
    marker = marker_session.added[0]
    assert marker.import_id == _IMPORT_ID
    assert marker.owner_id == _USER_ID
    assert marker.state == "staging"
    assert marker.storage_key == _STORAGE_KEY
    assert "FOR UPDATE" in str(marker_session.statements[0])


@pytest.mark.asyncio
async def test_final_admission_refuses_cleanup_pending_even_after_lease_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    profile = SimpleNamespace(id=_USER_ID, deleted_at=None)
    marker = SimpleNamespace(
        import_id=_IMPORT_ID,
        owner_id=_USER_ID,
        bucket="exports",
        storage_key=_STORAGE_KEY,
        state="cleanup_pending",
        lease_until=now - timedelta(minutes=1),
        cleanup_token="cleanup-token",
    )
    session = _BasicSession([profile, marker])

    result = await recovery.lock_import_staging_for_admission(
        session,  # type: ignore[arg-type]
        owner_id=_USER_ID,
        import_id=_IMPORT_ID,
        bucket="exports",
        storage_key=_STORAGE_KEY,
        now=now,
    )

    assert result is None
    assert len(session.statements) == 2
    assert "FROM profiles" in str(session.statements[0])
    assert "FOR UPDATE" in str(session.statements[0])
    assert "FROM import_staging" in str(session.statements[1])
    assert "FOR UPDATE" in str(session.statements[1])


@pytest.mark.asyncio
async def test_staging_cleanup_claim_verifies_before_storage_and_rechecks_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.media import supabase_storage

    now = datetime.now(UTC)
    marker = SimpleNamespace(
        import_id=_IMPORT_ID,
        owner_id=_USER_ID,
        bucket="exports",
        storage_key=_STORAGE_KEY,
        state="staging",
        lease_until=now - timedelta(seconds=1),
        cleanup_token=None,
        cleanup_lease_until=None,
        cleanup_attempts=0,
        last_error_code=None,
        created_at=now - timedelta(minutes=1),
    )
    session = _SweepSession((_IMPORT_ID, _USER_ID, "exports", _STORAGE_KEY), marker)
    monkeypatch.setattr(
        recovery,
        "_fresh_session_factory",
        lambda _bind: lambda: _SessionContext(session),
    )
    order: list[str] = []

    async def verify(_bind: object, claim: recovery.ImportStagingCleanupClaim) -> bool:
        order.append("verify")
        assert claim.import_id == _IMPORT_ID
        assert claim.cleanup_token == marker.cleanup_token
        assert marker.state == "cleanup_pending"
        return True

    async def storage_delete(bucket: str, key: str) -> None:
        order.append("storage")
        assert bucket == "exports"
        assert key == _STORAGE_KEY

    async def remove(_bind: object, claim: recovery.ImportStagingCleanupClaim) -> bool:
        order.append("remove")
        assert claim.cleanup_token == marker.cleanup_token
        return True

    monkeypatch.setattr(recovery, "verify_import_staging_cleanup_claim", verify)
    monkeypatch.setattr(recovery, "remove_import_staging_after_cleanup", remove)
    monkeypatch.setattr(supabase_storage, "delete_object", storage_delete)

    result = await recovery.sweep_import_staging(
        bind=object(), limit=1, now=now  # type: ignore[arg-type]
    )

    assert result.examined == 1
    assert result.deleted == 1
    assert result.failed == 0
    assert order == ["verify", "storage", "remove"]
    assert session.options == [{"isolation_level": "READ COMMITTED"}]
    assert marker.state == "cleanup_pending"
    assert marker.cleanup_attempts == 1
    assert marker.cleanup_lease_until > now


@pytest.mark.asyncio
async def test_cleanup_pending_transition_preserves_upload_settling_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    now = datetime.now(UTC)
    marker = SimpleNamespace(
        import_id=_IMPORT_ID,
        owner_id=_USER_ID,
        bucket="exports",
        storage_key=_STORAGE_KEY,
        state="staging",
        lease_until=now + timedelta(minutes=4),
        cleanup_token=None,
        cleanup_lease_until=None,
    )
    session = _ReconciliationSession([SimpleNamespace(id=_USER_ID), marker])
    session.scalar = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    @asynccontextmanager
    async def session_scope() -> object:
        yield session

    monkeypatch.setattr(recovery, "_fresh_session_factory", lambda _bind: lambda: session_scope())
    queued = await recovery.mark_import_staging_cleanup_pending(
        object(),  # type: ignore[arg-type]
        owner_id=_USER_ID,
        import_id=_IMPORT_ID,
        bucket="exports",
        storage_key=_STORAGE_KEY,
        now=now,
    )

    assert queued is True
    assert marker.state == "cleanup_pending"
    assert marker.cleanup_token
    assert marker.cleanup_lease_until >= marker.lease_until
    assert marker.cleanup_lease_until > now


@pytest.mark.asyncio
async def test_staging_claim_database_failure_is_reported_as_failed_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedSessionContext:
        async def __aenter__(self) -> object:
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        recovery,
        "_fresh_session_factory",
        lambda _bind: lambda: FailedSessionContext(),
    )

    result = await recovery.sweep_import_staging(bind=object(), limit=1)  # type: ignore[arg-type]

    assert result.failed == 1
    assert result.retained == 0
