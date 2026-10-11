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

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.observability.logging import get_logger
from app.services.imports.ig_pipeline import process_stored_import
from app.services.ingest.enhance import enhance_item
from app.services.ingest.pipeline import ingest_item
from app.settings import Settings, get_settings

_logger = get_logger(__name__)

# Google OIDC issuers for service-account JWTs (bare + URL forms).
_ALLOWED_OIDC_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})


def _host_and_path(url: str) -> tuple[str, str] | None:
    """Split a URL into (host[:port], path) for scheme-insensitive compare.

    Returns None for non-URLs so callers fail closed. Host is lowercased,
    default ports 80/443 are dropped, trailing slash stripped (except root),
    query/fragment ignored — Cloud Tasks audiences never carry them.
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
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return (host, path)


def _oidc_audience_ok(audience: str, request: Request, settings: Settings) -> bool:
    """Accept the exact push URL, or anything under the worker base URL.

    Cloud Tasks mints the token with audience == the push URL
    (`<worker>/work/<route>`), so the exact-request-URL match is the
    hot path; the base-URL prefix covers trailing-slash drift.

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
    base = (settings.worker_base_url or "").rstrip("/")
    if not base:
        return False
    if audience == base or audience.startswith(base + "/"):
        return True
    base_hp = _host_and_path(base)
    if base_hp is None or aud_hp is None:
        return False
    base_host, base_path = base_hp
    aud_host, aud_path = aud_hp
    return aud_host == base_host and (
        base_path == "/" or aud_path == base_path or aud_path.startswith(base_path + "/")
    )


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
    if not claims.get("email_verified") or not email.endswith(".iam.gserviceaccount.com"):
        _logger.warning("worker.oidc.bad_sa", email=email, path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid oidc service account"
        )

    _logger.info("worker.oidc.verified", email=email, path=request.url.path)


router = APIRouter(prefix="/work", tags=["worker"], dependencies=[Depends(verify_worker_oidc)])


class IngestPayload(BaseModel):
    item_id: str
    user_id: str | None = None


@router.post("/ingest", status_code=status.HTTP_200_OK)
async def ingest_task(payload: IngestPayload) -> dict[str, str]:
    """Runs the full ingest pipeline. Raises 500 on any pipeline error so
    Cloud Tasks retries with backoff — after 3 failed attempts the item
    lands in state=failed via pipeline._mark_failed."""
    try:
        await ingest_item(payload.item_id)
    except Exception as e:
        _logger.exception("worker.ingest.failed", item_id=payload.item_id, err=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ingest_failed: {e}",
        ) from e
    return {"status": "ok", "item_id": payload.item_id}


@router.post("/enhance", status_code=status.HTTP_200_OK)
async def enhance_task(payload: IngestPayload) -> dict[str, str]:
    """Re-runs analyze→index for an existing item. Raises 500 on any error
    so Cloud Tasks retries with backoff — after retries are exhausted the
    item lands in state=failed via enhance._mark_failed."""
    try:
        await enhance_item(payload.item_id)
    except Exception as e:
        _logger.exception("worker.enhance.failed", item_id=payload.item_id, err=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"enhance_failed: {e}",
        ) from e
    return {"status": "ok", "item_id": payload.item_id}


# Stubs for the remaining task types — each returns 200 so Cloud Tasks
# doesn't retry them into infinity while the real impls land.


class ImportPayload(BaseModel):
    import_id: str
    user_id: str | None = None
    storage_key: str | None = None


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
        outcome = await process_stored_import(
            payload.import_id, payload.user_id, payload.storage_key
        )
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


class DeleteUserPayload(BaseModel):
    user_id: str


@router.post("/delete_user", status_code=status.HTTP_200_OK)
async def delete_user_task(payload: DeleteUserPayload) -> dict[str, str]:
    _logger.warning("worker.delete_user.stub", user_id=payload.user_id)
    return {"status": "stub", "user_id": payload.user_id}


class CategoryBackfillPayload(BaseModel):
    category_id: str
    user_id: str


@router.post("/category_backfill", status_code=status.HTTP_200_OK)
async def category_backfill_task(payload: CategoryBackfillPayload) -> dict[str, str]:
    _logger.warning(
        "worker.category_backfill.stub",
        category_id=payload.category_id,
        user_id=payload.user_id,
    )
    return {"status": "stub", "category_id": payload.category_id}
