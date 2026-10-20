"""GET /v1/items + GET /v1/items/{id} — library listing and detail.

Filtering: platform, state, category_id, collection_id.
Cursor pagination on `saved_at` (DESC); malformed cursors are 422.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated

import ulid
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, desc, func, or_, select

from app.auth.middleware import CurrentUser, DbSession
from app.db.engine import get_pg_pool
from app.db.models import CollectionItem, IngestionEvent, Item, ItemCategory, Profile
from app.observability.logging import get_logger
from app.services.media.supabase_storage import canonicalize_signed_url
from app.services.memory.pgvector_store import PgVectorStore
from app.tasks.outbox import dispatch_after_commit, next_generation, record_task_intent

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

    @field_validator("thumbnail_url")
    @classmethod
    def fix_legacy_storage_url(cls, value: str | None) -> str | None:
        return canonicalize_signed_url(value)


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
    total_count: int


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
    # Count the complete filtered result set before applying the cursor. Keep
    # the owner predicate in this shared base so count and page queries cannot
    # drift across tenants.
    filter_conditions = [Item.user_id == user.id]
    if platform:
        filter_conditions.append(Item.platform == platform)
    if state:
        filter_conditions.append(Item.state == state)
    if category_id:
        filter_conditions.append(
            select(ItemCategory.item_id)
            .where(
                ItemCategory.item_id == Item.id,
                ItemCategory.category_id == category_id,
            )
            .exists()
        )
    if collection_id:
        filter_conditions.append(
            select(CollectionItem.item_id)
            .where(
                CollectionItem.item_id == Item.id,
                CollectionItem.collection_id == collection_id,
            )
            .exists()
        )
    cursor_boundary: tuple[datetime, str] | None = None
    if cursor:
        try:
            cursor_boundary = _decode_items_cursor(cursor)
        except (
            ValueError,
            TypeError,
            KeyError,
            binascii.Error,
            UnicodeEncodeError,
            json.JSONDecodeError,
        ) as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid cursor",
            ) from e

    total_count = await session.scalar(
        select(func.count(Item.id)).where(and_(*filter_conditions))
    )

    page_conditions = list(filter_conditions)
    if cursor_boundary:
        cursor_dt, cursor_id = cursor_boundary
        page_conditions.append(
            or_(Item.saved_at < cursor_dt, and_(Item.saved_at == cursor_dt, Item.id < cursor_id))
        )

    stmt = (
        select(Item)
        .where(and_(*page_conditions))
        .order_by(desc(Item.saved_at), desc(Item.id))
        .limit(limit + 1)
    )
    rows = (await session.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = _encode_items_cursor(page_rows[-1].saved_at, page_rows[-1].id) if has_more and page_rows else None

    return ItemsPage(
        items=[ItemSummary.model_validate(r) for r in page_rows],
        next_cursor=next_cursor,
        total_count=total_count or 0,
    )


def _encode_items_cursor(saved_at: datetime, item_id: str) -> str:
    payload = json.dumps([saved_at.isoformat(), item_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_items_cursor(cursor: str) -> tuple[datetime, str]:
    if len(cursor) > 512:
        raise ValueError("cursor too long")
    padded = cursor + "=" * (-len(cursor) % 4)
    saved_at_raw, item_id = json.loads(base64.urlsafe_b64decode(padded))
    saved_at = datetime.fromisoformat(saved_at_raw)
    if saved_at.tzinfo is None or not isinstance(item_id, str) or not item_id or len(item_id) > 200:
        raise ValueError("invalid cursor values")
    return saved_at, item_id


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
    await _lock_item_action_owner(user.id, item_id, session)
    stmt = select(Item).where(Item.id == item_id, Item.user_id == user.id).with_for_update()
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
    generation = await next_generation(session, "ingest", "item", item.id)
    outbox_row = await record_task_intent(
        session,
        "ingest",
        {"item_id": item.id, "user_id": user.id},
        target_type="item",
        target_id=item.id,
        generation=generation,
    )
    await session.commit()
    await dispatch_after_commit([outbox_row.id])
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
    await _lock_item_action_owner(user.id, item_id, session)
    stmt = select(Item).where(Item.id == item_id, Item.user_id == user.id).with_for_update()
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
    generation = await next_generation(session, "enhance", "item", item.id)
    outbox_row = await record_task_intent(
        session,
        "enhance",
        {"item_id": item.id, "user_id": user.id},
        target_type="item",
        target_id=item.id,
        generation=generation,
    )
    await session.commit()
    await dispatch_after_commit([outbox_row.id])
    _logger.info("items.enhance", user_id=user.id, item_id=item.id, from_state=prev_state)
    return ItemActionResponse(item_id=item.id, state="indexing")


async def _lock_item_action_owner(user_id: str, item_id: str, session: DbSession) -> None:
    """Use the same Profile→Item order as worker claims before state changes."""
    profile = (
        await session.execute(select(Profile).where(Profile.id == user_id).with_for_update())
    ).scalar_one_or_none()
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Lock the target before inspecting its state. `next_generation` rechecks
    # the same rows in this transaction before allocating a new outbox intent.
    item = (
        await session.execute(
            select(Item.id)
            .where(Item.id == item_id, Item.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
