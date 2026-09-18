"""Initial schema: users, items, categories, item_categories, collections,
collection_items, imports, saved_audio, ingestion_events, push_tokens.

Mirrors TRD §9 + §8b.2.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("firebase_uid", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String()),
        sa.Column("display_name", sa.String()),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_url_norm", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("saved_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("transcript", sa.Text()),
        sa.Column("on_screen_text", sa.Text()),
        sa.Column("caption", sa.Text()),
        sa.Column("hashtags", postgresql.ARRAY(sa.String()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column("owner_name", sa.String()),
        sa.Column("owner_username", sa.String()),
        sa.Column("owner_url", sa.String()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("thumbnail_url", sa.Text()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("cognee_id", sa.String()),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "source_url_norm", name="items_user_url_unique"),
        sa.CheckConstraint(
            "state IN ('queued','downloading','analyzing','indexing',"
            "'text_indexed','fully_indexed','source_gone','failed')",
            name="items_state_valid",
        ),
    )
    op.create_index("items_user_saved_at_idx", "items", ["user_id", "saved_at"])
    op.create_index("items_user_platform_idx", "items", ["user_id", "platform"])
    op.create_index("items_user_state_idx", "items", ["user_id", "state"])

    op.create_table(
        "categories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("bg_color", sa.String(), nullable=False),
        sa.Column("emoji", sa.String()),
        sa.Column("origin_id", sa.String()),
        sa.Column("centroid_id", sa.String()),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="categories_user_name_unique"),
        sa.CheckConstraint("source IN ('auto','user','import')", name="categories_source_valid"),
    )

    op.create_table(
        "item_categories",
        sa.Column("item_id", sa.String(), sa.ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_by", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("assigned_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("item_categories_category_idx", "item_categories", ["category_id"])

    op.create_table(
        "collections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("privacy", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="collections_user_name_unique"),
    )

    op.create_table(
        "collection_items",
        sa.Column("collection_id", sa.String(), sa.ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_id", sa.String(), sa.ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("added_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "imports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("total_parsed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("text_indexed_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("full_queued", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("full_done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("parser_schema_hash", sa.String()),
        sa.Column("failure_reason", sa.Text()),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "saved_audio",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("title", sa.String()),
        sa.Column("artist", sa.String()),
        sa.Column("saved_at", postgresql.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "ingestion_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("item_id", sa.String(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_state", sa.String(), nullable=False),
        sa.Column("to_state", sa.String(), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    op.create_table(
        "push_tokens",
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token", sa.String(), primary_key=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    for tbl in (
        "push_tokens",
        "ingestion_events",
        "saved_audio",
        "imports",
        "collection_items",
        "collections",
        "item_categories",
        "categories",
        "items",
        "users",
    ):
        op.drop_table(tbl)
