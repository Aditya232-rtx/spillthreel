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

    Deferred imports keep worker-only deps (google-genai, yt-dlp)
    off the api container's import graph until they're actually needed.
    """
    _logger.info("task.local.dispatch", task_type=task_type, payload=payload)

    if task_type == "ingest":
        from app.services.ingest.pipeline import ingest_item

        item_id = str(payload.get("item_id"))
        await ingest_item(item_id)
        return

    if task_type == "enhance":
        from app.services.ingest.enhance import enhance_item

        item_id = str(payload.get("item_id"))
        await enhance_item(item_id)
        return

    if task_type == "import":
        from app.services.imports.ig_pipeline import process_stored_import

        import_id = str(payload.get("import_id") or "")
        user_id = str(payload.get("user_id") or "")
        storage_key = str(payload.get("storage_key") or "")
        if not import_id or not user_id or not storage_key:
            _logger.warning("task.local.import_missing_fields", payload=payload)
            return
        await process_stored_import(import_id, user_id, storage_key)
        return

    if task_type == "delete_user":
        from app.services.users.deletion import delete_user_account

        user_id = str(payload.get("user_id") or "")
        if not user_id:
            _logger.warning("task.local.delete_user_missing_fields", payload=payload)
            return
        await delete_user_account(user_id)
        return

    if task_type == "category_backfill":
        from app.services.categories.backfill import backfill_category

        category_id = str(payload.get("category_id") or "")
        user_id = str(payload.get("user_id") or "")
        if not category_id or not user_id:
            _logger.warning("task.local.category_backfill_missing_fields", payload=payload)
            return
        await backfill_category(category_id, user_id)
        return

    _logger.warning("task.local.unknown_type", task_type=task_type)
