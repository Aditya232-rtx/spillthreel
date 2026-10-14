"""GET /v1/items + GET /v1/items/{id} — library listing and detail.

Filtering: platform, state, category_id, collection_id.
Cursor pagination on `saved_at` (DESC); malformed cursors are 422.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import ulid
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, desc, select

from app.auth.middleware import CurrentUser, DbSession
from app.db.engine import get_pg_pool
from app.db.models import CollectionItem, IngestionEvent, Item, ItemCategory
from app.observability.logging import get_logger
from app.services.memory.pgvector_store import PgVectorStore
from app.tasks.enqueue import enqueue_task

router = APIRouter()

_logger = get_logger(__name__)


class ItemSummary(BaseModel):
    id: str
    source_url: str
    platform: str
    state: str
    saved_at: datetime
    title: str | None
    summary: str | None
    thumbnail_url: str | None
    owner_username: str | None

    model_config = {"from_attributes": True}


class ItemDetail(ItemSummary):
    transcript: str | None
    on_screen_text: str | None
    caption: str | None
    hashtags: list[str]
    owner_name: str | None
    owner_url: str | None
    duration_seconds: float | None
    failure_reason: str | None


class ItemsPage(BaseModel):
    items: list[ItemSummary]
    next_cursor: str | None


@router.get("/items", response_model=ItemsPage)
async def list_items(
    user: CurrentUser,
    session: DbSession,
    platform: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    category_id: Annotated[str | None, Query()] = None,
    collection_id: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ItemsPage:
    """List the user's saves, newest first.

    Filters (all optional, ANDed): `platform`, `state`, `category_id`
    (via item_categories), `collection_id` (via collection_items).
    `cursor` is an ISO-8601 `saved_at` upper bound (exclusive); malformed
    values are 422, never 500.
    """
    conditions = [Item.user_id == user.id]
    if platform:
        conditions.append(Item.platform == platform)
    if state:
        conditions.append(Item.state == state)
    if category_id:
        conditions.append(
            select(ItemCategory.item_id)
            .where(
                ItemCategory.item_id == Item.id,
                ItemCategory.category_id == category_id,
            )
            .exists()
        )
    if collection_id:
        conditions.append(
            select(CollectionItem.item_id)
            .where(
                CollectionItem.item_id == Item.id,
                CollectionItem.collection_id == collection_id,
            )
            .exists()
        )
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid cursor",
            ) from e
        conditions.append(Item.saved_at < cursor_dt)

    stmt = select(Item).where(and_(*conditions)).order_by(desc(Item.saved_at)).limit(limit + 1)
    rows = (await session.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = page_rows[-1].saved_at.isoformat() if has_more and page_rows else None

    return ItemsPage(
        items=[ItemSummary.model_validate(r) for r in page_rows],
        next_cursor=next_cursor,
    )


@router.get("/items/{item_id}", response_model=ItemDetail)
async def get_item(item_id: str, user: CurrentUser, session: DbSession) -> ItemDetail:
    stmt = select(Item).where(Item.id == item_id, Item.user_id == user.id)
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return ItemDetail.model_validate(item)


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: str, user: CurrentUser, session: DbSession) -> None:
    stmt = select(Item).where(Item.id == item_id, Item.user_id == user.id)
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.delete(item)
    await session.commit()

    # Embedding row cascades via FK, but delete explicitly too so the
    # vector is gone even if the row delete ever stops cascading.
    await PgVectorStore(await get_pg_pool()).delete(user.id, item_id)


# ---------------------------------------------------------------------------
# POST /v1/items/{id}/retry + /enhance — user-triggered pipeline actions.
# ---------------------------------------------------------------------------

# Enhance is the multimodal upgrade path: text-only (or failed / already
# full — re-run) rows go back through analyze→index on the throttled
# enhance queue. `indexing` is the in-flight state (ITEM_STATES has no
# dedicated "enhancing" state).
_ENHANCEABLE_STATES = frozenset({"text_indexed", "fully_indexed", "failed"})


class ItemActionResponse(BaseModel):
    item_id: str
    state: str


@router.post("/items/{item_id}/retry", response_model=ItemActionResponse)
async def retry_item(item_id: str, user: CurrentUser, session: DbSession) -> ItemActionResponse:
    """Re-queue a failed item for ingestion.

    failed → queued + `failure_reason` cleared + ingest task enqueued
    (same call as the saves path, same dedup key). Already-queued is an
    idempotent no-op (double-tap safe). Anything else → 422.
    """
    stmt = select(Item).where(Item.id == item_id, Item.user_id == user.id)
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if item.state == "queued":
        return ItemActionResponse(item_id=item.id, state="queued")
    if item.state != "failed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"cannot retry item in state {item.state!r}",
        )
    item.state = "queued"
    item.failure_reason = None
    session.add(
        IngestionEvent(
            id=str(ulid.new()),
            item_id=item.id,
            from_state="failed",
            to_state="queued",
            detail={"trigger": "user_retry"},
        )
    )
    await session.commit()
    await enqueue_task(
        "ingest",
        {"item_id": item.id, "user_id": user.id},
        dedup_key=f"ingest-{item.id}",
    )
    _logger.info("items.retry", user_id=user.id, item_id=item.id)
    return ItemActionResponse(item_id=item.id, state="queued")


@router.post("/items/{item_id}/enhance", response_model=ItemActionResponse)
async def request_item_enhance(
    item_id: str, user: CurrentUser, session: DbSession
) -> ItemActionResponse:
    """Queue an item for the multimodal enhance pass.

    Allowed from `text_indexed` / `fully_indexed` (re-run) / `failed`
    only (422 otherwise). Sets state → `indexing` + enqueues on the
    throttled enhance queue.
    """
    stmt = select(Item).where(Item.id == item_id, Item.user_id == user.id)
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if item.state not in _ENHANCEABLE_STATES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"cannot enhance item in state {item.state!r}",
        )
    prev_state = item.state
    item.state = "indexing"
    item.failure_reason = None
    session.add(
        IngestionEvent(
            id=str(ulid.new()),
            item_id=item.id,
            from_state=prev_state,
            to_state="indexing",
            detail={"trigger": "user_enhance"},
        )
    )
    await session.commit()
    await enqueue_task(
        "enhance",
        {"item_id": item.id, "user_id": user.id},
        dedup_key=f"enhance-{item.id}",
    )
    _logger.info("items.enhance", user_id=user.id, item_id=item.id, from_state=prev_state)
    return ItemActionResponse(item_id=item.id, state="indexing")
