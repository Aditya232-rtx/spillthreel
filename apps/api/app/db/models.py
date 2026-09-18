"""SQLAlchemy models — mirrors the Postgres schema in trd.md §9 + §8b.2.

Any migration to the schema starts here: change the model, then generate
an Alembic revision with `alembic revision --autogenerate`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    MappedAsDataclass,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase, MappedAsDataclass):
    """Base for all ORM models — dataclass style for cleaner constructors."""

    type_annotation_map = {dict[str, Any]: JSON}


# ---------------------------------------------------------------------------
# profiles — custom fields for Supabase's auth.users.
#
# Supabase provides `auth.users` automatically; we do NOT create our own
# users table. `profiles.id` is the SAME UUID as `auth.users.id`, wired
# up in the migration via `ON DELETE CASCADE` so account deletion
# cleans everything up in one shot.
#
# Note: we can't declare the FK to `auth.users` at the ORM level (it
# lives in a different Postgres schema Supabase manages), so it's
# declared in the raw SQL migration only. From SQLAlchemy's POV this
# is just a table keyed by a UUID whose provenance is external.
# ---------------------------------------------------------------------------
class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID == auth.users.id
    display_name: Mapped[str | None] = mapped_column(String, default=None)
    avatar_url: Mapped[str | None] = mapped_column(String, default=None)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=text("now()"),
        init=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )


# ---------------------------------------------------------------------------
# items — indexed content
# ---------------------------------------------------------------------------
ITEM_STATES = (
    "queued",
    "downloading",
    "analyzing",
    "indexing",
    "text_indexed",
    "fully_indexed",
    "source_gone",
    "failed",
)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("user_id", "source_url_norm", name="items_user_url_unique"),
        CheckConstraint(
            "state IN ('queued','downloading','analyzing','indexing',"
            "'text_indexed','fully_indexed','source_gone','failed')",
            name="items_state_valid",
        ),
        Index("items_user_saved_at_idx", "user_id", "saved_at"),
        Index("items_user_platform_idx", "user_id", "platform"),
        Index("items_user_state_idx", "user_id", "state"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url_norm: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)  # 'share_extension' | 'instagram_import'

    title: Mapped[str | None] = mapped_column(Text, default=None)
    summary: Mapped[str | None] = mapped_column(Text, default=None)
    transcript: Mapped[str | None] = mapped_column(Text, default=None)
    on_screen_text: Mapped[str | None] = mapped_column(Text, default=None)
    caption: Mapped[str | None] = mapped_column(Text, default=None)
    hashtags: Mapped[list[str]] = mapped_column(
        ARRAY(String), server_default=text("'{}'::text[]"), default_factory=list
    )
    owner_name: Mapped[str | None] = mapped_column(String, default=None)
    owner_username: Mapped[str | None] = mapped_column(String, default=None)
    owner_url: Mapped[str | None] = mapped_column(String, default=None)
    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, default=None)
    failure_reason: Mapped[str | None] = mapped_column(Text, default=None)
    cognee_id: Mapped[str | None] = mapped_column(String, default=None)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# categories + item_categories — per trd.md §8b.2
# ---------------------------------------------------------------------------
CATEGORY_SOURCES = ("auto", "user", "import")


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="categories_user_name_unique"),
        CheckConstraint(
            "source IN ('auto','user','import')", name="categories_source_valid"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    bg_color: Mapped[str] = mapped_column(String, nullable=False)

    emoji: Mapped[str | None] = mapped_column(String, default=None)
    origin_id: Mapped[str | None] = mapped_column(String, default=None)
    centroid_id: Mapped[str | None] = mapped_column(String, default=None)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class ItemCategory(Base):
    __tablename__ = "item_categories"
    __table_args__ = (
        Index("item_categories_category_idx", "category_id"),
    )

    item_id: Mapped[str] = mapped_column(
        String, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        String, ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_by: Mapped[str] = mapped_column(String, nullable=False)  # 'classifier' | 'user' | 'import'
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    assigned_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# collections + collection_items
# ---------------------------------------------------------------------------
class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="collections_user_name_unique"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    privacy: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # 'instagram_import' | 'user_created'
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class CollectionItem(Base):
    __tablename__ = "collection_items"

    collection_id: Mapped[str] = mapped_column(
        String, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(
        String, ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


# ---------------------------------------------------------------------------
# imports — bulk import job records
# ---------------------------------------------------------------------------
class Import(Base):
    __tablename__ = "imports"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String, nullable=False)  # 'parsing' | 'indexing' | 'complete' | 'failed'
    total_parsed: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    text_indexed_done: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    full_queued: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    full_done: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    failed: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    parser_schema_hash: Mapped[str | None] = mapped_column(String, default=None)
    failure_reason: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )


# ---------------------------------------------------------------------------
# saved_audio, ingestion_events, push_tokens
# ---------------------------------------------------------------------------
class SavedAudio(Base):
    __tablename__ = "saved_audio"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String, default=None)
    artist: Mapped[str | None] = mapped_column(String, default=None)
    saved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), default=None
    )


class IngestionEvent(Base):
    __tablename__ = "ingestion_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String, ForeignKey("items.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[str] = mapped_column(String, nullable=False)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )


class PushToken(Base):
    __tablename__ = "push_tokens"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    token: Mapped[str] = mapped_column(String, primary_key=True)
    platform: Mapped[str] = mapped_column(String, nullable=False)  # 'ios' | 'android'
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), init=False
    )
