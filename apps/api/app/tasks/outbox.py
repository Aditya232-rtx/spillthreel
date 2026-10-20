"""Durable task intents and bounded dispatch/reconciliation helpers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import ulid
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import session_scope
from app.db.models import Item, Profile, TaskOutbox
from app.observability.logging import get_logger
from app.tasks.enqueue import (
    TaskType,
    enqueue_task,
    logical_task_name_key,
    uses_local_transport,
)

_logger = get_logger(__name__)
_LEASE_SECONDS = 60
_MAX_BACKOFF_SECONDS = 3600
_DISPATCH_CONCURRENCY = 10
_LOCAL_HEARTBEAT_SECONDS = 15


@dataclass(frozen=True)
class DispatchStats:
    scanned: int = 0
    dispatched: int = 0
    failed: int = 0
    skipped: int = 0
    oldest_pending_at: datetime | None = None


async def record_task_intent(
    session: AsyncSession,
    task_type: TaskType,
    payload: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
    generation: int = 1,
) -> TaskOutbox:
    """Insert one logical job in the caller's domain transaction.

    Repeated calls for a generation return the existing row, allowing duplicate
    saves and worker re-entry to reconcile an undispatched intent safely.
    """
    statement = (
        pg_insert(TaskOutbox)
        .values(
            id=str(ulid.new()),
            task_type=task_type,
            target_type=target_type,
            target_id=target_id,
            generation=generation,
            payload={**payload, "generation": generation},
        )
        .on_conflict_do_nothing(constraint="task_outbox_logical_job_unique")
        .returning(TaskOutbox.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        result = await session.execute(select(TaskOutbox).where(TaskOutbox.id == inserted_id))
        return result.scalar_one()
    result = await session.execute(
        select(TaskOutbox).where(
            TaskOutbox.task_type == task_type,
            TaskOutbox.target_type == target_type,
            TaskOutbox.target_id == target_id,
            TaskOutbox.generation == generation,
        )
    )
    return result.scalar_one()


async def next_generation(
    session: AsyncSession, task_type: TaskType, target_type: str, target_id: str
) -> int:
    """Return a new generation for an explicit retry, serialized by target.

    Item retries share the worker lock order (Profile → Item → TaskOutbox),
    so concurrent retry/enhance requests cannot allocate the same generation
    or race account deletion.
    """
    if target_type == "item":
        owner_result = await session.execute(select(Item.user_id).where(Item.id == target_id))
        owner_id = owner_result.scalar_one_or_none()
        if owner_id is None:
            raise RuntimeError("worker_target_missing")
        profile_result = await session.execute(
            select(Profile).where(Profile.id == owner_id).with_for_update()
        )
        profile = profile_result.scalar_one_or_none()
        if profile is None or profile.deleted_at is not None:
            raise RuntimeError("worker_profile_deleted")
        item_result = await session.execute(
            select(Item).where(Item.id == target_id, Item.user_id == owner_id).with_for_update()
        )
        if item_result.scalar_one_or_none() is None:
            raise RuntimeError("worker_target_missing")

    result = await session.execute(
        select(func.max(TaskOutbox.generation)).where(
            TaskOutbox.task_type == task_type,
            TaskOutbox.target_type == target_type,
            TaskOutbox.target_id == target_id,
        )
    )
    return int(result.scalar_one_or_none() or 0) + 1


async def ensure_existing_intent(
    session: AsyncSession,
    task_type: TaskType,
    payload: dict[str, Any],
    *,
    target_type: str,
    target_id: str,
) -> TaskOutbox:
    """Find/recreate the current logical delivery for an existing target."""
    result = await session.execute(
        select(TaskOutbox)
        .where(
            TaskOutbox.task_type == task_type,
            TaskOutbox.target_type == target_type,
            TaskOutbox.target_id == target_id,
        )
        .order_by(TaskOutbox.generation.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    return await record_task_intent(
        session,
        task_type,
        payload,
        target_type=target_type,
        target_id=target_id,
        generation=1,
    )


async def _enqueue_local_with_heartbeat(
    outbox_id: str,
    token: str,
    task_type: TaskType,
    payload: dict[str, Any],
    logical_key: str,
) -> None:
    """Keep a long local handler owned by one live outbox claim."""
    work = asyncio.create_task(
        enqueue_task(task_type, payload, logical_key=logical_key),
        name=f"local-outbox-{outbox_id}",
    )
    try:
        while True:
            done, _ = await asyncio.wait({work}, timeout=_LOCAL_HEARTBEAT_SECONDS)
            if done:
                await work
                return

            now = datetime.now(UTC)
            async with session_scope() as session:
                result = await session.execute(
                    update(TaskOutbox)
                    .where(
                        TaskOutbox.id == outbox_id,
                        TaskOutbox.lease_token == token,
                        TaskOutbox.lease_until > now,
                        TaskOutbox.dispatched_at.is_(None),
                    )
                    .values(lease_until=now + timedelta(seconds=_LEASE_SECONDS))
                    .returning(TaskOutbox.id)
                )
                still_owned = result.scalar_one_or_none() is not None
                await session.commit()
            if not still_owned:
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
                raise RuntimeError("local_dispatch_lease_lost")
    finally:
        if not work.done():
            work.cancel()
            with suppress(asyncio.CancelledError):
                await work


async def _finalize_dispatch(outbox_id: str, token: str, *, error: Exception | None = None) -> bool:
    """Finalize only while the caller still owns this exact delivery lease."""
    async with session_scope() as session:
        if error is None:
            result = await session.execute(
                update(TaskOutbox)
                .where(
                    TaskOutbox.id == outbox_id,
                    TaskOutbox.lease_token == token,
                    TaskOutbox.dispatched_at.is_(None),
                )
                .values(
                    dispatched_at=datetime.now(UTC),
                    attempt_count=TaskOutbox.attempt_count + 1,
                    last_error=None,
                    lease_token=None,
                    lease_until=None,
                )
                .returning(TaskOutbox.id)
            )
            finalized_id = result.scalar_one_or_none()
            if finalized_id is None:
                return False
            await session.commit()
            return True

        result = await session.execute(
            select(TaskOutbox.attempt_count).where(
                TaskOutbox.id == outbox_id,
                TaskOutbox.lease_token == token,
                TaskOutbox.dispatched_at.is_(None),
            )
        )
        attempt_count = cast(int | None, result.scalar_one_or_none())
        if attempt_count is None:
            return False
        next_attempt = attempt_count + 1
        delay = min(2 ** min(next_attempt, 12), _MAX_BACKOFF_SECONDS)
        result = await session.execute(
            update(TaskOutbox)
            .where(
                TaskOutbox.id == outbox_id,
                TaskOutbox.lease_token == token,
                TaskOutbox.dispatched_at.is_(None),
            )
            .values(
                attempt_count=TaskOutbox.attempt_count + 1,
                available_at=datetime.now(UTC) + timedelta(seconds=delay),
                lease_token=None,
                lease_until=None,
                last_error=type(error).__name__[:120],
            )
            .returning(TaskOutbox.id)
        )
        finalized_id = result.scalar_one_or_none()
        if finalized_id is None:
            return False
        await session.commit()
        return True


async def dispatch_outbox_job(outbox_id: str) -> bool | None:
    """Claim and submit one intent. The claim commits before transport I/O."""
    token = str(ulid.new())
    now = datetime.now(UTC)
    async with session_scope() as session:
        result = await session.execute(
            select(TaskOutbox)
            .where(
                TaskOutbox.id == outbox_id,
                TaskOutbox.dispatched_at.is_(None),
                TaskOutbox.available_at <= now,
                (TaskOutbox.lease_until.is_(None) | (TaskOutbox.lease_until <= now)),
            )
            .with_for_update(skip_locked=True)
        )
        row = result.scalar_one_or_none()
        if row is None:
            # Another runner may have claimed or finalized the selected row.
            # This is normal contention, not a transport failure.
            return None
        row.lease_token = token
        row.lease_until = now + timedelta(seconds=_LEASE_SECONDS)
        task_type = row.task_type
        target_type = row.target_type
        target_id = row.target_id
        generation = row.generation
        delivery_attempt = row.attempt_count + 1
        payload = dict(row.payload)
        # Bind the delivery to the exact durable intent. Workers must never
        # infer a current outbox row from an item ID or task type.
        payload["outbox_id"] = row.id
        # Treat the indexed generation as authoritative even for intents
        # written before generation was copied into the worker payload.
        payload["generation"] = generation
        await session.commit()

    try:
        logical_key = logical_task_name_key(
            task_type,
            target_type,
            target_id,
            generation,
            delivery_attempt=delivery_attempt,
        )
        if uses_local_transport():
            await _enqueue_local_with_heartbeat(
                outbox_id, token, cast(TaskType, task_type), payload, logical_key
            )
        else:
            await enqueue_task(cast(TaskType, task_type), payload, logical_key=logical_key)
    except Exception as exc:
        # Never persist/log exception text: provider errors can contain request
        # payloads, task URLs or other operational secrets.
        await _finalize_dispatch(outbox_id, token, error=exc)
        _logger.warning(
            "task.outbox_dispatch_failed", outbox_id=outbox_id, error_code=type(exc).__name__
        )
        return False

    # The transport accepted the job, but the lease may have been reclaimed
    # before this dispatcher could mark the row complete. Treat that as
    # contention: another dispatcher owns reconciliation, and this successful
    # enqueue must not be counted as a transport failure.
    finalized = await _finalize_dispatch(outbox_id, token)
    return True if finalized else None


async def dispatch_due_jobs(*, limit: int = 50) -> DispatchStats:
    """Dispatch a bounded batch of due jobs for an external scheduler/worker."""
    bounded_limit = max(1, min(limit, 200))
    now = datetime.now(UTC)
    async with session_scope() as session:
        oldest_result = await session.execute(
            select(TaskOutbox.created_at)
            .where(TaskOutbox.dispatched_at.is_(None))
            .order_by(TaskOutbox.created_at)
            .limit(1)
        )
        oldest_pending_at = oldest_result.scalar_one_or_none()
        result = await session.execute(
            select(TaskOutbox.id)
            .where(
                TaskOutbox.dispatched_at.is_(None),
                TaskOutbox.available_at <= now,
                (TaskOutbox.lease_until.is_(None) | (TaskOutbox.lease_until <= now)),
            )
            .order_by(TaskOutbox.available_at, TaskOutbox.created_at)
            .limit(bounded_limit)
        )
        ids = list(result.scalars().all())

    semaphore = asyncio.Semaphore(_DISPATCH_CONCURRENCY)

    async def _dispatch(job_id: str) -> bool | None:
        async with semaphore:
            try:
                return await dispatch_outbox_job(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A malformed row or transient database error must not abandon
                # the other claims in this bounded batch. Avoid exception text,
                # which may contain provider details or credentials.
                _logger.warning(
                    "task.outbox_dispatch_failed",
                    outbox_id=job_id,
                    error_code=type(exc).__name__,
                )
                return False

    tasks = [asyncio.create_task(_dispatch(job_id)) for job_id in ids]
    try:
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # A caller may close its DB pool immediately after cancellation. Cancel
        # and drain every child first so none can use resources after cleanup.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    # A child can be independently cancelled. Preserve cancellation semantics
    # after all siblings have settled rather than converting it into a failure.
    if any(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes):
        raise asyncio.CancelledError

    dispatched = sum(outcome is True for outcome in outcomes)
    failed = sum(outcome is False or isinstance(outcome, BaseException) for outcome in outcomes)
    return DispatchStats(
        scanned=len(ids),
        dispatched=dispatched,
        failed=failed,
        skipped=len(ids) - dispatched - failed,
        oldest_pending_at=oldest_pending_at,
    )


async def dispatch_after_commit(outbox_ids: list[str]) -> None:
    """Best-effort prompt dispatch; durable rows remain for scheduled recovery."""
    if uses_local_transport():
        # Local handlers perform full media work. Leave the durable row for the
        # one-shot local runner so request latency never depends on extraction.
        return
    for outbox_id in dict.fromkeys(outbox_ids):
        try:
            await dispatch_outbox_job(outbox_id)
        except Exception as exc:
            # Acceptance already committed. Keep the request successful and
            # leave the row for the external due-job reconciler.
            _logger.warning(
                "task.outbox_prompt_dispatch_unavailable",
                outbox_id=outbox_id,
                error_code=type(exc).__name__,
            )
