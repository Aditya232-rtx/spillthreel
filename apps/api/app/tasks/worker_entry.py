"""Worker HTTP endpoints — Cloud Tasks POSTs here.

Each queue name from settings maps to a route:
  * /work/ingest      — ingest a single item
  * /work/import      — process an IG bulk import (Phase 2)
  * /work/enhance     — same as ingest but from the user "Enhance" action
  * /work/delete_user — cascade account deletion (F5.7)
  * /work/category_backfill — classify existing items into a new category

The router runs as a separate Cloud Run service in prod; in dev it's
attached to the api app via `apps/api/app/worker.py`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.observability.logging import get_logger
from app.services.ingest.pipeline import ingest_item

router = APIRouter(prefix="/work", tags=["worker"])
_logger = get_logger(__name__)


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


# Stubs for the remaining task types — each returns 200 so Cloud Tasks
# doesn't retry them into infinity while the real impls land.


class ImportPayload(BaseModel):
    import_id: str


@router.post("/import", status_code=status.HTTP_200_OK)
async def import_task(payload: ImportPayload) -> dict[str, str]:
    _logger.warning("worker.import.stub", import_id=payload.import_id)
    return {"status": "stub", "import_id": payload.import_id}


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
