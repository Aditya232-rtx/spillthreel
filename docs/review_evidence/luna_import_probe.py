"""Offline import regressions; preserve safe outcomes for two reviewed failures.

Run from apps/api: .venv/bin/python ../../docs/review_evidence/luna_import_probe.py
All database, storage and dispatch operations are replaced with existing fakes.
The corresponding focused pytest cases remain the primary regression suite.
"""
import asyncio
import json
import os
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

API = Path(__file__).resolve().parents[2] / "apps/api"
sys.path.insert(0, str(API))
os.environ.update({
    "ENVIRONMENT": "dev",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
    "SUPABASE_SECRET_KEY": "sb_secret_test",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "DATABASE_URL_DIRECT": "postgresql+asyncpg://test:test@localhost:5432/test",
})
import pytest

h = SimpleNamespace(**runpy.run_path(str(API / "tests/unit/test_ig_import.py")))
membership = SimpleNamespace(**runpy.run_path(str(API / "tests/unit/test_import_entries.py")))


def ambiguous_commit():
    class Session(h._FakeSession):
        durable = False

        async def commit(self):
            self.durable = True  # model server COMMIT succeeding
            raise ConnectionError("synthetic lost commit acknowledgement")

    with pytest.MonkeyPatch.context() as patch:
        h._enable_admission(patch)
        session = Session(None)
        patch.setattr(h.imports_module, "parse_zip", lambda _: h._parsed(audio=False))
        patch.setattr(h.imports_module, "upload_object", AsyncMock())
        patch.setattr(h.imports_module, "record_import_processing_intent", AsyncMock(return_value="intent"))
        cleanup = AsyncMock()
        patch.setattr(h.imports_module, "_schedule_import_staging_cleanup", cleanup)
        patch.setattr(
            h.imports_module,
            "reconcile_import_commit",
            AsyncMock(return_value=h.imports_module.ImportCommitReconciliation.UNKNOWN),
        )
        response = h._imports_client(session, raise_server_exceptions=False).post(
            "/v1/imports", files={"file": ("export.zip", b"synthetic", "application/zip")}
        )
        result = {"response": response.status_code, "committed": session.durable,
                  "cleanup_scheduled": cleanup.await_count > 0}
        assert result == {"response": 500, "committed": True, "cleanup_scheduled": False}
        return result


async def retry_counter():
    with pytest.MonkeyPatch.context() as patch:
        memory = membership._MemoryMembership()
        row = membership._import_row()
        membership._install(patch, row, memory)
        source = membership._parsed(
            posts=(membership._post(membership._URL),),
            collections=(
                membership.SavedCollection(
                    "folder", None, None, None, (membership._post(membership._URL_B),)
                ),
            ),
        )
        await membership.pipeline.process_import(
            row.id, membership._OWNER, source, execution=membership._execution()
        )
        first = row.full_queued
        await membership.pipeline.process_import(
            row.id, membership._OWNER, source, execution=membership._execution()
        )
        result = {"before_replay": first, "after_replay": row.full_queued}
        assert result == {"before_replay": 2, "after_replay": 2}
        return result


if __name__ == "__main__":
    print(json.dumps({"ambiguous_commit": ambiguous_commit(),
                      "retry_counter": asyncio.run(retry_counter())}, sort_keys=True))
