"""Focused unit coverage for resumable, fenced account deletion checkpoints.

The SQL boundary is a stateful AsyncSession fake so these tests can verify the
service's sequencing and compare-and-swap behavior without a database or any
provider credentials. PostgreSQL lock/concurrency behavior is covered by the
separate integration suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Delete, Insert, Update
from sqlalchemy.ext.asyncio import AsyncSession

import app.services.users.deletion as deletion
from app.db.models import Profile, UserDeletionJob
from app.tasks.worker_claims import TargetExecution, WorkerIdentity

_USER_ID = "123e4567-e89b-12d3-a456-426614174000"
_TOKEN = "lease-token-current"
_STEPS = ("storage_media", "storage_exports", "vectors", "profile", "auth")


class _Result:
    def __init__(self, value: Any = None, *, rowcount: int = 1) -> None:
        self.value = value
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self.value


class _DeletionSession(AsyncSession):
    """Small stateful stand-in that still satisfies the service's type guard."""

    def __init__(self, initial_step: str, *, token: str = _TOKEN) -> None:
        super().__init__(bind=None)
        self.checkpoint: SimpleNamespace | None = SimpleNamespace(
            user_id=_USER_ID, next_step=initial_step
        )
        self.profile: Profile | None = Profile(
            id=_USER_ID, display_name="Test", deleted_at=datetime.now(timezone.utc)
        )
        self.token = token
        self.open = False
        self.commits = 0
        self.statements: list[Any] = []
        self.scope_id = 0
        self.sql_events: list[tuple[str, str, int, bool]] = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> _Result:
        self.statements.append(statement)
        if isinstance(statement, Insert):
            self.sql_events.append(("insert", statement.table.name, self.scope_id, self.open))
        elif isinstance(statement, Update):
            self.sql_events.append(("update", statement.table.name, self.scope_id, self.open))
        elif isinstance(statement, Delete):
            self.sql_events.append(("delete", statement.table.name, self.scope_id, self.open))
        else:
            sources = statement.get_final_froms() if hasattr(statement, "get_final_froms") else []
            for source in sources:
                if hasattr(source, "name"):
                    self.sql_events.append(("select", source.name, self.scope_id, self.open))

        if isinstance(statement, Insert):
            if self.checkpoint is None:
                self.checkpoint = SimpleNamespace(user_id=_USER_ID, next_step="storage_media")
            return _Result()

        if isinstance(statement, Update) and statement.table.name == "user_deletion_jobs":
            values = statement._values or {}
            next_value = next(
                (value for column, value in values.items() if column.name == "next_step"),
                None,
            )
            candidate = getattr(next_value, "value", next_value)
            expected_for_candidate = {
                "storage_exports": "storage_media",
                "vectors": "storage_exports",
                "profile": "vectors",
                "auth": "profile",
                "complete": "auth",
            }.get(candidate)
            if self.checkpoint is not None and self.checkpoint.next_step == expected_for_candidate:
                self.checkpoint.next_step = candidate
                return _Result(rowcount=1)
            return _Result(rowcount=0)

        if isinstance(statement, Delete):
            if statement.table.name == "profiles":
                self.profile = None
            elif statement.table.name == "user_deletion_jobs":
                self.checkpoint = None
            return _Result()

        sources = statement.get_final_froms() if hasattr(statement, "get_final_froms") else []
        table_names = {source.name for source in sources if hasattr(source, "name")}
        if "profiles" in table_names:
            return _Result(self.profile)
        if "task_outbox" in table_names:
            return _Result(
                SimpleNamespace(
                    id="outbox-1",
                    task_type="delete_user",
                    target_type="user",
                    target_id=_USER_ID,
                    generation=3,
                    execution_status="pending",
                    execution_lease_token=self.token,
                    execution_lease_until=datetime.now(timezone.utc) + timedelta(minutes=2),
                )
            )
        if "user_deletion_jobs" in table_names:
            return _Result(self.checkpoint)
        raise AssertionError(f"unexpected SQL in deletion test: {statement}")

    async def scalar(self, statement: Any, *args: Any, **kwargs: Any) -> int:
        self.statements.append(statement)
        return 3

    async def commit(self) -> None:
        self.commits += 1
        self.open = False


def _execution(*, token: str = _TOKEN, target_id: str = _USER_ID) -> TargetExecution:
    return TargetExecution(
        identity=WorkerIdentity(
            outbox_id="outbox-1",
            task_type="delete_user",
            target_type="user",
            target_id=target_id,
            generation=3,
        ),
        token=token,
        owner_id=_USER_ID,
    )


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial_step: str = "storage_media",
    current_token: str = _TOKEN,
) -> tuple[_DeletionSession, list[tuple[str, bool]], AsyncMock]:
    session = _DeletionSession(initial_step, token=current_token)
    effects: list[tuple[str, bool]] = []

    @asynccontextmanager
    async def scope() -> AsyncIterator[_DeletionSession]:
        session.scope_id += 1
        session.open = True
        try:
            yield session
        finally:
            session.open = False

    import app.db.engine as engine
    import app.services.media.supabase_storage as storage
    import app.services.memory.pgvector_store as vectors

    monkeypatch.setattr(engine, "session_scope", scope)
    monkeypatch.setattr(engine, "get_pg_pool", AsyncMock(return_value=object()))

    async def delete_prefix(bucket: str, prefix: str) -> int:
        effects.append((f"storage:{bucket}", session.open))
        assert prefix == f"{_USER_ID}/"
        return 0

    class _VectorStore:
        def __init__(self, _pool: Any) -> None:
            pass

        async def delete_namespace(self, user_id: str) -> None:
            effects.append(("vectors", session.open))
            assert user_id == _USER_ID

    async def record_auth(_user_id: str) -> None:
        await _record_auth(effects, session)

    auth = AsyncMock(side_effect=record_auth)
    monkeypatch.setattr(storage, "delete_prefix", delete_prefix)
    monkeypatch.setattr(vectors, "PgVectorStore", _VectorStore)
    monkeypatch.setattr(deletion, "delete_auth_user", auth)
    monkeypatch.setattr(
        deletion,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_bucket_media="media",
            supabase_bucket_exports="exports",
        ),
    )
    return session, effects, auth


async def _record_auth(effects: list[tuple[str, bool]], session: _DeletionSession) -> None:
    effects.append(("auth", session.open))


@pytest.mark.asyncio
async def test_deletion_runs_each_step_in_order_outside_transactions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, effects, auth = _install_fakes(monkeypatch)

    result = await deletion.delete_user_account(_USER_ID, _execution())

    assert result == {"status": "ok", "user_id": _USER_ID}
    assert effects == [
        ("storage:media", False),
        ("storage:exports", False),
        ("vectors", False),
        ("auth", False),
    ]
    # The profile step is database-only and the minimal no-recreation
    # tombstone remains after Auth succeeds.
    assert session.profile is None
    assert session.checkpoint is not None
    assert session.checkpoint.next_step == "complete"
    auth.assert_awaited_once_with(_USER_ID)
    assert session.commits == 10


@pytest.mark.asyncio
async def test_retry_resumes_at_persisted_checkpoint_without_repeating_prior_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, effects, auth = _install_fakes(monkeypatch, initial_step="storage_exports")

    await deletion.delete_user_account(_USER_ID, _execution())

    assert effects == [
        ("storage:exports", False),
        ("vectors", False),
        ("auth", False),
    ]
    assert session.checkpoint is not None
    assert session.checkpoint.next_step == "complete"
    auth.assert_awaited_once_with(_USER_ID)


@pytest.mark.asyncio
async def test_auth_failure_keeps_auth_checkpoint_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, effects, auth = _install_fakes(monkeypatch, initial_step="auth")
    auth_calls = 0

    async def fail_once(_user_id: str) -> None:
        nonlocal auth_calls
        auth_calls += 1
        effects.append(("auth", session.open))
        if auth_calls == 1:
            raise RuntimeError("synthetic auth outage")

    auth.side_effect = fail_once

    with pytest.raises(RuntimeError, match="synthetic auth outage"):
        await deletion.delete_user_account(_USER_ID, _execution())

    assert session.checkpoint is not None
    assert session.checkpoint.next_step == "auth"
    assert effects == [("auth", False)]

    result = await deletion.delete_user_account(_USER_ID, _execution())

    assert result == {"status": "ok", "user_id": _USER_ID}
    assert session.checkpoint is not None
    assert session.checkpoint.next_step == "complete"
    assert effects == [("auth", False), ("auth", False)]
    assert auth.await_count == 2


@pytest.mark.asyncio
async def test_profile_delete_and_checkpoint_advance_share_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _, _ = _install_fakes(monkeypatch, initial_step="profile")

    await deletion.delete_user_account(_USER_ID, _execution())

    profile_delete = next(
        index
        for index, (operation, table, _, _) in enumerate(session.sql_events)
        if operation == "delete" and table == "profiles"
    )
    checkpoint_advance = next(
        index
        for index, (operation, table, _, _) in enumerate(session.sql_events)
        if operation == "update" and table == "user_deletion_jobs"
    )
    delete_event = session.sql_events[profile_delete]
    advance_event = session.sql_events[checkpoint_advance]

    assert profile_delete < checkpoint_advance
    assert delete_event[2] == advance_event[2]
    assert delete_event[3] is True and advance_event[3] is True


@pytest.mark.asyncio
async def test_stale_lease_cannot_advance_after_idempotent_external_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, effects, auth = _install_fakes(monkeypatch)

    async def takeover(bucket: str, prefix: str) -> int:
        effects.append((f"storage:{bucket}", session.open))
        assert prefix == f"{_USER_ID}/"
        # Model a new claimant taking over while the external request is in flight.
        session.token = "lease-token-takeover"
        return 0

    import app.services.media.supabase_storage as storage

    monkeypatch.setattr(storage, "delete_prefix", takeover)
    with pytest.raises(deletion.DeletionExecutionLost):
        await deletion.delete_user_account(_USER_ID, _execution())

    assert effects == [("storage:media", False)]
    assert session.checkpoint is not None
    assert session.checkpoint.next_step == "storage_media"
    auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_execution_identity_is_rejected_before_database_or_provider_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, effects, auth = _install_fakes(monkeypatch)

    with pytest.raises(deletion.DeletionExecutionLost):
        await deletion.delete_user_account(
            _USER_ID, _execution(target_id="another-user")
        )

    assert session.statements == []
    assert effects == []
    auth.assert_not_awaited()

