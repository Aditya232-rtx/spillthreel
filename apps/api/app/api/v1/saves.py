"""POST /v1/saves — the endpoint the share extension calls.

Contract per TRD §6.5:
  Request:  { url, source }  (source ∈ {'share_extension', ...})
  Response (< 300 ms target):
            { itemId, state, duplicateOf }

Flow:
  1. Auth (Supabase JWT → CurrentUser).
  2. Normalize URL, detect platform (400 on unsupported).
  3. INSERT items ON CONFLICT DO NOTHING (dedup on user_id+source_url_norm).
     If conflict, look up existing item and return duplicateOf.
  4. Enqueue Cloud Task `ingest{item_id}`.
  5. Return 201.

The client updates its toast from this synchronous response — the actual
pipeline runs asynchronously and emits state transitions over SSE.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import AnyUrl, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ulid import ULID

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import Item
from app.observability.logging import get_logger
from app.services.ingest.platform_detect import (
    UnsupportedPlatformError,
    detect_platform,
    normalize_url,
)
from app.tasks.enqueue import enqueue_task

router = APIRouter()
_logger = get_logger(__name__)


SaveSource = Literal["share_extension", "manual_paste", "web"]


class SaveRequest(BaseModel):
    url: AnyUrl
    source: SaveSource = "share_extension"


class SaveResponse(BaseModel):
    item_id: str = Field(..., alias="itemId")
    state: str
    duplicate_of: str | None = Field(default=None, alias="duplicateOf")

    model_config = {"populate_by_name": True}


@router.post("/saves", response_model=SaveResponse, status_code=status.HTTP_201_CREATED)
async def create_save(
    body: SaveRequest,
    user: CurrentUser,
    session: DbSession,
) -> SaveResponse:
    url_str = str(body.url)
    try:
        platform = detect_platform(url_str)
    except UnsupportedPlatformError as e:
        _logger.info("saves.rejected_unsupported", url=url_str, reason=str(e), user_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unsupported platform",
        ) from e

    normalized = normalize_url(url_str)
    now = datetime.now(UTC)
    new_id = str(ULID())

    # Upsert-with-dedup: INSERT ... ON CONFLICT DO NOTHING RETURNING id
    stmt = (
        pg_insert(Item)
        .values(
            id=new_id,
            user_id=user.id,
            source_url=url_str,
            source_url_norm=normalized,
            platform=platform,
            state="queued",
            saved_at=now,
            source_type=body.source,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "source_url_norm"])
        .returning(Item.id)
    )
    result = await session.execute(stmt)
    inserted_id = result.scalar_one_or_none()

    if inserted_id is None:
        # Conflict — look up the existing item and return as duplicate.
        existing_stmt = select(Item.id, Item.state).where(
            Item.user_id == user.id, Item.source_url_norm == normalized
        )
        existing = (await session.execute(existing_stmt)).first()
        if existing is None:
            # Extremely unlikely race, but keep the DB the source of truth.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="dedup lookup failed",
            )
        await session.commit()
        _logger.info(
            "saves.duplicate", user_id=user.id, item_id=existing.id, url_norm=normalized
        )
        return SaveResponse(item_id=existing.id, state=existing.state, duplicate_of=existing.id)

    await session.commit()

    await enqueue_task(
        "ingest",
        {"item_id": inserted_id, "user_id": user.id},
        dedup_key=f"ingest-{inserted_id}",
    )
    _logger.info(
        "saves.created",
        user_id=user.id,
        item_id=inserted_id,
        platform=platform,
        source=body.source,
    )
    return SaveResponse(item_id=inserted_id, state="queued", duplicate_of=None)
