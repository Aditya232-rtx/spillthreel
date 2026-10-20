"""Bound semantic import replay and enforce collection tenant integrity.

Revision ID: 20261018_0006_import_identity
Revises: 20261018_0005_import_entries
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261018_0006_import_identity"
down_revision = "20261018_0005_import_entries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("imports", sa.Column("semantic_fingerprint", sa.String(64), nullable=True))
    op.create_check_constraint(
        "imports_semantic_fingerprint_valid", "imports",
        "semantic_fingerprint IS NULL OR semantic_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_unique_constraint(
        "imports_user_semantic_fingerprint_unique", "imports", ["user_id", "semantic_fingerprint"]
    )
    op.create_index("imports_user_started_id_idx", "imports", ["user_id", sa.text("started_at DESC"), sa.text("id DESC")])

    op.create_unique_constraint("collections_id_user_unique", "collections", ["id", "user_id"])
    op.add_column("collection_items", sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=True))
    op.execute("""
        UPDATE collection_items ci
        SET owner_id = c.user_id
        FROM collections c
        WHERE ci.collection_id = c.id
    """)
    op.alter_column("collection_items", "owner_id", nullable=False)
    op.drop_constraint("collection_items_collection_id_fkey", "collection_items", type_="foreignkey")
    op.drop_constraint("collection_items_item_id_fkey", "collection_items", type_="foreignkey")
    op.create_foreign_key(
        "collection_items_collection_owner_fk", "collection_items", "collections",
        ["collection_id", "owner_id"], ["id", "user_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "collection_items_item_owner_fk", "collection_items", "items",
        ["item_id", "owner_id"], ["id", "user_id"], ondelete="CASCADE",
    )
    op.create_index("collection_items_item_owner_idx", "collection_items", ["item_id", "owner_id"])

    op.create_table(
        "import_collections",
        sa.Column("import_id", sa.String(), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("collection_key", sa.String(64), nullable=False),
        sa.Column("collection_id", sa.String(), nullable=False),
        sa.Column("collection_name", sa.Text(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["import_id", "owner_id"], ["imports.id", "imports.user_id"], ondelete="CASCADE", name="import_collections_import_owner_fk"),
        sa.ForeignKeyConstraint(["collection_id", "owner_id"], ["collections.id", "collections.user_id"], ondelete="CASCADE", name="import_collections_collection_owner_fk"),
        sa.PrimaryKeyConstraint("import_id", "collection_key"),
        sa.UniqueConstraint("import_id", "collection_id", name="import_collections_import_collection_unique"),
        sa.CheckConstraint("collection_key ~ '^[0-9a-f]{64}$'", name="import_collections_key_valid"),
    )
    op.create_index("import_collections_owner_idx", "import_collections", ["owner_id", "import_id"])
    op.execute("ALTER TABLE import_collections ENABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON TABLE import_collections FROM PUBLIC, anon, authenticated;")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                GRANT ALL ON TABLE import_collections TO service_role;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_index("import_collections_owner_idx", table_name="import_collections")
    op.drop_table("import_collections")
    op.drop_index("collection_items_item_owner_idx", table_name="collection_items")
    op.drop_constraint("collection_items_collection_owner_fk", "collection_items", type_="foreignkey")
    op.drop_constraint("collection_items_item_owner_fk", "collection_items", type_="foreignkey")
    op.create_foreign_key("collection_items_collection_id_fkey", "collection_items", "collections", ["collection_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("collection_items_item_id_fkey", "collection_items", "items", ["item_id"], ["id"], ondelete="CASCADE")
    op.drop_column("collection_items", "owner_id")
    op.drop_constraint("collections_id_user_unique", "collections", type_="unique")
    op.drop_index("imports_user_started_id_idx", table_name="imports")
    op.drop_constraint("imports_user_semantic_fingerprint_unique", "imports", type_="unique")
    op.drop_constraint("imports_semantic_fingerprint_valid", "imports", type_="check")
    op.drop_column("imports", "semantic_fingerprint")
