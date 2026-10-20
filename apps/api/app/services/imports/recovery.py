"""Durable processing intent helpers for admitted imports.

Inline imports used to commit their admission and ``Import`` row, then start
processing from request-local memory. A process crash in that gap lost the only
copy of the parsed upload. Recovery now relies on a staged source plus the
existing import outbox contract; the caller must store the source before calling
this helper and commit the returned intent with the admission transaction.
Dispatch must happen only after that commit.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.db.models import Import, Profile, TaskOutbox
from app.db.engine import session_scope
from app.db.models import ImportStaging
from app.tasks.outbox import record_task_intent

_RESERVATION_KEY = re.compile(r"^[0-9a-f]{64}$")
_MAX_ADMITTED_KEYS = 1000
STAGING_LEASE_SECONDS = 5 * 60
STAGING_UPLOAD_SETTLE_GRACE_SECONDS = 5 * 60
STAGING_CLEANUP_LEASE_SECONDS = 2 * 60
MAX_STAGING_SWEEP_BATCH = 100


@dataclass(frozen=True)
class ImportStagingCleanupClaim:
    import_id: str
    owner_id: str
    bucket: str
    storage_key: str
    cleanup_token: str


@dataclass(frozen=True)
class ImportStagingSweepResult:
    examined: int = 0
    deleted: int = 0
    retained: int = 0
    failed: int = 0


async def create_import_staging_marker(
    session: AsyncSession,
    *,
    owner_id: str,
    import_id: str,
    bucket: str,
    storage_key: str,
    lease_until: datetime,
) -> bool:
    """Commit a durable staging marker before upload, under a short profile lock."""
    profile = (
        await session.execute(select(Profile).where(Profile.id == owner_id).with_for_update())
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        return False
    if storage_key != f"{owner_id}/{import_id}.zip":
        raise ValueError("staging key must be scoped to its owner and import")
    session.add(
        ImportStaging(
            import_id=import_id,
            owner_id=owner_id,
            bucket=bucket,
            storage_key=storage_key,
            state="staging",
            lease_until=lease_until,
            cleanup_token=None,
            cleanup_lease_until=None,
            cleanup_attempts=0,
        )
    )
    await session.commit()
    return True


async def lock_import_staging_for_admission(
    session: AsyncSession,
    *,
    owner_id: str,
    import_id: str,
    bucket: str,
    storage_key: str,
    now: datetime | None = None,
) -> ImportStaging | None:
    """Lock Profile then require the exact live, not-cleaning staging marker."""
    now = now or datetime.now(UTC)
    profile = (
        await session.execute(select(Profile).where(Profile.id == owner_id).with_for_update())
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        return None
    marker = (
        await session.execute(
            select(ImportStaging).where(ImportStaging.import_id == import_id).with_for_update()
        )
    ).scalar_one_or_none()
    if (
        marker is None
        or marker.owner_id != owner_id
        or marker.bucket != bucket
        or marker.storage_key != storage_key
        or marker.state != "staging"
        or marker.lease_until <= now
        or marker.cleanup_token is not None
    ):
        return None
    return marker


async def mark_import_staging_cleanup_pending(
    bind: AsyncEngine | AsyncConnection,
    *,
    owner_id: str,
    import_id: str,
    bucket: str,
    storage_key: str,
    now: datetime | None = None,
    error_code: str | None = None,
) -> bool:
    """Irreversibly queue a known failed intake source for bounded cleanup."""
    now = now or datetime.now(UTC)
    async with _fresh_session_factory(bind)() as session:
        async with session.begin():
            await _read_committed(session)
            await _lock_profile_if_present(session, owner_id)
            marker = (
                await session.execute(
                    select(ImportStaging)
                    .where(ImportStaging.import_id == import_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not _marker_matches(marker, owner_id, import_id, bucket, storage_key):
                return False
            if await _has_import_source_reference(session, import_id, storage_key):
                return False
            assert marker is not None
            if marker.state == "cleanup_pending":
                return True
            if marker.state != "staging":
                return False
            marker.state = "cleanup_pending"
            marker.cleanup_token = secrets.token_urlsafe(24)
            # A Storage timeout/cancellation is not proof that the remote write
            # stopped. Keep the original lease and add a fresh settling window
            # before any sweeper may delete a key that could still be written.
            marker.cleanup_lease_until = max(
                marker.lease_until,
                now + timedelta(seconds=STAGING_UPLOAD_SETTLE_GRACE_SECONDS),
            )
            marker.last_error_code = _safe_error_code(error_code)
        return True


async def sweep_import_staging(
    *,
    bind: AsyncEngine | AsyncConnection,
    limit: int = 50,
    import_id: str | None = None,
    now: datetime | None = None,
) -> ImportStagingSweepResult:
    """Claim a bounded set, verify fresh token ownership, then delete Storage outside DB locks."""
    from app.services.media.supabase_storage import delete_object

    now = now or datetime.now(UTC)
    limit = max(1, min(limit, MAX_STAGING_SWEEP_BATCH))
    claims: list[ImportStagingCleanupClaim] = []
    claim_error = False
    session_factory = _fresh_session_factory(bind)
    try:
        async with session_factory() as session:
            async with session.begin():
                await _read_committed(session)
                eligibility = or_(
                    (ImportStaging.state == "staging") & (ImportStaging.lease_until <= now),
                    (ImportStaging.state == "cleanup_pending")
                    & (ImportStaging.cleanup_lease_until <= now),
                )
                candidate_stmt = select(
                    ImportStaging.import_id,
                    ImportStaging.owner_id,
                    ImportStaging.bucket,
                    ImportStaging.storage_key,
                ).where(eligibility)
                if import_id is not None:
                    candidate_stmt = candidate_stmt.where(ImportStaging.import_id == import_id)
                candidates = (await session.execute(
                    candidate_stmt.order_by(ImportStaging.created_at).limit(limit)
                )).all()

                # Candidate discovery holds no row locks. For each target, follow
                # the global profile→staging→reference order before claiming.
                for candidate_id, owner_id, bucket, storage_key in candidates:
                    await _lock_profile_if_present(session, str(owner_id))
                    marker = (
                        await session.execute(
                            select(ImportStaging)
                            .where(ImportStaging.import_id == candidate_id)
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if not _marker_matches(
                        marker, str(owner_id), str(candidate_id), str(bucket), str(storage_key)
                    ):
                        continue
                    assert marker is not None
                    eligible_now = (
                        marker.state == "staging" and marker.lease_until <= now
                    ) or (
                        marker.state == "cleanup_pending"
                        and marker.cleanup_lease_until is not None
                        and marker.cleanup_lease_until <= now
                    )
                    if not eligible_now or await _has_import_source_reference(
                        session, marker.import_id, marker.storage_key
                    ):
                        continue
                    token = secrets.token_urlsafe(24)
                    marker.state = "cleanup_pending"
                    marker.cleanup_token = token
                    marker.cleanup_lease_until = now + timedelta(
                        seconds=STAGING_CLEANUP_LEASE_SECONDS
                    )
                    marker.cleanup_attempts += 1
                    marker.last_error_code = None
                    claims.append(
                        ImportStagingCleanupClaim(
                            marker.import_id,
                            marker.owner_id,
                            marker.bucket,
                            marker.storage_key,
                            token,
                        )
                    )
    except Exception:
        # A failed/ambiguous claim commit is never authority to delete. The
        # fresh verification below is the only path from claimed state to I/O.
        claim_error = True

    deleted = retained = failed = 0
    for claim in claims:
        try:
            if not await verify_import_staging_cleanup_claim(bind, claim):
                retained += 1
                continue
        except Exception as exc:
            await _record_cleanup_failure(bind, claim, exc)
            failed += 1
            continue
        try:
            await delete_object(claim.bucket, claim.storage_key)
        except Exception as exc:
            await _record_cleanup_failure(bind, claim, exc)
            failed += 1
            continue
        try:
            if await remove_import_staging_after_cleanup(bind, claim):
                deleted += 1
            else:
                retained += 1
        except Exception as exc:
            await _record_cleanup_failure(bind, claim, exc)
            failed += 1
    return ImportStagingSweepResult(
        examined=len(claims),
        deleted=deleted,
        retained=retained,
        failed=failed + (1 if claim_error else 0),
    )


async def verify_import_staging_cleanup_claim(
    bind: AsyncEngine | AsyncConnection,
    claim: ImportStagingCleanupClaim,
    *,
    now: datetime | None = None,
) -> bool:
    """Fresh-session proof of token ownership and no durable import reference."""
    now = now or datetime.now(UTC)
    async with _fresh_session_factory(bind)() as session:
        async with session.begin():
            await _read_committed(session)
            await _lock_profile_if_present(session, claim.owner_id)
            marker = (
                await session.execute(
                    select(ImportStaging)
                    .where(ImportStaging.import_id == claim.import_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not _marker_matches(
                marker, claim.owner_id, claim.import_id, claim.bucket, claim.storage_key
            ):
                return False
            assert marker is not None
            if not (
                marker.state == "cleanup_pending"
                and marker.cleanup_token == claim.cleanup_token
                and marker.cleanup_lease_until is not None
                and marker.cleanup_lease_until > now
            ):
                return False
            return not await _has_import_source_reference(
                session, claim.import_id, claim.storage_key
            )


async def remove_import_staging_after_cleanup(
    bind: AsyncEngine | AsyncConnection,
    claim: ImportStagingCleanupClaim,
) -> bool:
    """Delete a marker only after Storage success and a fresh token/reference check."""
    async with _fresh_session_factory(bind)() as session:
        async with session.begin():
            await _read_committed(session)
            await _lock_profile_if_present(session, claim.owner_id)
            marker = (
                await session.execute(
                    select(ImportStaging)
                    .where(ImportStaging.import_id == claim.import_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if not _marker_matches(
                marker, claim.owner_id, claim.import_id, claim.bucket, claim.storage_key
            ):
                return False
            assert marker is not None
            if not (
                marker.state == "cleanup_pending"
                and marker.cleanup_token == claim.cleanup_token
            ):
                return False
            if await _has_import_source_reference(session, claim.import_id, claim.storage_key):
                return False
            await session.delete(marker)
        return True


async def _record_cleanup_failure(
    bind: AsyncEngine | AsyncConnection,
    claim: ImportStagingCleanupClaim,
    error: Exception,
) -> None:
    try:
        async with _fresh_session_factory(bind)() as session:
            async with session.begin():
                await _read_committed(session)
                await _lock_profile_if_present(session, claim.owner_id)
                marker = (
                    await session.execute(
                        select(ImportStaging)
                        .where(ImportStaging.import_id == claim.import_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if (
                    _marker_matches(
                        marker, claim.owner_id, claim.import_id, claim.bucket, claim.storage_key
                    )
                    and marker is not None
                    and marker.state == "cleanup_pending"
                    and marker.cleanup_token == claim.cleanup_token
                ):
                    marker.last_error_code = type(error).__name__[:120]
    except Exception:
        # The original cleanup lease expires and remains a retry path.
        pass


async def _lock_profile_if_present(session: AsyncSession, owner_id: str) -> Profile | None:
    return (
        await session.execute(select(Profile).where(Profile.id == owner_id).with_for_update())
    ).scalar_one_or_none()


async def _has_import_source_reference(
    session: AsyncSession, import_id: str, storage_key: str
) -> bool:
    import_ref = await session.scalar(select(Import.id).where(Import.id == import_id))
    if import_ref is not None:
        return True
    outbox_ref = await session.scalar(
        select(TaskOutbox.id).where(
            TaskOutbox.task_type == "import",
            TaskOutbox.target_type == "import",
            TaskOutbox.target_id == import_id,
            TaskOutbox.payload.contains(
                {"import_id": import_id, "storage_key": storage_key}
            ),
        )
    )
    return outbox_ref is not None


def _marker_matches(
    marker: ImportStaging | None,
    owner_id: str,
    import_id: str,
    bucket: str,
    storage_key: str,
) -> bool:
    return bool(
        marker is not None
        and marker.owner_id == owner_id
        and marker.import_id == import_id
        and marker.bucket == bucket
        and marker.storage_key == storage_key
    )


def _fresh_session_factory(bind: AsyncEngine | AsyncConnection) -> Any:
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _read_committed(session: AsyncSession) -> None:
    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})


def _safe_error_code(error_code: str | None) -> str | None:
    return error_code[:120] if error_code else None


class ImportCommitReconciliation(StrEnum):
    """Evidence from a fresh serialized read after an uncertain commit."""

    COMMITTED = "committed"
    PROVEN_UNCOMMITTED = "proven_uncommitted"
    UNKNOWN = "unknown"


async def reconcile_import_commit(
    bind: AsyncEngine | AsyncConnection,
    *,
    import_id: str,
    user_id: str,
    storage_key: str,
    outbox_id: str,
) -> ImportCommitReconciliation:
    """Resolve an intake commit outcome using a fresh READ COMMITTED session.

    Locking the owner profile first serializes this read with the admission
    transaction. Only two matching durable rows prove commit; only both rows
    absent while the owner row exists proves rollback. Every partial or
    mismatched state is deliberately inconclusive.
    """
    if (
        not import_id
        or not user_id
        or not outbox_id
        or storage_key != f"{user_id}/{import_id}.zip"
    ):
        return ImportCommitReconciliation.UNKNOWN

    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        async with session.begin():
            await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
            profile = (
                await session.execute(select(Profile).where(Profile.id == user_id).with_for_update())
            ).scalar_one_or_none()
            if profile is None:
                return ImportCommitReconciliation.UNKNOWN

            import_row = (
                await session.execute(
                    select(Import)
                    .where(Import.id == import_id, Import.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            outbox_row = (
                await session.execute(
                    select(TaskOutbox)
                    .where(
                        TaskOutbox.id == outbox_id,
                        TaskOutbox.task_type == "import",
                        TaskOutbox.target_type == "import",
                        TaskOutbox.target_id == import_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()

            if import_row is None and outbox_row is None:
                return ImportCommitReconciliation.PROVEN_UNCOMMITTED
            payload = getattr(outbox_row, "payload", None)
            if (
                import_row is not None
                and import_row.id == import_id
                and import_row.user_id == user_id
                and outbox_row is not None
                and outbox_row.id == outbox_id
                and isinstance(payload, dict)
                and payload.get("import_id") == import_id
                and payload.get("user_id") == user_id
                and payload.get("storage_key") == storage_key
            ):
                return ImportCommitReconciliation.COMMITTED
            return ImportCommitReconciliation.UNKNOWN


async def record_import_processing_intent(
    session: AsyncSession,
    *,
    import_id: str,
    user_id: str,
    storage_key: str,
    admitted_reservation_keys: set[str] | list[str] | tuple[str, ...],
    already_reserved_reservation_keys: set[str] | list[str] | tuple[str, ...] = (),
) -> str:
    """Persist an idempotent import task in the caller's transaction.

    Repeating this call after a request crash returns the same generation-one
    logical outbox row, so the committed admission is not charged again. The
    caller owns commit/rollback and post-commit dispatch. ``storage_key`` must
    point to the already-staged source needed by the worker to finish later.
    """
    if not import_id or not user_id:
        raise ValueError("import and user identities are required")
    if storage_key != f"{user_id}/{import_id}.zip":
        raise ValueError("import source key must be scoped to its owner and import")

    keys = sorted(set(admitted_reservation_keys))
    already_keys = sorted(set(already_reserved_reservation_keys))
    if not keys and not already_keys:
        raise ValueError("at least one admitted reservation key is required")
    if len(keys) + len(already_keys) > _MAX_ADMITTED_KEYS:
        raise ValueError("too many admitted reservation keys")
    if set(keys).intersection(already_keys):
        raise ValueError("admitted and already-reserved keys must be disjoint")
    if any(_RESERVATION_KEY.fullmatch(key) is None for key in (*keys, *already_keys)):
        raise ValueError("admission keys must be SHA-256 hex digests")

    payload: dict[str, Any] = {
        "import_id": import_id,
        "user_id": user_id,
        "storage_key": storage_key,
        "admitted_reservation_keys": keys,
        "already_reserved_reservation_keys": already_keys,
    }
    intent = await record_task_intent(
        session,
        "import",
        payload,
        target_type="import",
        target_id=import_id,
    )
    return intent.id
