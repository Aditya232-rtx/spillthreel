"""One-shot entry point for local and Cloud Run outbox recovery workers."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from app.db.engine import close_pg_pool, get_engine
from app.observability.logging import configure_logging, get_logger
from app.services.imports.preview_cleanup import sweep_expired_import_previews
from app.services.imports.recovery import sweep_import_staging
from app.tasks.outbox import dispatch_due_jobs

_logger = get_logger(__name__)


async def _run_once(limit: int) -> int:
    try:
        previews = await sweep_expired_import_previews(bind=get_engine(), limit=limit)
        _logger.info(
            "imports.preview_metadata_sweep_complete",
            examined=previews.examined,
            deleted=previews.deleted,
            skipped_owner=previews.skipped_owner,
            skipped_changed=previews.skipped_changed,
        )
        staging = await sweep_import_staging(bind=get_engine(), limit=limit)
        _logger.info(
            "imports.staging_sweep_complete",
            examined=staging.examined,
            deleted=staging.deleted,
            retained=staging.retained,
            failed=staging.failed,
        )
        stats = await dispatch_due_jobs(limit=limit)
        _logger.info(
            "task.outbox_batch_complete",
            scanned=stats.scanned,
            dispatched=stats.dispatched,
            failed=stats.failed,
            skipped=stats.skipped,
            oldest_pending_at=(
                stats.oldest_pending_at.isoformat() if stats.oldest_pending_at else None
            ),
        )
        return 1 if stats.failed or staging.failed else 0
    except Exception as exc:
        # Provider/database exception strings may contain private connection data.
        _logger.error("task.outbox_batch_crashed", error_code=type(exc).__name__)
        return 2
    finally:
        await close_pg_pool()
        await get_engine().dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch a bounded batch of due jobs")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")
    configure_logging()
    return asyncio.run(_run_once(args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
