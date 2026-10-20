"""Worker HTTP endpoints — Cloud Tasks POSTs here.

Each queue name from settings maps to a route:
  * /work/ingest      — ingest a single item
  * /work/import      — process an IG bulk import (Phase 2)
  * /work/enhance     — same as ingest but from the user "Enhance" action
  * /work/delete_user — cascade account deletion (F5.7)
  * /work/category_backfill — classify existing items into a new category

The router runs as a separate Cloud Run service in prod; in dev it's
attached to the api app via `apps/api/app/worker.py`.

Every /work/* route requires a Google-minted OIDC JWT (Cloud Tasks
pushes with an OIDC token from the api runtime SA). Rejected with 401
otherwise. Dev bypasses verification with an explicit log line.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from collections.abc import Awaitable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.observability.logging import get_logger
from app.services.imports.ig_pipeline import process_stored_import
from app.services.ingest.enhance import enhance_item
from app.services.ingest.pipeline import ingest_item
from app.services.notifications.sender import send_import_summary
from app.tasks.worker_claims import (
    LEASE_SECONDS,
    WorkerIdentity,
    WorkerLease,
    TargetExecution,
    claim_worker,
    claim_target_intent,
    complete_target_intent,
    finalize_worker,
    renew_worker_lease,
    renew_target_intent,
    release_target_intent,
)
from app.settings import Settings, get_settings

_logger = get_logger(__name__)


def _lease_heartbeat_interval_seconds() -> int:
    """Renew well before expiry without waiting on Cloud Run's old 5m default."""
    return max(1, min(LEASE_SECONDS // 3, 60))

# Google OIDC issuers for service-account JWTs (bare + URL forms).
_ALLOWED_OIDC_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


def _host_and_path(url: str) -> tuple[str, str] | None:
    """Split a URL into (host[:port], path) for scheme-insensitive compare.

    Returns None for non-URLs so callers fail closed. Host is lowercased and
    default ports 80/443 are dropped. Path spelling must match exactly.
    Query/fragment are ignored because Cloud Tasks audiences do not carry them.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    hostname = parts.hostname
    if not hostname:
        return None
    host = hostname.lower()
    try:
        port = parts.port
    except ValueError:
        return None
    if port is not None and port not in (80, 443):
        host = f"{host}:{port}"
    path = parts.path or "/"
    return (host, path)


def _oidc_audience_ok(audience: str, request: Request, settings: Settings) -> bool:
    """Accept only this exact push URL, with proxy-aware scheme handling.

    Cloud Tasks mints the token with audience == the push URL
    (`<worker>/work/<route>`), so the exact-request-URL match is the
    hot path. A base-URL prefix would allow route-to-route token replay.

    Behind Cloud Run the ASGI scope scheme is regularly `http` while the
    minted audience is `https`, so after the exact match we (a) rebuild
    the public URL from `x-forwarded-proto` when present, then (b) fall
    back to a scheme-insensitive (host, path) compare. Host AND path must
    both match — a wrong host or wrong path still fails closed.
    """
    request_url = str(request.url)
    if audience == request_url:
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if proto in ("http", "https"):
        try:
            if audience == str(request.url.replace(scheme=proto)):
                return True
        except ValueError:
            pass
    aud_hp = _host_and_path(audience)
    if aud_hp is not None and aud_hp == _host_and_path(request_url):
        return True
    req_hp = _host_and_path(request_url)
    return aud_hp is not None and req_hp is not None and aud_hp == req_hp


async def verify_worker_oidc(request: Request) -> None:
    """FastAPI dependency — gate every /work/* route on a valid OIDC JWT."""
    settings = get_settings()
    if settings.environment == "dev":
        _logger.info("worker.oidc.bypassed_dev", path=request.url.path)
        return

    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        _logger.warning("worker.oidc.missing_token", path=request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")

    # Deferred imports — google-auth lives in the `worker` extra only,
    # keeping the api image lean.
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token as google_id_token

    try:
        # google-auth ships no stubs, so its defs are untyped: bind the
        # verifier to a typed alias and call THAT, keeping strict's
        # no-untyped-call quiet in the full (worker-extras) env. In the
        # lean dev env the module is Any (ignore_missing_imports) and
        # this is a no-op.
        verify_token: Callable[[str, GoogleRequest], dict[str, Any]] = (
            google_id_token.verify_oauth2_token
        )
        claims: dict[str, Any] = verify_token(token, GoogleRequest())
    except Exception as e:
        _logger.warning("worker.oidc.invalid_token", path=request.url.path, err=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid oidc token"
        ) from e

    issuer = str(claims.get("iss", ""))
    if issuer not in _ALLOWED_OIDC_ISSUERS:
        _logger.warning("worker.oidc.bad_issuer", issuer=issuer, path=request.url.path)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid oidc issuer")

    audience = str(claims.get("aud", ""))
    if not _oidc_audience_ok(audience, request, settings):
        _logger.warning("worker.oidc.bad_audience", audience=audience, path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid oidc audience"
        )

    email = str(claims.get("email", ""))
    allowed_emails = {
        value.strip().lower()
        for value in getattr(settings, "worker_allowed_sa_emails", [])
        if isinstance(value, str) and value.strip()
    }
    if (
        not claims.get("email_verified")
        or not email.endswith(".iam.gserviceaccount.com")
        or email.lower() not in allowed_emails
    ):
        _logger.warning("worker.oidc.bad_sa", email=email, path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid oidc service account"
        )

    _logger.info("worker.oidc.verified", email=email, path=request.url.path)


router = APIRouter(prefix="/work", tags=["worker"], dependencies=[Depends(verify_worker_oidc)])


class IngestPayload(BaseModel):
    item_id: str
    user_id: str | None = None
    outbox_id: str | None = None
    target_type: str = "item"
    generation: int | None = None


async def run_item_task(task_type: str, payload: IngestPayload) -> dict[str, str]:
    """Shared exact-identity claim path for HTTP and local dispatch."""
    if (
        task_type not in {"ingest", "enhance"}
        or not payload.outbox_id
        or payload.generation is None
        or payload.generation < 1
        or payload.target_type != "item"
    ):
        return {"status": "noop", "reason": "missing_identity", "item_id": payload.item_id}
    identity = WorkerIdentity(
        outbox_id=payload.outbox_id,
        task_type=task_type,
        target_type=payload.target_type,
        target_id=payload.item_id,
        generation=payload.generation,
    )
    claim = await claim_worker(identity)
    if claim.status != "claimed" or claim.lease is None:
        result_status = "capacity_deferred" if claim.status == "capacity" else claim.status
        return {"status": result_status, "item_id": payload.item_id}

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(_lease_heartbeat_interval_seconds())
            if not await renew_worker_lease(claim.lease):
                raise RuntimeError("worker lease lost")

    async def execute() -> None:
        if task_type == "ingest":
            await ingest_item(payload.item_id, claim.lease)
        else:
            await enhance_item(payload.item_id, claim.lease)

    heartbeat_task = asyncio.create_task(heartbeat())
    operation_task = asyncio.create_task(execute())
    try:
        done, _ = await asyncio.wait(
            {heartbeat_task, operation_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat_task in done:
            # A failed renewal means another generation/worker may now own
            # the item. Stop paid work promptly; database writes still carry
            # their own lease fence in case a provider ignores cancellation.
            heartbeat_task.result()
            raise RuntimeError("worker lease heartbeat stopped unexpectedly")
        await operation_task
    except asyncio.CancelledError:
        operation_task.cancel()
        await asyncio.gather(operation_task, return_exceptions=True)
        await finalize_worker(claim.lease, status="pending", error_code="CancelledError")
        raise
    except Exception as exc:
        operation_task.cancel()
        await asyncio.gather(operation_task, return_exceptions=True)
        await finalize_worker(claim.lease, status="pending", error_code=type(exc).__name__)
        raise
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
    finalized = await finalize_worker(claim.lease, status="completed")
    return {"status": "ok" if finalized else "stale", "item_id": payload.item_id}


@router.post("/ingest", status_code=status.HTTP_200_OK)
async def ingest_task(payload: IngestPayload) -> dict[str, str]:
    """Runs the full ingest pipeline. Raises 500 on any pipeline error so
    Cloud Tasks retries with backoff — after 3 failed attempts the item
    lands in state=failed via pipeline._mark_failed."""
    try:
        result = await run_item_task("ingest", payload)
    except Exception as e:
        _logger.exception("worker.ingest.failed", item_id=payload.item_id, err=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ingest_failed: {e}",
        ) from e
    return result


@router.post("/enhance", status_code=status.HTTP_200_OK)
async def enhance_task(payload: IngestPayload) -> dict[str, str]:
    """Re-runs analyze→index for an existing item. Raises 500 on any error
    so Cloud Tasks retries with backoff — after retries are exhausted the
    item lands in state=failed via enhance._mark_failed."""
    try:
        result = await run_item_task("enhance", payload)
    except Exception as e:
        _logger.exception("worker.enhance.failed", item_id=payload.item_id, err=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"enhance_failed: {e}",
        ) from e
    return result


# Stubs for the remaining task types — each returns 200 so Cloud Tasks
# doesn't retry them into infinity while the real impls land.


class ImportPayload(BaseModel):
    import_id: str
    user_id: str | None = None
    storage_key: str | None = None
    # New staged imports always include the exact opaque reservation keys
    # committed at intake. None is legacy-only compatibility for already queued jobs.
    admitted_reservation_keys: list[str] | None = Field(default=None, max_length=1000)
    already_reserved_reservation_keys: list[str] | None = Field(default=None, max_length=1000)
    outbox_id: str | None = None
    generation: int | None = None

    @field_validator("admitted_reservation_keys")
    @classmethod
    def _reservation_keys_are_opaque_hashes(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(
            len(key) != 64 or any(char not in "0123456789abcdef" for char in key)
            for key in value
        ):
            raise ValueError("admission keys must be SHA-256 hex digests")
        return value

    @field_validator("already_reserved_reservation_keys")
    @classmethod
    def _already_reserved_keys_are_opaque_hashes(
        cls, value: list[str] | None
    ) -> list[str] | None:
        if value is not None and any(
            len(key) != 64 or any(char not in "0123456789abcdef" for char in key)
            for key in value
        ):
            raise ValueError("admission keys must be SHA-256 hex digests")
        return value

    @model_validator(mode="after")
    def _admission_keys_are_disjoint(self) -> ImportPayload:
        if set(self.admitted_reservation_keys or ()).intersection(
            self.already_reserved_reservation_keys or ()
        ):
            raise ValueError("admitted and already-reserved keys must be disjoint")
        return self


async def run_target_task(
    task_type: str,
    *,
    target_type: str,
    target_id: str,
    owner_id: str | None,
    outbox_id: str | None,
    generation: int | None,
    operation: Callable[[], Awaitable[Any]] | None = None,
    execution_operation: Callable[[TargetExecution], Awaitable[Any]] | None = None,
    payload_fields: dict[str, Any] | None = None,
) -> tuple[str, Any | None]:
    """Gate a non-item handler on its exact latest durable outbox intent."""
    if not outbox_id or generation is None or generation < 1:
        return "invalid", None
    identity = WorkerIdentity(outbox_id, task_type, target_type, target_id, generation)
    claim = await claim_target_intent(
        identity, owner_id=owner_id, payload_fields=payload_fields
    )
    if claim.status != "claimed" or claim.token is None:
        return claim.status, None
    execution = TargetExecution(identity=identity, token=claim.token, owner_id=owner_id or "")

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(_lease_heartbeat_interval_seconds())
            if not await renew_target_intent(identity, claim.token, owner_id=owner_id):
                raise RuntimeError("target intent lease lost")

    async def execute() -> Any:
        if execution_operation is not None:
            return await execution_operation(execution)
        if operation is None:
            raise ValueError("target task operation is required")
        return await operation()

    heartbeat_task = asyncio.create_task(heartbeat())
    operation_task = asyncio.create_task(execute())
    try:
        done, _ = await asyncio.wait(
            {heartbeat_task, operation_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat_task in done:
            heartbeat_task.result()
            raise RuntimeError("target intent heartbeat stopped unexpectedly")
        result = await operation_task
        if not await complete_target_intent(identity, claim.token, owner_id=owner_id):
            return "stale", result
        return "ok", result
    except BaseException as exc:
        operation_task.cancel()
        await asyncio.gather(operation_task, return_exceptions=True)
        await release_target_intent(
            identity,
            claim.token,
            owner_id=owner_id,
            error_code=type(exc).__name__,
        )
        raise
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


@router.post("/import", status_code=status.HTTP_200_OK)
async def import_task(payload: ImportPayload) -> dict[str, str | int]:
    """Runs the IG bulk-import pipeline for a staged export ZIP.

    Shares `process_import` with the inline POST /v1/imports path via
    `process_stored_import` (download staged ZIP → parse → upsert →
    enqueue per-item ingest tasks). Raises 500 on transient errors so
    Cloud Tasks retries; deterministic parse failures return 200 with
    status `failed` (the import row carries the reason) so a poison
    payload is not retried into infinity.
    """
    if not payload.user_id or not payload.storage_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="import task requires user_id + storage_key",
        )
    try:
        result_status, outcome = await run_target_task(
            "import",
            target_type="import",
            target_id=payload.import_id,
            owner_id=payload.user_id,
            outbox_id=payload.outbox_id,
            generation=payload.generation,
            execution_operation=lambda execution: process_stored_import(
                payload.import_id,
                payload.user_id,
                payload.storage_key,
                set(payload.admitted_reservation_keys)
                if payload.admitted_reservation_keys is not None
                else None,
                already_reserved_reservation_keys=(
                    set(payload.already_reserved_reservation_keys)
                    if payload.already_reserved_reservation_keys is not None
                    else None
                ),
                execution=execution,
            ),
            payload_fields={
                "user_id": payload.user_id,
                "storage_key": payload.storage_key,
                "admitted_reservation_keys": payload.admitted_reservation_keys,
                "already_reserved_reservation_keys": payload.already_reserved_reservation_keys,
            },
        )
        if outcome is None:
            return {"status": result_status, "import_id": payload.import_id, "total_parsed": 0, "queued": 0}
    except Exception as e:
        _logger.exception("worker.import.failed", import_id=payload.import_id, err=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"import_failed: {e}",
        ) from e
    _logger.info(
        "worker.import.done",
        import_id=payload.import_id,
        ok=outcome.ok,
        total_parsed=outcome.total_parsed,
        queued=outcome.queued,
    )
    return {
        "status": "ok" if outcome.ok else "failed",
        "import_id": payload.import_id,
        "total_parsed": outcome.total_parsed,
        "queued": outcome.queued,
    }


class ImportSummaryPayload(BaseModel):
    user_id: str = Field(min_length=1)
    import_id: str = Field(min_length=1)
    outbox_id: str = Field(min_length=1)
    generation: int = Field(ge=1)


@router.post("/import_summary", status_code=status.HTTP_200_OK)
async def import_summary_task(payload: ImportSummaryPayload) -> dict[str, str]:
    """Send a durable, generic push hint for a terminal import."""
    try:
        result_status, result = await run_target_task(
            "import_summary",
            target_type="import",
            target_id=payload.import_id,
            owner_id=payload.user_id,
            outbox_id=payload.outbox_id,
            generation=payload.generation,
            execution_operation=lambda _execution: send_import_summary(
                payload.user_id, payload.import_id
            ),
            payload_fields={"user_id": payload.user_id, "import_id": payload.import_id},
        )
        return {"status": result_status, "delivery": str(result or "none")}
    except Exception as exc:
        _logger.exception("worker.import_summary.failed", import_id=payload.import_id, err=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="import_summary_failed",
        ) from exc


class DeleteUserPayload(BaseModel):
    user_id: str
    outbox_id: str | None = None
    generation: int | None = None


@router.post("/delete_user", status_code=status.HTTP_200_OK)
async def delete_user_task(payload: DeleteUserPayload) -> dict[str, str]:
    """Purge a user per PRD F5.7: storage → vectors → DB cascade → auth.

    Raises 500 on any failure so Cloud Tasks retries; the purge itself is
    idempotent (missing rows / 404 auth user / empty prefixes are success).
    """
    # Deferred import — keeps the worker-only purge off the api import
    # graph until this route actually runs.
    from app.services.users.deletion import delete_user_account

    try:
        result_status, result = await run_target_task(
            "delete_user",
            target_type="user",
            target_id=payload.user_id,
            owner_id=payload.user_id,
            outbox_id=payload.outbox_id,
            generation=payload.generation,
            execution_operation=lambda execution: delete_user_account(
                payload.user_id, execution
            ),
            payload_fields={"user_id": payload.user_id},
        )
        return result if result is not None else {"status": result_status}
    except Exception as e:
        _logger.exception("worker.delete_user.failed", user_id=payload.user_id, err=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"delete_user_failed: {e}",
        ) from e


class CategoryBackfillPayload(BaseModel):
    category_id: str
    user_id: str
    outbox_id: str | None = None
    generation: int | None = None


@router.post("/category_backfill", status_code=status.HTTP_200_OK)
async def category_backfill_task(
    payload: CategoryBackfillPayload,
) -> dict[str, str | int]:
    """Backfill a new user category over the user's existing items.

    Raises 500 on transient errors so Cloud Tasks retries; the backfill
    itself is idempotent (skips existing rows + ON CONFLICT DO NOTHING).
    A missing category (deleted before the task ran) returns 200 with
    status `missing` — not retryable into existence.
    """
    # Deferred import — keeps the backfill off the api import graph until
    # this route actually runs.
    from app.services.categories.backfill import backfill_category

    try:
        result_status, result = await run_target_task(
            "category_backfill",
            target_type="category",
            target_id=payload.category_id,
            owner_id=payload.user_id,
            outbox_id=payload.outbox_id,
            generation=payload.generation,
            operation=lambda: backfill_category(payload.category_id, payload.user_id),
            payload_fields={"user_id": payload.user_id},
        )
        return result if result is not None else {"status": result_status}
    except Exception as e:
        _logger.exception(
            "worker.category_backfill.failed",
            category_id=payload.category_id,
            user_id=payload.user_id,
            err=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"category_backfill_failed: {e}",
        ) from e
