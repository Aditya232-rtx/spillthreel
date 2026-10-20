"""Bounded cleanup for expired, uncommitted import preview metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import ImportPreview, Profile

MAX_PREVIEW_CLEANUP_BATCH = 200


@dataclass(frozen=True, slots=True)
class ImportPreviewCleanupResult:
    """Outcome of one bounded metadata cleanup transaction."""

    examined: int = 0
    deleted: int = 0
    skipped_owner: int = 0
    skipped_changed: int = 0


async def sweep_expired_import_previews(
    *,
    bind: AsyncEngine | AsyncConnection,
    limit: int = 50,
    now: datetime | None = None,
) -> ImportPreviewCleanupResult:
    """Delete only expired open previews, with account/preview lock ordering.

    Candidate discovery is intentionally unlocked. The fresh READ COMMITTED
    transaction then locks each owner's live Profile before locking its
    preview, matching the preview commit path. The locked row is revalidated
    before deletion so a concurrent commit or extension wins safely. The
    database FK cascade removes candidate PII atomically with its preview.
    Committed previews remain for idempotent commit replay.
    """

    instant = now or datetime.now(UTC)
    batch_limit = max(1, min(int(limit), MAX_PREVIEW_CLEANUP_BATCH))
    engine = bind.engine if isinstance(bind, AsyncConnection) else bind
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    examined = deleted = skipped_owner = skipped_changed = 0

    async with session_factory() as session:
        async with session.begin():
            await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
            candidates = (
                await session.execute(
                    select(ImportPreview.id, ImportPreview.owner_id)
                    .where(
                        ImportPreview.state == "open",
                        ImportPreview.expires_at <= instant,
                    )
                    # Consistent cross-owner lock order avoids sweeper/sweeper
                    # deadlocks when batches contain overlapping owners.
                    .order_by(
                        ImportPreview.owner_id,
                        ImportPreview.expires_at,
                        ImportPreview.id,
                    )
                    .limit(batch_limit)
                )
            ).all()

            for preview_id, owner_id in candidates:
                examined += 1
                profile = (
                    await session.execute(
                        select(Profile)
                        .where(Profile.id == owner_id)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if profile is None or profile.deleted_at is not None:
                    skipped_owner += 1
                    continue

                preview = (
                    await session.execute(
                        select(ImportPreview)
                        .where(
                            ImportPreview.id == preview_id,
                            ImportPreview.owner_id == owner_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if (
                    preview is None
                    or preview.state != "open"
                    or preview.expires_at > instant
                ):
                    skipped_changed += 1
                    continue

                # Rely on the declared ON DELETE CASCADE for candidate rows;
                # deleting them separately would widen the lock/race surface.
                deletion = await session.execute(
                    delete(ImportPreview).where(
                        ImportPreview.id == preview_id,
                        ImportPreview.owner_id == owner_id,
                        ImportPreview.state == "open",
                        ImportPreview.expires_at <= instant,
                    )
                )
                if deletion.rowcount:
                    deleted += 1
                else:
                    skipped_changed += 1

    return ImportPreviewCleanupResult(
        examined=examined,
        deleted=deleted,
        skipped_owner=skipped_owner,
        skipped_changed=skipped_changed,
    )
