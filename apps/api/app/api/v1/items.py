"""GET /v1/items + GET /v1/items/{id} — library listing and detail.

Filtering per PRD F3.4: platform, category, collection, state.
Cursor pagination on `saved_at` (DESC).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, desc, select

from app.auth.middleware import CurrentUser, DbSession
from app.db.engine import get_pg_pool
from app.db.models import Item
from app.services.memory.pgvector_store import PgVectorStore

router = APIRouter()


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
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ItemsPage:
    conditions = [Item.user_id == user.id]
    if platform:
        conditions.append(Item.platform == platform)
    if state:
        conditions.append(Item.state == state)
    if cursor:
        conditions.append(Item.saved_at < datetime.fromisoformat(cursor))

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
