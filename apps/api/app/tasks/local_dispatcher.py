"""Dev-only in-process task dispatcher.

Runs the same handlers Cloud Tasks would hit in prod, so the local dev
loop (docker compose up + curl POST /v1/saves) exercises the real
pipeline without needing a task-emulator container.
"""

from __future__ import annotations

from app.observability.logging import get_logger

_logger = get_logger(__name__)


async def dispatch_local(task_type: str, payload: dict[str, str | int | float]) -> None:
    """Local shim — routes to the same worker handlers Cloud Tasks would."""
    _logger.info("task.local.dispatch", task_type=task_type, payload=payload)

    # Deferred imports to keep the api container from pulling in worker deps
    # (yt-dlp, cognee, google-genai) at boot time.
    if task_type == "ingest":
        _logger.info(
            "task.local.ingest_stub",
            item_id=payload.get("item_id"),
            note="ingestion pipeline not yet implemented (Phase 1)",
        )
    elif task_type == "import":
        _logger.info(
            "task.local.import_stub",
            import_id=payload.get("import_id"),
            note="IG import pipeline not yet implemented (Phase 2)",
        )
    else:
        _logger.warning("task.local.unknown_type", task_type=task_type)
