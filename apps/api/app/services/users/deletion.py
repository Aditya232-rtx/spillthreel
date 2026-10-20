"""Resumable, lease-fenced account deletion.

Each external deletion is idempotent. The durable ``user_deletion_jobs`` row
records the next action, and the exact outbox execution lease fences both the
pre-action check and the checkpoint transition. Database transactions are
always closed before calling Storage, pgvector, or Auth.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Profile, TaskOutbox, UserDeletionJob
from app.observability.logging import get_logger
from app.settings import get_settings
from app.tasks.worker_claims import TargetExecution

_logger = get_logger(__name__)

_AUTH_ADMIN_TIMEOUT_SECONDS = 30.0
_STEPS: Final[tuple[str, ...]] = (
    "storage_media",
    "storage_exports",
    "vectors",
    "profile",
    "auth",
)
_STEP_AFTER: Final[dict[str, str]] = dict(zip(_STEPS, _STEPS[1:], strict=False))


class DeletionExecutionLost(RuntimeError):
    """The worker no longer owns the exact deletion execution lease."""


def _auth_admin_headers() -> dict[str, str]:
    """Service-role headers for the Auth Admin API."""
    key = get_settings().supabase_secret_key
    if not key:
        raise RuntimeError("supabase_auth_service_key_missing")
    return {"apikey": key, "Authorization": f"Bearer {key}"}


async def delete_auth_user(user_id: str) -> None:
    """Delete ``auth.users``; 404 means a prior attempt already succeeded."""
    settings = get_settings()
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
    async with httpx.AsyncClient(timeout=_AUTH_ADMIN_TIMEOUT_SECONDS) as client:
        response = await client.delete(url, headers=_auth_admin_headers())
    if response.status_code == 404:
        _logger.info("users.auth_delete_missing", user_id=user_id)
        return
    if response.status_code >= 400:
        raise RuntimeError(
            f"auth admin delete failed: {response.status_code} {response.text[:200]}"
        )
    _logger.info("users.auth_deleted", user_id=user_id)


def _execution_matches_user(execution: TargetExecution, user_id: str) -> bool:
    identity = execution.identity
    return bool(
        execution.owner_id == user_id
        and execution.token
        and identity.task_type == "delete_user"
        and identity.target_type == "user"
        and identity.target_id == user_id
        and identity.outbox_id
        and identity.generation > 0
    )


async def _locked_checkpoint(
    session: AsyncSession,
    user_id: str,
    execution: TargetExecution,
    *,
    create_legacy: bool,
) -> UserDeletionJob | None:
    """Lock and validate deletion target + lease using the global lock order.

    The profile is optional because a previous ``profile`` step can already
    have committed. When present, it must be soft-deleted before any purge.
    """
    identity = execution.identity

    profile = (
        await session.execute(
            select(Profile).where(Profile.id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    if profile is not None and profile.deleted_at is None:
        return None

    outbox = (
        await session.execute(
            select(TaskOutbox)
            .where(TaskOutbox.id == identity.outbox_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if outbox is None or not (
        outbox.id == identity.outbox_id
        and outbox.task_type == "delete_user"
        and outbox.target_type == "user"
        and outbox.target_id == user_id
        and outbox.generation == identity.generation
        and outbox.execution_status == "pending"
        and outbox.execution_lease_token == execution.token
        and outbox.execution_lease_until is not None
        and outbox.execution_lease_until > datetime.now(UTC)
    ):
        return None

    latest_generation = await session.scalar(
        select(func.max(TaskOutbox.generation)).where(
            TaskOutbox.task_type == "delete_user",
            TaskOutbox.target_type == "user",
            TaskOutbox.target_id == user_id,
        )
    )
    if latest_generation != identity.generation:
        return None

    if create_legacy:
        await session.execute(
            insert(UserDeletionJob)
            .values(user_id=user_id, next_step="storage_media")
            .on_conflict_do_nothing(index_elements=[UserDeletionJob.user_id])
        )
    checkpoint = (
        await session.execute(
            select(UserDeletionJob)
            .where(UserDeletionJob.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    return checkpoint


async def _current_step(
    user_id: str, execution: TargetExecution
) -> str:
    from app.db.engine import session_scope

    async with session_scope() as session:
        checkpoint = await _locked_checkpoint(
            session, user_id, execution, create_legacy=True
        )
        if checkpoint is None:
            raise DeletionExecutionLost("deletion lease or checkpoint is no longer current")
        step = checkpoint.next_step
        # This commit only persists a newly-created legacy checkpoint. It
        # releases row locks before the caller performs external work.
        await session.commit()
        return step


async def _advance(
    user_id: str,
    expected_step: str,
    execution: TargetExecution,
    *,
    delete_profile: bool = False,
) -> None:
    """CAS the next step under the same live outbox lease.

    The profile row removal, when requested, shares this transaction with the
    transition to ``auth`` so no successful checkpoint can skip a DB purge.
    """
    from app.db.engine import session_scope

    next_step = _STEP_AFTER.get(expected_step)
    if next_step is None:
        raise ValueError(f"cannot advance terminal deletion step: {expected_step}")
    async with session_scope() as session:
        checkpoint = await _locked_checkpoint(
            session, user_id, execution, create_legacy=False
        )
        if checkpoint is None or checkpoint.next_step != expected_step:
            raise DeletionExecutionLost("deletion lease or checkpoint changed before advance")

        if delete_profile:
            await session.execute(delete(Profile).where(Profile.id == user_id))

        now = datetime.now(UTC)
        result = await session.execute(
            update(UserDeletionJob)
            .where(
                UserDeletionJob.user_id == user_id,
                UserDeletionJob.next_step == expected_step,
            )
            .values(next_step=next_step, updated_at=now)
        )
        if result.rowcount != 1:
            raise DeletionExecutionLost("deletion checkpoint compare-and-swap failed")
        await session.commit()


async def _finish_auth_step(user_id: str, execution: TargetExecution) -> None:
    """Remove the checkpoint only if this exact auth-step lease still owns it."""
    from app.db.engine import session_scope

    async with session_scope() as session:
        checkpoint = await _locked_checkpoint(
            session, user_id, execution, create_legacy=False
        )
        if checkpoint is None or checkpoint.next_step != "auth":
            raise DeletionExecutionLost("deletion lease or auth checkpoint is no longer current")
        await session.execute(
            update(UserDeletionJob)
            .where(
                UserDeletionJob.user_id == user_id,
                UserDeletionJob.next_step == "auth",
            )
            .values(next_step="complete", updated_at=datetime.now(UTC))
        )
        await session.commit()


async def delete_user_account(
    user_id: str, execution: TargetExecution
) -> dict[str, str]:
    """Resume deletion one idempotent, lease-fenced step at a time."""
    if not _execution_matches_user(execution, user_id):
        raise DeletionExecutionLost("invalid deletion execution identity")

    from app.db.engine import get_pg_pool
    from app.services.media import supabase_storage as storage
    from app.services.memory.pgvector_store import PgVectorStore

    settings = get_settings()
    pool = None
    while True:
        step = await _current_step(user_id, execution)
        if step == "storage_media":
            await storage.delete_prefix(settings.supabase_bucket_media, f"{user_id}/")
        elif step == "storage_exports":
            await storage.delete_prefix(settings.supabase_bucket_exports, f"{user_id}/")
        elif step == "vectors":
            if pool is None:
                pool = await get_pg_pool()
            await PgVectorStore(pool).delete_namespace(user_id)
        elif step == "profile":
            await _advance(user_id, step, execution, delete_profile=True)
            continue
        elif step == "auth":
            await delete_auth_user(user_id)
            await _finish_auth_step(user_id, execution)
            _logger.warning("users.deleted", user_id=user_id)
            return {"status": "ok", "user_id": user_id}
        elif step == "complete":
            # The process may have lost its acknowledgment after persisting
            # the tombstone but before completing the outbox intent.
            return {"status": "ok", "user_id": user_id}
        else:
            raise RuntimeError(f"unknown deletion checkpoint: {step}")

        # External calls run with no open database transaction or row lock.
        # If ownership changed during the call, this fenced CAS fails and the
        # next claimant repeats the idempotent external action.
        await _advance(user_id, step, execution)
