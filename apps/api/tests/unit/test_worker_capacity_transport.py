from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.tasks import worker_entry
from app.tasks.local_dispatcher import dispatch_local


async def test_local_worker_capacity_is_returned_to_outbox_for_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        worker_entry,
        "run_item_task",
        AsyncMock(return_value={"status": "capacity_deferred", "item_id": "item-1"}),
    )

    with pytest.raises(RuntimeError, match="worker_capacity"):
        await dispatch_local(
            "ingest",
            {
                "item_id": "item-1",
                "outbox_id": "job-1",
                "target_type": "item",
                "generation": 1,
            },
        )
