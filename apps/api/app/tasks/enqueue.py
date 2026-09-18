"""Cloud Tasks enqueue helpers — abstracted so local dev can run inline.

In prod, tasks are POSTed via Google Cloud Tasks to the worker Cloud Run
service (`worker_base_url` in settings). In dev, we short-circuit to an
in-process asyncio task so `docker compose up` doesn't need a real Cloud
Tasks emulator.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

TaskType = Literal["ingest", "import", "enhance", "delete_user", "category_backfill"]


async def enqueue_task(
    task_type: TaskType,
    payload: dict[str, str | int | float],
    *,
    dedup_key: str | None = None,
) -> None:
    """Enqueue a background task.

    `dedup_key`: if the queue is configured for named-task dedup, using
    the same key within the dedup window will drop the second enqueue
    silently. Used e.g. by ingestion retries.
    """
    settings = get_settings()
    _logger.info(
        "task.enqueue",
        task_type=task_type,
        payload=payload,
        dedup_key=dedup_key,
        env=settings.environment,
    )

    if settings.environment == "dev" and settings.worker_base_url is None:
        # Local shortcut — run in-process. Real Cloud Tasks wiring lands
        # in Phase 0 with the GCP module.
        from app.tasks.local_dispatcher import dispatch_local

        asyncio.create_task(dispatch_local(task_type, payload))
        return

    # TODO(phase-0): implement Cloud Tasks HTTP push to worker_base_url.
    # Keeping this as a soft raise instead of NotImplementedError so the
    # skeleton boots and the /v1/saves happy path works via the local
    # dispatcher during Phase 1 dev.
    _logger.warning(
        "task.cloud_tasks_not_wired",
        task_type=task_type,
        note="worker_base_url set but Cloud Tasks client not yet implemented",
    )
