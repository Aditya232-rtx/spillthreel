"""Dev-only in-process task dispatcher.

Runs the same handlers Cloud Tasks would hit in prod, so the local dev
loop (docker compose up + curl POST /v1/saves) exercises the real
pipeline without needing a task-emulator container.
"""

from __future__ import annotations

from app.observability.logging import get_logger

_logger = get_logger(__name__)


async def dispatch_local(task_type: str, payload: dict[str, str | int | float]) -> None:
    """Local shim — routes to the same worker handlers Cloud Tasks would.

    Deferred imports keep worker-only deps (google-genai, cognee, yt-dlp)
    off the api container's import graph until they're actually needed.
    """
    _logger.info("task.local.dispatch", task_type=task_type, payload=payload)

    if task_type == "ingest":
        from app.services.ingest.pipeline import ingest_item

        item_id = str(payload.get("item_id"))
        await ingest_item(item_id)
        return

    if task_type == "import":
        _logger.info(
            "task.local.import_stub",
            import_id=payload.get("import_id"),
            note="IG import pipeline not yet implemented (Phase 2)",
        )
        return

    if task_type == "delete_user":
        _logger.info(
            "task.local.delete_user_stub",
            user_id=payload.get("user_id"),
            note="account deletion cascade not yet implemented",
        )
        return

    if task_type == "category_backfill":
        _logger.info(
            "task.local.category_backfill_stub",
            category_id=payload.get("category_id"),
            note="backfill classifier not yet implemented",
        )
        return

    _logger.warning("task.local.unknown_type", task_type=task_type)
