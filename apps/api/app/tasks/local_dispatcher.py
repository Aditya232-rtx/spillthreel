"""Dev-only in-process task dispatcher.

Runs the same handlers Cloud Tasks would hit in prod, so the local dev
loop (docker compose up + curl POST /v1/saves) exercises the real
pipeline without needing a task-emulator container.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

_logger = get_logger(__name__)


async def dispatch_local(task_type: str, payload: dict[str, Any]) -> bool:
    """Local shim — routes to the same worker handlers Cloud Tasks would.

    Deferred imports keep worker-only deps (google-genai, yt-dlp)
    off the api container's import graph until they're actually needed.
    """
    _logger.info("task.local.dispatch", task_type=task_type)

    if task_type == "ingest":
        from app.tasks.worker_entry import run_item_task

        item_id = _required(task_type, payload, "item_id")
        if item_id is None:
            return False
        result = await run_item_task("ingest", _worker_payload(item_id, payload))
        if result.get("status") == "capacity_deferred":
            raise RuntimeError("worker_capacity")
        return True

    if task_type == "import_summary":
        from app.tasks.worker_entry import ImportSummaryPayload, import_summary_task

        user_id = _required(task_type, payload, "user_id")
        import_id = _required(task_type, payload, "import_id")
        outbox_id = _required(task_type, payload, "outbox_id")
        generation = _optional_int(payload, "generation")
        if user_id is None or import_id is None or outbox_id is None or generation is None:
            return False
        await import_summary_task(
            ImportSummaryPayload(
                user_id=user_id,
                import_id=import_id,
                outbox_id=outbox_id,
                generation=generation,
            )
        )
        return True

    if task_type == "enhance":
        from app.tasks.worker_entry import run_item_task

        item_id = _required(task_type, payload, "item_id")
        if item_id is None:
            return False
        result = await run_item_task("enhance", _worker_payload(item_id, payload))
        if result.get("status") == "capacity_deferred":
            raise RuntimeError("worker_capacity")
        return True

    if task_type == "import":
        from app.tasks.worker_entry import ImportPayload, import_task

        import_id = _required(task_type, payload, "import_id")
        user_id = _required(task_type, payload, "user_id")
        storage_key = _required(task_type, payload, "storage_key")
        if import_id is None or user_id is None or storage_key is None:
            return False
        await import_task(
            ImportPayload(
                import_id=import_id,
                user_id=user_id,
                storage_key=storage_key,
                admitted_reservation_keys=(
                    payload.get("admitted_reservation_keys")
                    if isinstance(payload.get("admitted_reservation_keys"), list)
                    else None
                ),
                already_reserved_reservation_keys=(
                    payload.get("already_reserved_reservation_keys")
                    if isinstance(payload.get("already_reserved_reservation_keys"), list)
                    else None
                ),
                outbox_id=_optional_string(payload, "outbox_id"),
                generation=_optional_int(payload, "generation"),
            )
        )
        return True

    if task_type == "delete_user":
        from app.tasks.worker_entry import DeleteUserPayload, delete_user_task

        user_id = _required(task_type, payload, "user_id")
        if user_id is None:
            return False
        await delete_user_task(
            DeleteUserPayload(
                user_id=user_id,
                outbox_id=_optional_string(payload, "outbox_id"),
                generation=_optional_int(payload, "generation"),
            )
        )
        return True

    if task_type == "category_backfill":
        from app.tasks.worker_entry import CategoryBackfillPayload, category_backfill_task

        category_id = _required(task_type, payload, "category_id")
        user_id = _required(task_type, payload, "user_id")
        if category_id is None or user_id is None:
            return False
        await category_backfill_task(
            CategoryBackfillPayload(
                category_id=category_id,
                user_id=user_id,
                outbox_id=_optional_string(payload, "outbox_id"),
                generation=_optional_int(payload, "generation"),
            )
        )
        return True

    _logger.warning("task.local.unknown_type", task_type=task_type)
    return False


def _required(task_type: str, payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        _logger.warning("task.local.invalid_payload", task_type=task_type, missing_field=key)
        return None
    return value


def _worker_payload(item_id: str, payload: dict[str, Any]):
    """Build the same payload shape as Cloud Tasks, without inferring identity."""
    from app.tasks.worker_entry import IngestPayload

    outbox_id = payload.get("outbox_id")
    target_type = payload.get("target_type", "item")
    generation = payload.get("generation")
    return IngestPayload(
        item_id=item_id,
        outbox_id=outbox_id if isinstance(outbox_id, str) else None,
        target_type=target_type if isinstance(target_type, str) else "",
        generation=generation if isinstance(generation, int) else None,
    )


def _optional_string(
    payload: dict[str, Any], key: str
) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None
