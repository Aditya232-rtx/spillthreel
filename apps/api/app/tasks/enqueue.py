"""Cloud Tasks enqueue helpers — abstracted so local dev can run inline.

In prod, tasks are POSTed via Google Cloud Tasks to the worker Cloud Run
service (`worker_base_url` in settings). In dev, we short-circuit to an
in-process asyncio task so `docker compose up` doesn't need a real Cloud
Tasks emulator.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

TaskType = Literal["ingest", "import", "enhance", "delete_user", "category_backfill"]

# Fire-and-forget tasks must be referenced or the event loop may cancel
# them mid-flight on GC. Entries remove themselves when done.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

# TaskType -> (settings attr holding the queue name, worker route).
# delete_user / category_backfill have no dedicated queue in
# infra/modules/cloud_tasks (three queues per architecture.md), so they
# share the ingest queue. enhance hits /work/enhance — the lighter
# analyze→index re-run triggered by the user "Enhance" action.
_TASK_ROUTES: dict[str, tuple[str, str]] = {
    "ingest": ("cloud_tasks_queue_ingest", "ingest"),
    "enhance": ("cloud_tasks_queue_enhance", "enhance"),
    "import": ("cloud_tasks_queue_import", "import"),
    "delete_user": ("cloud_tasks_queue_ingest", "delete_user"),
    "category_backfill": ("cloud_tasks_queue_ingest", "category_backfill"),
}

# Cloud Tasks resource names allow letters, digits, hyphens, underscores.
_TASK_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


def _task_name(parent: str, dedup_key: str) -> str:
    safe = _TASK_NAME_RE.sub("-", dedup_key)[:500]
    return f"{parent}/tasks/{safe}"


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
        # Local shortcut — run in-process so `docker compose up` doesn't
        # need a real Cloud Tasks emulator.
        from app.tasks.local_dispatcher import dispatch_local

        task = asyncio.create_task(dispatch_local(task_type, payload))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return

    # worker_base_url is set (staging/prod, or dev pointed at a worker):
    # real Cloud Tasks HTTP push. Fail CLOSED outside dev — a dropped
    # ingest must page, never vanish into a warning log.
    await _enqueue_cloud_task(task_type, payload, dedup_key=dedup_key)


async def _enqueue_cloud_task(
    task_type: TaskType,
    payload: dict[str, str | int | float],
    *,
    dedup_key: str | None,
) -> None:
    # Deferred import — google-cloud-tasks lives in the `worker` extra
    # only, keeping the api image lean.
    from google.cloud import tasks_v2

    settings = get_settings()
    queue_attr, route = _TASK_ROUTES[task_type]
    queue_name: str = getattr(settings, queue_attr)
    base_url = (settings.worker_base_url or "").rstrip("/")
    project = settings.gcp_project_id
    sa_email = settings.cloud_tasks_oidc_sa_email

    missing = [
        name
        for name, value in (
            ("WORKER_BASE_URL", settings.worker_base_url),
            ("GCP_PROJECT_ID", project),
            ("CLOUD_TASKS_OIDC_SA_EMAIL", sa_email),
        )
        if not value
    ]
    if missing:
        msg = f"cloud tasks not configured (missing: {', '.join(missing)})"
        _logger.error("task.cloud_tasks_missing_config", task_type=task_type, missing=missing)
        if settings.environment != "dev":
            raise RuntimeError(msg)
        # Dev explicitly pointed at a worker but misconfigured — fall
        # back to the local dispatcher so the loop keeps working.
        from app.tasks.local_dispatcher import dispatch_local

        await dispatch_local(task_type, payload)
        return

    # mypy: missing-branch above guarantees non-None; assert for the checker.
    assert project is not None and sa_email is not None and base_url

    url = f"{base_url}/work/{route}"
    parent = f"projects/{project}/locations/{settings.gcp_region}/queues/{queue_name}"
    body = json.dumps(payload).encode("utf-8")

    task: dict[str, Any] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "body": body,
            # OIDC mint comes from the api Cloud Run runtime SA —
            # infra must grant it roles/run.invoker on the worker.
            "oidc_token": {"service_account_email": sa_email, "audience": url},
        }
    }
    if dedup_key:
        task["name"] = _task_name(parent, dedup_key)

    try:
        client = tasks_v2.CloudTasksClient()
        await asyncio.to_thread(
            client.create_task,
            tasks_v2.CreateTaskRequest(parent=parent, task=task),
        )
    except Exception as e:
        _logger.exception("task.cloud_tasks_push_failed", task_type=task_type, queue=queue_name)
        if settings.environment != "dev":
            raise RuntimeError(f"cloud tasks push failed: {e}") from e
        from app.tasks.local_dispatcher import dispatch_local

        await dispatch_local(task_type, payload)
        return

    _logger.info("task.cloud_tasks_enqueued", task_type=task_type, queue=queue_name, url=url)
