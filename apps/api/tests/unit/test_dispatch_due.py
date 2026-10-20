"""Runner entrypoint must configure the structured logs used by monitoring."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from app.settings import get_settings
from app.tasks import dispatch_due
from app.tasks.outbox import DispatchStats


def test_main_emits_json_batch_log(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()

    async def fake_dispatch_due_jobs(*, limit: int) -> DispatchStats:
        assert limit == 5
        return DispatchStats(
            scanned=3,
            dispatched=2,
            failed=0,
            skipped=1,
            oldest_pending_at=datetime(2026, 10, 18, tzinfo=UTC),
        )

    async def fake_sweep_import_staging(*, bind: object, limit: int) -> SimpleNamespace:
        assert limit == 5
        assert bind is engine
        return SimpleNamespace(examined=2, deleted=1, retained=1, failed=0)

    async def fake_sweep_expired_import_previews(*, bind: object, limit: int) -> SimpleNamespace:
        assert limit == 5
        assert bind is engine
        return SimpleNamespace(examined=4, deleted=3, skipped_owner=0, skipped_changed=1)

    async def close_pool() -> None:
        return None

    async def dispose_engine() -> None:
        return None

    engine = SimpleNamespace(dispose=dispose_engine)
    monkeypatch.setattr(dispatch_due, "sweep_import_staging", fake_sweep_import_staging)
    monkeypatch.setattr(
        dispatch_due, "sweep_expired_import_previews", fake_sweep_expired_import_previews
    )
    monkeypatch.setattr(dispatch_due, "dispatch_due_jobs", fake_dispatch_due_jobs)
    monkeypatch.setattr(dispatch_due, "close_pg_pool", close_pool)
    monkeypatch.setattr(dispatch_due, "get_engine", lambda: engine)

    # main() owns asyncio.run(); retain its original function while observing
    # the final CLI call from this synchronous test.
    real_run = asyncio.run
    monkeypatch.setattr(dispatch_due.asyncio, "run", real_run)
    try:
        assert dispatch_due.main(["--limit", "5"]) == 0
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    finally:
        get_settings.cache_clear()

    preview_record, staging_record, record = records
    assert preview_record["event"] == "imports.preview_metadata_sweep_complete"
    assert preview_record["deleted"] == 3
    assert staging_record["event"] == "imports.staging_sweep_complete"
    assert staging_record["deleted"] == 1
    assert record["event"] == "task.outbox_batch_complete"
    assert record["scanned"] == 3
    assert record["dispatched"] == 2
    assert record["skipped"] == 1
    assert record["failed"] == 0
