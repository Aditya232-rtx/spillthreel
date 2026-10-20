"""Categories API — auto + user + import sources.

Contract: TRD §8b.5.
Phase 1 scope: CRUD only. The backfill classifier job (`category_backfill`
task) lands in Phase 4 alongside the ingest-time classifier.
"""

from __future__ import annotations

import ulid
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import Category, ItemCategory
from app.observability.logging import get_logger
from app.tasks.outbox import dispatch_after_commit, record_task_intent

router = APIRouter()
_logger = get_logger(__name__)

# Palette rotation for user categories — kept in sync with mobile's
# USER_CATEGORY_PALETTE (apps/mobile/src/data/categories.ts).
_USER_PALETTE = ["#E8AC3D", "#E85C3F", "#6A67E0", "#15170F", "#C7D3C3"]


class CategoryOut(BaseModel):
    id: str
    name: str
    source: str
    emoji: str | None
    bg_color: str
    item_count: int


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=32)
    emoji: str | None = Field(default=None, max_length=8)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(user: CurrentUser, session: DbSession) -> list[CategoryOut]:
    stmt = (
        select(
            Category.id,
            Category.name,
            Category.source,
            Category.emoji,
            Category.bg_color,
            func.count(ItemCategory.item_id).label("item_count"),
        )
        .outerjoin(ItemCategory, ItemCategory.category_id == Category.id)
        .where(Category.user_id == user.id)
        .group_by(Category.id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        CategoryOut(
            id=r.id,
            name=r.name,
            source=r.source,
            emoji=r.emoji,
            bg_color=r.bg_color,
            item_count=int(r.item_count or 0),
        )
        for r in rows
    ]


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    body: CategoryCreate, user: CurrentUser, session: DbSession
) -> CategoryOut:
    # Rotate palette by current user category count.
    count_stmt = select(func.count(Category.id)).where(
        Category.user_id == user.id, Category.source == "user"
    )
    existing_count = int((await session.execute(count_stmt)).scalar_one() or 0)
    bg_color = _USER_PALETTE[existing_count % len(_USER_PALETTE)]

    category = Category(
        id=str(ulid.new()),
        user_id=user.id,
        name=body.name.upper(),
        source="user",
        bg_color=bg_color,
        emoji=body.emoji,
    )
    session.add(category)
    outbox_row = await record_task_intent(
        session,
        "category_backfill",
        {"category_id": category.id, "user_id": user.id},
        target_type="category",
        target_id=category.id,
    )
    try:
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="category name already exists"
        ) from e
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="category create failed",
        ) from e
    await session.refresh(category)

    # The task intent committed atomically with the new category.
    await dispatch_after_commit([outbox_row.id])

    _logger.info(
        "categories.created",
        user_id=user.id,
        category_id=category.id,
        name=category.name,
    )
    return CategoryOut(
        id=category.id,
        name=category.name,
        source=category.source,
        emoji=category.emoji,
        bg_color=category.bg_color,
        item_count=0,
    )


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: str, user: CurrentUser, session: DbSession) -> None:
    stmt = select(Category).where(
        Category.id == category_id,
        Category.user_id == user.id,
        Category.source == "user",  # cannot delete auto/import categories
    )
    category = (await session.execute(stmt)).scalar_one_or_none()
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.delete(category)
    await session.commit()
