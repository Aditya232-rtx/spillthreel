"""Initial schema: profiles + items + categories + item_categories +
collections + collection_items + imports + saved_audio + ingestion_events
+ push_tokens.

All user-scoped tables key on `auth.users(id)` (Supabase-managed UUID)
via a `profiles` mirror row created by a trigger on `auth.users` insert.
Every user-scoped table has RLS enabled with `USING (auth.uid() = user_id)`.

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
    # ------------------------------------------------------------------
    # profiles — mirrors auth.users. FK to auth schema declared in raw
    # SQL because SQLAlchemy can't cross Postgres schemas cleanly.
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE profiles (
          id            UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
          display_name  TEXT,
          avatar_url    TEXT,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          deleted_at    TIMESTAMPTZ
        );

        ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
        CREATE POLICY profiles_select_own ON profiles FOR SELECT USING (auth.uid() = id);
        CREATE POLICY profiles_update_own ON profiles FOR UPDATE USING (auth.uid() = id);

        -- Auto-create profile row on new Supabase Auth signup.
        CREATE OR REPLACE FUNCTION public.handle_new_user() RETURNS trigger AS $$
        BEGIN
          INSERT INTO public.profiles (id, display_name)
          VALUES (NEW.id, COALESCE(NEW.raw_user_meta_data->>'full_name',
                                   NEW.raw_user_meta_data->>'name',
                                   NEW.email));
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;

        DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
        CREATE TRIGGER on_auth_user_created
          AFTER INSERT ON auth.users
          FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();
    """)

    # ------------------------------------------------------------------
    # items
    # ------------------------------------------------------------------
    op.create_table(
        "items",
        sa.Column("id", sa.String(), primary_key=True),  # ULID
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
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

    op.execute("""
        ALTER TABLE items ENABLE ROW LEVEL SECURITY;
        CREATE POLICY items_select_own ON items FOR SELECT USING (auth.uid() = user_id);
        CREATE POLICY items_insert_own ON items FOR INSERT WITH CHECK (auth.uid() = user_id);
        CREATE POLICY items_update_own ON items FOR UPDATE USING (auth.uid() = user_id);
        CREATE POLICY items_delete_own ON items FOR DELETE USING (auth.uid() = user_id);
    """)

    # ------------------------------------------------------------------
    # categories + item_categories
    # ------------------------------------------------------------------
    op.create_table(
        "categories",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
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
    op.execute("""
        ALTER TABLE categories ENABLE ROW LEVEL SECURITY;
        CREATE POLICY categories_all_own ON categories FOR ALL
          USING (auth.uid() = user_id)
          WITH CHECK (auth.uid() = user_id);
    """)

    op.create_table(
        "item_categories",
        sa.Column("item_id", sa.String(), sa.ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.String(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_by", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("assigned_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("item_categories_category_idx", "item_categories", ["category_id"])
    op.execute("""
        ALTER TABLE item_categories ENABLE ROW LEVEL SECURITY;
        CREATE POLICY item_categories_via_item ON item_categories FOR ALL
          USING (EXISTS (SELECT 1 FROM items i WHERE i.id = item_id AND i.user_id = auth.uid()))
          WITH CHECK (EXISTS (SELECT 1 FROM items i WHERE i.id = item_id AND i.user_id = auth.uid()));
    """)

    # ------------------------------------------------------------------
    # collections + collection_items
    # ------------------------------------------------------------------
    op.create_table(
        "collections",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("privacy", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("user_id", "name", name="collections_user_name_unique"),
    )
    op.execute("""
        ALTER TABLE collections ENABLE ROW LEVEL SECURITY;
        CREATE POLICY collections_all_own ON collections FOR ALL
          USING (auth.uid() = user_id)
          WITH CHECK (auth.uid() = user_id);
    """)

    op.create_table(
        "collection_items",
        sa.Column("collection_id", sa.String(), sa.ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("item_id", sa.String(), sa.ForeignKey("items.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("added_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.execute("""
        ALTER TABLE collection_items ENABLE ROW LEVEL SECURITY;
        CREATE POLICY collection_items_via_collection ON collection_items FOR ALL
          USING (EXISTS (SELECT 1 FROM collections c WHERE c.id = collection_id AND c.user_id = auth.uid()))
          WITH CHECK (EXISTS (SELECT 1 FROM collections c WHERE c.id = collection_id AND c.user_id = auth.uid()));
    """)

    # ------------------------------------------------------------------
    # imports
    # ------------------------------------------------------------------
    op.create_table(
        "imports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
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
    op.execute("""
        ALTER TABLE imports ENABLE ROW LEVEL SECURITY;
        CREATE POLICY imports_all_own ON imports FOR ALL
          USING (auth.uid() = user_id)
          WITH CHECK (auth.uid() = user_id);
    """)

    # ------------------------------------------------------------------
    # saved_audio
    # ------------------------------------------------------------------
    op.create_table(
        "saved_audio",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("title", sa.String()),
        sa.Column("artist", sa.String()),
        sa.Column("saved_at", postgresql.TIMESTAMP(timezone=True)),
    )
    op.execute("""
        ALTER TABLE saved_audio ENABLE ROW LEVEL SECURITY;
        CREATE POLICY saved_audio_all_own ON saved_audio FOR ALL
          USING (auth.uid() = user_id)
          WITH CHECK (auth.uid() = user_id);
    """)

    # ------------------------------------------------------------------
    # ingestion_events — read-only for users; writes come from workers
    # (service-role, bypasses RLS).
    # ------------------------------------------------------------------
    op.create_table(
        "ingestion_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("item_id", sa.String(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_state", sa.String(), nullable=False),
        sa.Column("to_state", sa.String(), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.execute("""
        ALTER TABLE ingestion_events ENABLE ROW LEVEL SECURITY;
        CREATE POLICY ingestion_events_via_item ON ingestion_events FOR SELECT
          USING (EXISTS (SELECT 1 FROM items i WHERE i.id = item_id AND i.user_id = auth.uid()));
    """)

    # ------------------------------------------------------------------
    # push_tokens
    # ------------------------------------------------------------------
    op.create_table(
        "push_tokens",
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("token", sa.String(), primary_key=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.execute("""
        ALTER TABLE push_tokens ENABLE ROW LEVEL SECURITY;
        CREATE POLICY push_tokens_all_own ON push_tokens FOR ALL
          USING (auth.uid() = user_id)
          WITH CHECK (auth.uid() = user_id);
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users")
    op.execute("DROP FUNCTION IF EXISTS public.handle_new_user()")
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
        "profiles",
    ):
        op.drop_table(tbl)
