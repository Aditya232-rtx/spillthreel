"""Task transport for Cloud Tasks and the local worker shim.

Database durability and retries live in :mod:`app.tasks.outbox`; this module
only submits an already-committed logical job to its transport.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

TaskType = Literal[
    "ingest", "import", "enhance", "delete_user", "category_backfill", "import_summary"
]

_TASK_ROUTES: dict[str, tuple[str, str]] = {
    "ingest": ("cloud_tasks_queue_ingest", "ingest"),
    "enhance": ("cloud_tasks_queue_enhance", "enhance"),
    "import": ("cloud_tasks_queue_import", "import"),
    "delete_user": ("cloud_tasks_queue_ingest", "delete_user"),
    "category_backfill": ("cloud_tasks_queue_ingest", "category_backfill"),
    "import_summary": ("cloud_tasks_queue_ingest", "import_summary"),
}
_TASK_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")
_CLOUD_RPC_TIMEOUT_SECONDS = 5.0
# Cloud Tasks permits HTTP dispatch deadlines up to 30 minutes. Media
# ingestion may outlive its 10-minute default, so keep this above the worker
# request timeout configured for Cloud Run (25 minutes).
_CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS = 30 * 60


def logical_task_name_key(
    task_type: str,
    target_type: str,
    target_id: str,
    generation: int,
    *,
    delivery_attempt: int = 1,
) -> str:
    """Queue name for one attempt to deliver an immutable logical identity."""
    raw = f"{task_type}-{target_type}-{target_id}-g{generation}-d{delivery_attempt}"
    return _TASK_NAME_RE.sub("-", raw)[:500]


def uses_local_transport() -> bool:
    """Whether dev dispatch executes a handler in this process.

    Local handlers run only in the durable outbox runner. Request handlers must
    leave those rows pending so media work cannot extend request latency.
    """
    settings = get_settings()
    return settings.environment == "dev" and _cloud_configuration_missing(settings)


def _cloud_configuration_missing(settings: Any) -> bool:
    return not all(
        (
            settings.worker_base_url,
            settings.gcp_project_id,
            settings.cloud_tasks_oidc_sa_email,
        )
    )


async def enqueue_task(
    task_type: TaskType,
    payload: dict[str, Any],
    *,
    logical_key: str,
) -> None:
    """Submit a committed outbox intent; errors are handled by its dispatcher."""
    if uses_local_transport():
        from app.tasks.local_dispatcher import dispatch_local

        if not await dispatch_local(task_type, payload):
            raise RuntimeError("local_dispatch_rejected_payload")
        return
    await _enqueue_cloud_task(task_type, payload, logical_key=logical_key)


async def _enqueue_cloud_task(
    task_type: TaskType,
    payload: dict[str, Any],
    *,
    logical_key: str,
) -> None:
    from google.api_core.exceptions import AlreadyExists
    from google.api_core.retry import Retry
    from google.cloud import tasks_v2
    from google.protobuf.duration_pb2 import Duration

    settings = get_settings()
    queue_attr, route = _TASK_ROUTES[task_type]
    queue_name: str = getattr(settings, queue_attr)
    base_url = (settings.worker_base_url or "").rstrip("/")
    project = settings.gcp_project_id
    sa_email = settings.cloud_tasks_oidc_sa_email
    if _cloud_configuration_missing(settings):
        if settings.environment == "dev":
            from app.tasks.local_dispatcher import dispatch_local

            if not await dispatch_local(task_type, payload):
                raise RuntimeError("local_dispatch_rejected_payload")
            return
        raise RuntimeError("cloud_tasks_missing_configuration")

    assert project is not None and sa_email is not None and base_url
    url = f"{base_url}/work/{route}"
    parent = f"projects/{project}/locations/{settings.gcp_region}/queues/{queue_name}"
    task: dict[str, Any] = {
        "name": f"{parent}/tasks/{logical_key}",
        "dispatch_deadline": Duration(seconds=_CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS),
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(payload).encode("utf-8"),
            "oidc_token": {"service_account_email": sa_email, "audience": url},
        },
    }
    client = tasks_v2.CloudTasksClient()
    request = tasks_v2.CreateTaskRequest(parent=parent, task=task)
    try:
        await asyncio.to_thread(
            client.create_task,
            request,
            retry=Retry(initial=0.2, maximum=1.0, multiplier=2.0, deadline=3.0),
            timeout=_CLOUD_RPC_TIMEOUT_SECONDS,
        )
    except AlreadyExists:
        # The named task is derived only from (operation,target,generation),
        # so this exact collision is the same logical outbox delivery.
        _logger.info("task.cloud_tasks_already_exists", task_type=task_type)
        return
    except Exception as exc:
        # Exception strings may contain request data, endpoints or credentials.
        _logger.warning(
            "task.cloud_tasks_push_failed",
            task_type=task_type,
            error_code=type(exc).__name__,
        )
        raise RuntimeError("cloud_tasks_push_failed") from None
    _logger.info("task.cloud_tasks_enqueued", task_type=task_type, queue=queue_name)
