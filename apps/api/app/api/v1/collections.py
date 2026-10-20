"""Owner-scoped collection and collection-item operations."""

from __future__ import annotations

import ulid
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.auth.middleware import CurrentUser, DbSession
from app.db.models import Collection, CollectionItem, Item, Profile

router = APIRouter()


class CollectionOut(BaseModel):
    id: str
    name: str
    privacy: str
    source: str
    item_count: int
    contains_item: bool = False


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


async def _require_active_profile(
    user_id: str, session: DbSession, *, lock_for_write: bool = False
) -> None:
    query = select(Profile.id).where(Profile.id == user_id, Profile.deleted_at.is_(None))
    if lock_for_write:
        # Keep the live-account check valid until the mutation commits. FOR SHARE
        # conflicts with the FOR NO KEY UPDATE/UPDATE locks used by deletion.
        query = query.with_for_update(read=True)
    active = await session.scalar(query)
    if active is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")


async def _owned_collection(collection_id: str, user_id: str, session: DbSession) -> Collection:
    collection = await session.scalar(
        select(Collection).where(
            Collection.id == collection_id,
            Collection.user_id == user_id,
        )
    )
    if collection is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return collection


async def _owned_item(item_id: str, user_id: str, session: DbSession) -> Item:
    item = await session.scalar(
        select(Item).where(Item.id == item_id, Item.user_id == user_id)
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
    return item


@router.get("/collections", response_model=list[CollectionOut])
async def list_collections(
    user: CurrentUser,
    session: DbSession,
    item_id: str | None = Query(default=None),
) -> list[CollectionOut]:
    await _require_active_profile(user.id, session)
    if item_id is not None:
        await _owned_item(item_id, user.id, session)
    contains_item = func.coalesce(
        func.bool_or(CollectionItem.item_id == item_id) if item_id is not None else False,
        False,
    ).label("contains_item")
    rows = (
        await session.execute(
            select(
                Collection.id,
                Collection.name,
                Collection.privacy,
                Collection.source,
                func.count(CollectionItem.item_id).label("item_count"),
                contains_item,
            )
            .outerjoin(
                CollectionItem,
                (CollectionItem.collection_id == Collection.id)
                & (CollectionItem.owner_id == Collection.user_id),
            )
            .where(Collection.user_id == user.id)
            .group_by(Collection.id)
            .order_by(Collection.created_at, Collection.id)
        )
    ).all()
    return [
        CollectionOut(
            id=row.id,
            name=row.name,
            privacy=row.privacy,
            source=row.source,
            item_count=int(row.item_count or 0),
            contains_item=bool(row.contains_item),
        )
        for row in rows
    ]


@router.post(
    "/collections",
    response_model=CollectionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    body: CollectionCreate, user: CurrentUser, session: DbSession
) -> CollectionOut:
    await _require_active_profile(user.id, session, lock_for_write=True)
    collection = Collection(
        id=str(ulid.new()),
        user_id=user.id,
        name=body.name,
        privacy="private",
        source="user_created",
    )
    session.add(collection)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        original = exc.orig
        constraint_name = None
        for cause in (original, getattr(original, "__cause__", None)):
            if cause is None:
                continue
            constraint_name = getattr(cause, "constraint_name", None)
            if constraint_name is None:
                constraint_name = getattr(getattr(cause, "diag", None), "constraint_name", None)
            if constraint_name:
                break
        if constraint_name == "collections_user_name_unique":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="collection name already exists"
            ) from exc
        raise
    return CollectionOut(
        id=collection.id,
        name=collection.name,
        privacy=collection.privacy,
        source=collection.source,
        item_count=0,
    )


@router.put(
    "/collections/{collection_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def add_collection_item(
    collection_id: str, item_id: str, user: CurrentUser, session: DbSession
) -> None:
    await _require_active_profile(user.id, session, lock_for_write=True)
    await _owned_collection(collection_id, user.id, session)
    await _owned_item(item_id, user.id, session)
    try:
        await session.execute(
            insert(CollectionItem)
            .values(collection_id=collection_id, item_id=item_id, owner_id=user.id)
            .on_conflict_do_nothing(
                index_elements=[
                    CollectionItem.collection_id,
                    CollectionItem.item_id,
                ]
            )
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        # A concurrent deletion may invalidate a previously checked row. Keep
        # the response indistinguishable from any other missing owner-scoped row.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found") from exc


@router.delete(
    "/collections/{collection_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_collection_item(
    collection_id: str, item_id: str, user: CurrentUser, session: DbSession
) -> None:
    await _require_active_profile(user.id, session, lock_for_write=True)
    await _owned_collection(collection_id, user.id, session)
    await _owned_item(item_id, user.id, session)
    await session.execute(
        delete(CollectionItem).where(
            CollectionItem.collection_id == collection_id,
            CollectionItem.item_id == item_id,
            CollectionItem.owner_id == user.id,
        )
    )
    await session.commit()
