from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.tasks.worker_entry as worker
from app.tasks.worker_claims import ClaimResult, WorkerIdentity, WorkerLease


def test_worker_lease_heartbeat_has_safe_headroom() -> None:
    assert worker._lease_heartbeat_interval_seconds() == 60


@pytest.mark.parametrize(
    "payload",
    [
        worker.IngestPayload(item_id="item-1"),
        worker.IngestPayload(item_id="item-1", outbox_id="job-1"),
        worker.IngestPayload(
            item_id="item-1", outbox_id="job-1", generation=1, target_type="other"
        ),
    ],
)
async def test_missing_or_malformed_identity_never_calls_provider(
    payload: worker.IngestPayload, monkeypatch: pytest.MonkeyPatch
) -> None:
    claim = AsyncMock(side_effect=AssertionError("invalid identity reached claim"))
    ingest = AsyncMock(side_effect=AssertionError("provider path reached"))
    monkeypatch.setattr(worker, "claim_worker", claim)
    monkeypatch.setattr(worker, "ingest_item", ingest)

    result = await worker.run_item_task("ingest", payload)

    assert result["status"] == "noop"
    assert result["reason"] == "missing_identity"
    claim.assert_not_awaited()
    ingest.assert_not_awaited()


async def test_completed_duplicate_is_acknowledged_without_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = AsyncMock(return_value=ClaimResult("completed"))
    ingest = AsyncMock(side_effect=AssertionError("completed duplicate invoked provider"))
    monkeypatch.setattr(worker, "claim_worker", claim)
    monkeypatch.setattr(worker, "ingest_item", ingest)
    payload = worker.IngestPayload(item_id="item-1", outbox_id="job-1", generation=4)

    result = await worker.run_item_task("ingest", payload)

    assert result == {"status": "completed", "item_id": "item-1"}
    claim.assert_awaited_once_with(
        WorkerIdentity("job-1", "ingest", "item", "item-1", 4)
    )
    ingest.assert_not_awaited()


async def test_owner_capacity_is_retryable_and_never_reaches_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = AsyncMock(return_value=ClaimResult("capacity"))
    ingest = AsyncMock(side_effect=AssertionError("capacity response invoked provider"))
    monkeypatch.setattr(worker, "claim_worker", claim)
    monkeypatch.setattr(worker, "ingest_item", ingest)
    payload = worker.IngestPayload(item_id="item-1", outbox_id="job-1", generation=4)

    result = await worker.run_item_task("ingest", payload)

    assert result == {"status": "capacity_deferred", "item_id": "item-1"}
    claim.assert_awaited_once()
    ingest.assert_not_awaited()


@pytest.mark.parametrize("handler", ["ingest_task", "enhance_task"])
async def test_capacity_deferral_is_acknowledged_after_durable_reschedule(
    monkeypatch: pytest.MonkeyPatch, handler: str
) -> None:
    monkeypatch.setattr(
        worker,
        "run_item_task",
        AsyncMock(return_value={"status": "capacity_deferred", "item_id": "item-1"}),
    )
    payload = worker.IngestPayload(item_id="item-1", outbox_id="job-1", generation=4)

    result = await getattr(worker, handler)(payload)

    assert result == {"status": "capacity_deferred", "item_id": "item-1"}


async def test_claimed_identity_is_passed_to_pipeline_and_finalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = WorkerIdentity("job-1", "ingest", "item", "item-1", 4)
    lease = WorkerLease(identity, "item-1", "user-1", "secret-token")
    claim = AsyncMock(return_value=ClaimResult("claimed", lease))
    ingest = AsyncMock()
    finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "claim_worker", claim)
    monkeypatch.setattr(worker, "ingest_item", ingest)
    monkeypatch.setattr(worker, "finalize_worker", finalize)
    payload = worker.IngestPayload(item_id="item-1", outbox_id="job-1", generation=4)

    result = await worker.run_item_task("ingest", payload)

    assert result == {"status": "ok", "item_id": "item-1"}
    ingest.assert_awaited_once_with("item-1", lease)
    finalize.assert_awaited_once_with(lease, status="completed")


async def test_heartbeat_failure_cancels_blocked_provider_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = WorkerIdentity("job-1", "ingest", "item", "item-1", 4)
    lease = WorkerLease(identity, "item-1", "user-1", "secret-token")
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_ingest(item_id: str, claimed_lease: WorkerLease) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(worker, "LEASE_SECONDS", 1)
    monkeypatch.setattr(worker, "claim_worker", AsyncMock(return_value=ClaimResult("claimed", lease)))
    monkeypatch.setattr(worker, "renew_worker_lease", AsyncMock(return_value=False))
    monkeypatch.setattr(worker, "ingest_item", blocked_ingest)
    finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "finalize_worker", finalize)
    payload = worker.IngestPayload(item_id="item-1", outbox_id="job-1", generation=4)

    task = asyncio.create_task(worker.run_item_task("ingest", payload))
    await asyncio.wait_for(started.wait(), timeout=1)
    with pytest.raises(RuntimeError, match="worker lease lost"):
        await asyncio.wait_for(task, timeout=2)

    assert cancelled.is_set()
    finalize.assert_awaited_once_with(lease, status="pending", error_code="RuntimeError")


async def test_non_item_task_missing_identity_never_calls_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = AsyncMock(side_effect=AssertionError("missing identity reached DB gate"))
    service = AsyncMock(side_effect=AssertionError("missing identity reached service"))
    monkeypatch.setattr(worker, "claim_target_intent", claim)

    status, result = await worker.run_target_task(
        "delete_user",
        target_type="user",
        target_id="victim",
        owner_id="victim",
        outbox_id=None,
        generation=None,
        operation=service,
    )

    assert status == "invalid" and result is None
    claim.assert_not_awaited()
    service.assert_not_awaited()


async def test_exact_non_item_intent_completes_after_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks.worker_claims import TargetIntentResult

    claim = AsyncMock(return_value=TargetIntentResult("claimed", "target-token"))
    complete = AsyncMock(return_value=True)
    service = AsyncMock(return_value={"status": "ok"})
    monkeypatch.setattr(worker, "claim_target_intent", claim)
    monkeypatch.setattr(worker, "complete_target_intent", complete)

    result = await worker.run_target_task(
        "delete_user",
        target_type="user",
        target_id="user-1",
        owner_id="user-1",
        outbox_id="job-1",
        generation=3,
        operation=service,
    )

    identity = WorkerIdentity("job-1", "delete_user", "user", "user-1", 3)
    assert result == ("ok", {"status": "ok"})
    claim.assert_awaited_once_with(identity, owner_id="user-1", payload_fields=None)
    service.assert_awaited_once_with()
    complete.assert_awaited_once_with(identity, "target-token", owner_id="user-1")


async def test_completed_non_item_duplicate_skips_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks.worker_claims import TargetIntentResult

    claim = AsyncMock(return_value=TargetIntentResult("completed"))
    complete = AsyncMock()
    service = AsyncMock(side_effect=AssertionError("duplicate invoked service"))
    monkeypatch.setattr(worker, "claim_target_intent", claim)
    monkeypatch.setattr(worker, "complete_target_intent", complete)

    result = await worker.run_target_task(
        "delete_user",
        target_type="user",
        target_id="user-1",
        owner_id="user-1",
        outbox_id="job-1",
        generation=3,
        operation=service,
    )

    assert result == ("completed", None)
    service.assert_not_awaited()
    complete.assert_not_awaited()


async def test_import_summary_worker_uses_exact_import_intent_and_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks.worker_claims import TargetIntentResult

    claim = AsyncMock(return_value=TargetIntentResult("claimed", "lease-token"))
    complete = AsyncMock(return_value=True)
    send = AsyncMock(return_value="sent")
    monkeypatch.setattr(worker, "claim_target_intent", claim)
    monkeypatch.setattr(worker, "complete_target_intent", complete)
    monkeypatch.setattr(worker, "send_import_summary", send)

    result = await worker.import_summary_task(
        worker.ImportSummaryPayload(
            user_id="user-1", import_id="import-1", outbox_id="job-1", generation=1
        )
    )

    identity = WorkerIdentity("job-1", "import_summary", "import", "import-1", 1)
    assert result == {"status": "ok", "delivery": "sent"}
    claim.assert_awaited_once_with(
        identity,
        owner_id="user-1",
        payload_fields={"user_id": "user-1", "import_id": "import-1"},
    )
    send.assert_awaited_once_with("user-1", "import-1")
    complete.assert_awaited_once_with(identity, "lease-token", owner_id="user-1")


async def test_import_summary_worker_rejects_missing_outbox_identity() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        worker.ImportSummaryPayload(
            user_id="user-1", import_id="import-1", outbox_id="", generation=1
        )


async def test_non_item_heartbeat_loss_cancels_operation_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.tasks.worker_claims import TargetIntentResult

    identity = WorkerIdentity("job-1", "import", "import", "imp-1", 1)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked() -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(worker, "LEASE_SECONDS", 1)
    monkeypatch.setattr(
        worker, "claim_target_intent", AsyncMock(return_value=TargetIntentResult("claimed", "t"))
    )
    monkeypatch.setattr(worker, "renew_target_intent", AsyncMock(return_value=False))
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "release_target_intent", release)

    task = asyncio.create_task(
        worker.run_target_task(
            "import",
            target_type="import",
            target_id="imp-1",
            owner_id="user-1",
            outbox_id="job-1",
            generation=1,
            operation=blocked,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    with pytest.raises(RuntimeError, match="target intent lease lost"):
        await asyncio.wait_for(task, timeout=2)
    assert cancelled.is_set()
    release.assert_awaited_once_with(
        identity, "t", owner_id="user-1", error_code="RuntimeError"
    )


def _install_fake_google_auth(monkeypatch: pytest.MonkeyPatch, claims: dict[str, object]) -> None:
    google = types.ModuleType("google")
    google.__path__ = []  # type: ignore[attr-defined]
    auth = types.ModuleType("google.auth")
    auth.__path__ = []  # type: ignore[attr-defined]
    transport = types.ModuleType("google.auth.transport")
    transport.__path__ = []  # type: ignore[attr-defined]
    requests = types.ModuleType("google.auth.transport.requests")
    requests.Request = lambda: object()  # type: ignore[attr-defined]
    oauth2 = types.ModuleType("google.oauth2")
    oauth2.__path__ = []  # type: ignore[attr-defined]
    id_token = types.ModuleType("google.oauth2.id_token")
    id_token.verify_oauth2_token = lambda token, request: claims  # type: ignore[attr-defined]
    for name, module in {
        "google": google,
        "google.auth": auth,
        "google.auth.transport": transport,
        "google.auth.transport.requests": requests,
        "google.oauth2": oauth2,
        "google.oauth2.id_token": id_token,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)


def _worker_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("worker.example.test", 443),
            "path": "/work/ingest",
            "query_string": b"",
            "headers": [
                (b"authorization", b"Bearer synthetic-token"),
                (b"host", b"worker.example.test"),
            ],
            "client": ("testclient", 1234),
        }
    )


@pytest.mark.parametrize(
    "email,accepted",
    [
        ("worker@app-project.iam.gserviceaccount.com", True),
        ("other@app-project.iam.gserviceaccount.com", False),
    ],
)
async def test_oidc_requires_exact_allowlisted_service_account(
    email: str, accepted: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_google_auth(
        monkeypatch,
        {
            "iss": "https://accounts.google.com",
            "aud": "https://worker.example.test/work/ingest",
            "email": email,
            "email_verified": True,
        },
    )
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: types.SimpleNamespace(
            environment="staging",
            worker_allowed_sa_emails=["worker@app-project.iam.gserviceaccount.com"],
            worker_base_url="https://worker.example.test",
        ),
    )
    if accepted:
        await worker.verify_worker_oidc(_worker_request())
    else:
        with pytest.raises(HTTPException) as exc:
            await worker.verify_worker_oidc(_worker_request())
        assert exc.value.status_code == 401
