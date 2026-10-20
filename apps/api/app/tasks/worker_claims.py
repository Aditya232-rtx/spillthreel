"""Fenced execution leases shared by HTTP and local worker transports.

Transport delivery is at least once.  This module owns the separate database
lease which ensures one provider attempt per item and prevents stale workers
from publishing after a retry or account deletion.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import session_scope
from app.db.models import Category, Import, Item, Profile, TaskOutbox
from app.settings import get_settings

LEASE_SECONDS = 15 * 60
CAPACITY_RETRY_BASE_SECONDS = 30
CAPACITY_RETRY_MAX_SECONDS = 30 * 60


@dataclass(frozen=True)
class WorkerIdentity:
    outbox_id: str
    task_type: str
    target_type: str
    target_id: str
    generation: int


@dataclass(frozen=True)
class WorkerLease:
    identity: WorkerIdentity
    item_id: str
    user_id: str
    token: str


@dataclass(frozen=True)
class ClaimResult:
    status: str  # claimed, completed, stale, busy, capacity, invalid
    lease: WorkerLease | None = None


@dataclass(frozen=True)
class TargetIntentResult:
    status: str  # claimed, completed, stale, terminal, busy, invalid
    token: str | None = None


@dataclass(frozen=True)
class TargetExecution:
    """The immutable lease identity passed into every target mutation transaction."""

    identity: WorkerIdentity
    token: str
    owner_id: str


async def claim_worker(
    identity: WorkerIdentity, *, max_active_items: int | None = None
) -> ClaimResult:
    """Validate exact intent and claim under profile→item→outbox locks.

    Missing/legacy identity is rejected before touching a provider.  Item
    operations are intentionally limited to the item target contract.
    """
    if (
        not identity.outbox_id
        or identity.task_type not in {"ingest", "enhance"}
        or identity.target_type != "item"
        or not identity.target_id
        or identity.generation < 1
    ):
        return ClaimResult("invalid")
    if max_active_items is None:
        max_active_items = get_settings().worker_max_active_items_per_owner

    async with session_scope() as session:
        # Read the owner without locking, then follow the global lock order.
        item_probe = await session.execute(
            select(Item.user_id).where(Item.id == identity.target_id)
        )
        user_id = item_probe.scalar_one_or_none()
        if user_id is None:
            await _mark_outbox_stale(session, identity)
            return ClaimResult("stale")
        profile = (
            await session.execute(select(Profile).where(Profile.id == user_id).with_for_update())
        ).scalar_one_or_none()
        item = (
            await session.execute(
                select(Item).where(Item.id == identity.target_id).with_for_update()
            )
        ).scalar_one_or_none()
        outbox = (
            await session.execute(
                select(TaskOutbox)
                .where(TaskOutbox.id == identity.outbox_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if outbox is None:
            return ClaimResult("invalid")
        matches = (
            outbox.task_type == identity.task_type
            and outbox.target_type == identity.target_type
            and outbox.target_id == identity.target_id
            and outbox.generation == identity.generation
            and item is not None
            and item.user_id == user_id
        )
        if not matches:
            return ClaimResult("invalid")
        latest = await session.scalar(
            select(func.max(TaskOutbox.generation)).where(
                TaskOutbox.task_type == identity.task_type,
                TaskOutbox.target_type == identity.target_type,
                TaskOutbox.target_id == identity.target_id,
            )
        )
        if profile is None or profile.deleted_at is not None or latest != identity.generation:
            _finish_outbox(outbox, "stale", "stale_generation_or_deleted")
            if item is not None and item.worker_lease_token is None:
                item.worker_task_type = None
            await session.commit()
            return ClaimResult("stale")
        if outbox.execution_status == "completed":
            return ClaimResult("completed")
        if outbox.execution_status in {"stale", "terminal"}:
            return ClaimResult(outbox.execution_status)
        now = datetime.now(UTC)
        if (
            item.worker_lease_token is not None
            and item.worker_lease_until is not None
            and item.worker_lease_until > now
        ):
            return ClaimResult("busy")
        # Profile's row lock serializes all item claims for this owner. Count
        # only live item leases after the same-item busy check, so duplicate
        # deliveries keep their existing semantics while expired leases stop
        # consuming capacity immediately.
        active_count = await session.scalar(
            select(func.count(Item.id)).where(
                Item.user_id == user_id,
                Item.worker_lease_token.is_not(None),
                Item.worker_lease_until > now,
            )
        )
        if max_active_items <= 0 or (active_count or 0) >= max_active_items:
            # The currently delivered Cloud Task will be acknowledged after
            # this durable deferral. The outbox sweeper will submit a new
            # delivery after the slot has had time to turn over.
            # Clear the dispatch lease too: the original enqueue call can
            # still be returning while this very fast worker delivery runs.
            # Its later finalizer must not overwrite this deferral.
            retry_exponent = min(max(outbox.attempt_count - 1, 0), 6)
            retry_delay = min(
                CAPACITY_RETRY_BASE_SECONDS * (2**retry_exponent),
                CAPACITY_RETRY_MAX_SECONDS,
            )
            outbox.dispatched_at = None
            outbox.available_at = now + timedelta(seconds=retry_delay)
            outbox.lease_token = None
            outbox.lease_until = None
            await session.commit()
            return ClaimResult("capacity")
        token = secrets.token_urlsafe(32)
        item.worker_lease_token = token
        item.worker_lease_until = now + timedelta(seconds=LEASE_SECONDS)
        item.worker_task_type = identity.task_type
        item.worker_generation = identity.generation
        outbox.worker_attempt_count += 1
        outbox.worker_last_error_code = None
        await session.commit()
        return ClaimResult(
            "claimed",
            WorkerLease(identity, item.id, item.user_id, token),
        )


async def claim_target_intent(
    identity: WorkerIdentity,
    *,
    owner_id: str | None = None,
    payload_fields: dict[str, Any] | None = None,
) -> TargetIntentResult:
    """Validate and record an attempt for a non-item durable target.

    Item work has an item-wide execution lease. These handlers still require
    the exact latest outbox identity and validate target ownership/deletion
    state before entering their service. The deletion intent uniquely remains
    valid after profile purge because its outbox row is deliberately retained.
    """
    expected_targets = {
        "delete_user": ("user", "user"),
        "import": ("import", "import"),
        "import_summary": ("import", "import"),
        "category_backfill": ("category", "category"),
    }
    expected = expected_targets.get(identity.task_type)
    if (
        expected is None
        or identity.target_type != expected[0]
        or identity.generation < 1
        or not identity.outbox_id
        or not identity.target_id
        or (identity.task_type != "delete_user" and not owner_id)
        or (identity.task_type == "delete_user" and owner_id not in (None, identity.target_id))
    ):
        return TargetIntentResult("invalid")

    async with session_scope() as session:
        profile_id = identity.target_id if identity.task_type == "delete_user" else owner_id
        profile = (
            await session.execute(
                select(Profile).where(Profile.id == profile_id).with_for_update()
            )
        ).scalar_one_or_none()
        if identity.task_type == "delete_user":
            # A caller cannot turn this endpoint into an arbitrary account
            # deletion. The profile must already carry the durable fence.
            if profile is not None and profile.deleted_at is None:
                return TargetIntentResult("invalid")
        else:
            if profile is None or profile.deleted_at is not None:
                return TargetIntentResult("stale")
            target_model = Import if identity.task_type in {"import", "import_summary"} else Category
            target = (
                await session.execute(
                    select(target_model)
                    .where(target_model.id == identity.target_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if target is None or target.user_id != owner_id:
                return TargetIntentResult("invalid")
            if (
                identity.task_type == "import"
                and target.admission_enforced
                and (
                    payload_fields is None
                    or payload_fields.get("admitted_reservation_keys") is None
                )
            ):
                return TargetIntentResult("invalid")

        outbox = (
            await session.execute(
                select(TaskOutbox)
                .where(TaskOutbox.id == identity.outbox_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if outbox is None or not _outbox_identity_matches(outbox, identity):
            return TargetIntentResult("invalid")
        if payload_fields and any(
            not isinstance(outbox.payload, dict)
            or outbox.payload.get(key) != value
            for key, value in payload_fields.items()
        ):
            return TargetIntentResult("invalid")
        latest = await session.scalar(
            select(func.max(TaskOutbox.generation)).where(
                TaskOutbox.task_type == identity.task_type,
                TaskOutbox.target_type == identity.target_type,
                TaskOutbox.target_id == identity.target_id,
            )
        )
        if latest != identity.generation:
            if outbox.execution_status == "pending":
                _finish_outbox(outbox, "stale", "stale_generation")
                await session.commit()
            return TargetIntentResult("stale")
        if outbox.execution_status == "completed":
            return TargetIntentResult("completed")
        if outbox.execution_status in {"stale", "terminal"}:
            return TargetIntentResult(outbox.execution_status)
        now = datetime.now(UTC)
        if (
            outbox.execution_lease_token is not None
            and outbox.execution_lease_until is not None
            and outbox.execution_lease_until > now
        ):
            return TargetIntentResult("busy")
        token = secrets.token_urlsafe(32)
        outbox.execution_lease_token = token
        outbox.execution_lease_until = now + timedelta(seconds=LEASE_SECONDS)
        outbox.worker_attempt_count += 1
        outbox.worker_last_error_code = None
        await session.commit()
        return TargetIntentResult("claimed", token)


async def renew_target_intent(
    identity: WorkerIdentity, token: str, *, owner_id: str | None = None
) -> bool:
    async with session_scope() as session:
        profile_id = identity.target_id if identity.task_type == "delete_user" else owner_id
        profile = (
            await session.execute(
                select(Profile).where(Profile.id == profile_id).with_for_update()
            )
        ).scalar_one_or_none()
        if identity.task_type == "delete_user":
            if profile is not None and profile.deleted_at is None:
                return False
        elif profile is None or profile.deleted_at is not None:
            return False
        outbox = (
            await session.execute(
                select(TaskOutbox)
                .where(TaskOutbox.id == identity.outbox_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if outbox is None or not _outbox_identity_matches(outbox, identity):
            return False
        latest = await session.scalar(
            select(func.max(TaskOutbox.generation)).where(
                TaskOutbox.task_type == identity.task_type,
                TaskOutbox.target_type == identity.target_type,
                TaskOutbox.target_id == identity.target_id,
            )
        )
        now = datetime.now(UTC)
        if not (
            latest == identity.generation
            and outbox.execution_status == "pending"
            and outbox.execution_lease_token == token
            and outbox.execution_lease_until is not None
            and outbox.execution_lease_until > now
        ):
            return False
        outbox.execution_lease_until = now + timedelta(seconds=LEASE_SECONDS)
        await session.commit()
        return True


async def release_target_intent(
    identity: WorkerIdentity,
    token: str,
    *,
    owner_id: str | None = None,
    error_code: str | None = None,
) -> bool:
    """Release only the caller's still-current lease while leaving intent pending."""
    async with session_scope() as session:
        profile_id = identity.target_id if identity.task_type == "delete_user" else owner_id
        profile = (
            await session.execute(
                select(Profile).where(Profile.id == profile_id).with_for_update()
            )
        ).scalar_one_or_none()
        if identity.task_type == "delete_user":
            if profile is not None and profile.deleted_at is None:
                return False
        elif profile is None or profile.deleted_at is not None:
            return False
        outbox = (
            await session.execute(
                select(TaskOutbox)
                .where(TaskOutbox.id == identity.outbox_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if outbox is None or not _outbox_identity_matches(outbox, identity):
            return False
        latest = await session.scalar(
            select(func.max(TaskOutbox.generation)).where(
                TaskOutbox.task_type == identity.task_type,
                TaskOutbox.target_type == identity.target_type,
                TaskOutbox.target_id == identity.target_id,
            )
        )
        if not (
            latest == identity.generation
            and outbox.execution_status == "pending"
            and outbox.execution_lease_token == token
        ):
            return False
        outbox.execution_lease_token = None
        outbox.execution_lease_until = None
        outbox.worker_last_error_code = (error_code or None)[:120] if error_code else None
        await session.commit()
        return True


async def complete_target_intent(
    identity: WorkerIdentity, token: str, *, owner_id: str | None = None
) -> bool:
    """Complete a validated non-item intent after its service succeeds."""
    if not identity.outbox_id:
        return False
    async with session_scope() as session:
        profile_id = identity.target_id if identity.task_type == "delete_user" else owner_id
        profile = (
            await session.execute(
                select(Profile).where(Profile.id == profile_id).with_for_update()
            )
        ).scalar_one_or_none()
        if identity.task_type == "delete_user":
            if profile is not None and profile.deleted_at is None:
                return False
        elif profile is None or profile.deleted_at is not None:
            return False
        outbox = (
            await session.execute(
                select(TaskOutbox)
                .where(TaskOutbox.id == identity.outbox_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if outbox is None or not _outbox_identity_matches(outbox, identity):
            return False
        latest = await session.scalar(
            select(func.max(TaskOutbox.generation)).where(
                TaskOutbox.task_type == identity.task_type,
                TaskOutbox.target_type == identity.target_type,
                TaskOutbox.target_id == identity.target_id,
            )
        )
        if latest != identity.generation:
            return False
        if outbox.execution_status == "completed":
            return True
        if not (
            outbox.execution_status == "pending"
            and outbox.execution_lease_token == token
            and outbox.execution_lease_until is not None
            and outbox.execution_lease_until > datetime.now(UTC)
        ):
            return False
        _finish_outbox(outbox, "completed", None)
        outbox.execution_lease_token = None
        outbox.execution_lease_until = None
        await session.commit()
        return True


async def target_intent_is_current_in_session(
    session: AsyncSession, execution: TargetExecution
) -> bool:
    """Lock and validate an import execution fence in the caller's transaction.

    Lock order matches claim/renew/finalize: profile → target → outbox. Keeping
    this check in each mutation transaction makes takeover and account deletion
    serialize with the write being authorized.
    """
    identity = execution.identity
    if (
        identity.task_type != "import"
        or identity.target_type != "import"
        or not identity.outbox_id
        or not identity.target_id
        or identity.generation < 1
        or not execution.token
        or not execution.owner_id
    ):
        return False
    profile = (
        await session.execute(
            select(Profile).where(Profile.id == execution.owner_id).with_for_update()
        )
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        return False
    target = (
        await session.execute(
            select(Import)
            .where(Import.id == identity.target_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if target is None or target.user_id != execution.owner_id:
        return False
    outbox = (
        await session.execute(
            select(TaskOutbox)
            .where(TaskOutbox.id == identity.outbox_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if outbox is None or not _outbox_identity_matches(outbox, identity):
        return False
    latest = await session.scalar(
        select(func.max(TaskOutbox.generation)).where(
            TaskOutbox.task_type == identity.task_type,
            TaskOutbox.target_type == identity.target_type,
            TaskOutbox.target_id == identity.target_id,
        )
    )
    return bool(
        latest == identity.generation
        and outbox.execution_status == "pending"
        and outbox.execution_lease_token == execution.token
        and outbox.execution_lease_until is not None
        and outbox.execution_lease_until > datetime.now(UTC)
    )


async def renew_worker_lease(lease: WorkerLease) -> bool:
    async with session_scope() as session:
        profile, item, outbox, latest = await _lock_lease_rows(session, lease)
        if not _owns(profile, item, outbox, latest, lease):
            return False
        item.worker_lease_until = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
        await session.commit()
        return True


async def finalize_worker(
    lease: WorkerLease, *, status: str, error_code: str | None = None
) -> bool:
    """Atomically commit worker outcome and release only its own lease."""
    if status not in {"completed", "stale", "terminal", "pending"}:
        raise ValueError("invalid worker execution status")
    async with session_scope() as session:
        profile, item, outbox, latest = await _lock_lease_rows(session, lease)
        if (
            status == "completed"
            and profile is not None
            and profile.deleted_at is None
            and outbox is not None
            and _outbox_matches(outbox, lease)
            and outbox.execution_status in {"completed", "terminal"}
        ):
            return True
        if not _owns(profile, item, outbox, latest, lease):
            return False
        _finish_outbox(outbox, status, error_code)
        _clear_item_lease(item)
        await session.commit()
        return True


async def lease_is_current(session: AsyncSession, lease: WorkerLease) -> bool:
    """Check fenced ownership inside a caller's publication transaction."""
    profile, item, outbox, latest = await _lock_lease_rows(session, lease)
    return _owns(profile, item, outbox, latest, lease)


async def finalize_worker_in_session(
    session: AsyncSession,
    lease: WorkerLease,
    *,
    status: str,
    error_code: str | None = None,
) -> bool:
    """Stage a terminal outcome in the caller's transaction."""
    if status not in {"completed", "stale", "terminal"}:
        raise ValueError("in-transaction worker status must be terminal")
    profile, item, outbox, latest = await _lock_lease_rows(session, lease)
    if not _owns(profile, item, outbox, latest, lease):
        return False
    assert item is not None and outbox is not None
    _finish_outbox(outbox, status, error_code)
    _clear_item_lease(item)
    return True


async def _lock_lease_rows(
    session: AsyncSession, lease: WorkerLease
) -> tuple[Profile | None, Item | None, TaskOutbox | None, int | None]:
    profile = (
        await session.execute(
            select(Profile).where(Profile.id == lease.user_id).with_for_update()
        )
    ).scalar_one_or_none()
    item = (
        await session.execute(
            select(Item).where(Item.id == lease.item_id).with_for_update()
        )
    ).scalar_one_or_none()
    outbox = (
        await session.execute(
            select(TaskOutbox)
            .where(TaskOutbox.id == lease.identity.outbox_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    latest = None
    if outbox is not None:
        latest = await session.scalar(
            select(func.max(TaskOutbox.generation)).where(
                TaskOutbox.task_type == lease.identity.task_type,
                TaskOutbox.target_type == lease.identity.target_type,
                TaskOutbox.target_id == lease.identity.target_id,
            )
        )
    return profile, item, outbox, latest


def _owns(
    profile: Profile | None,
    item: Item | None,
    outbox: TaskOutbox | None,
    latest: int | None,
    lease: WorkerLease,
) -> bool:
    now = datetime.now(UTC)
    return bool(
        profile is not None
        and profile.deleted_at is None
        and item is not None
        and item.user_id == lease.user_id
        and item.worker_lease_token == lease.token
        and item.worker_task_type == lease.identity.task_type
        and item.worker_generation == lease.identity.generation
        and item.worker_lease_until is not None
        and item.worker_lease_until > now
        and outbox is not None
        and outbox.task_type == lease.identity.task_type
        and outbox.target_type == lease.identity.target_type
        and outbox.target_id == lease.identity.target_id
        and outbox.generation == lease.identity.generation
        and outbox.execution_status == "pending"
        and latest == lease.identity.generation
    )


def _finish_outbox(outbox: TaskOutbox, status: str, error_code: str | None) -> None:
    outbox.execution_status = status
    outbox.worker_completed_at = datetime.now(UTC) if status != "pending" else None
    outbox.worker_last_error_code = (error_code or None)[:120] if error_code else None
    if status != "pending":
        outbox.execution_lease_token = None
        outbox.execution_lease_until = None


def _clear_item_lease(item: Item) -> None:
    item.worker_lease_token = None
    item.worker_lease_until = None
    item.worker_task_type = None
    item.worker_generation = None


def _outbox_matches(outbox: TaskOutbox, lease: WorkerLease) -> bool:
    return (
        outbox.task_type == lease.identity.task_type
        and outbox.target_type == lease.identity.target_type
        and outbox.target_id == lease.identity.target_id
        and outbox.generation == lease.identity.generation
    )


def _outbox_identity_matches(outbox: TaskOutbox, identity: WorkerIdentity) -> bool:
    return (
        outbox.task_type == identity.task_type
        and outbox.target_type == identity.target_type
        and outbox.target_id == identity.target_id
        and outbox.generation == identity.generation
    )


async def _mark_outbox_stale(session: AsyncSession, identity: WorkerIdentity) -> None:
    row = (
        await session.execute(
            select(TaskOutbox).where(TaskOutbox.id == identity.outbox_id).with_for_update()
        )
    ).scalar_one_or_none()
    if (
        row
        and row.task_type == identity.task_type
        and row.target_type == identity.target_type
        and row.target_id == identity.target_id
        and row.generation == identity.generation
    ):
        _finish_outbox(row, "stale", "missing_item")
        await session.commit()
