"""Persist unique imported media membership and parsed occurrences.

Revision ID: 20261018_0005_import_entries
Revises: 20261018_0004_import_staging_registry
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261018_0005_import_entries"
down_revision = "20261018_0004_import_staging_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("imports_id_user_unique", "imports", ["id", "user_id"])
    op.add_column("imports", sa.Column("request_key_hash", sa.String(length=64), nullable=True))
    op.add_column("imports", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.create_unique_constraint(
        "imports_user_request_key_unique", "imports", ["user_id", "request_key_hash"]
    )
    op.create_check_constraint(
        "imports_request_identity_valid",
        "imports",
        "(request_key_hash IS NULL AND request_fingerprint IS NULL) OR "
        "(request_key_hash ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$')",
    )
    op.create_check_constraint(
        "imports_counters_nonnegative",
        "imports",
        "total_parsed >= 0 AND text_indexed_done >= 0 AND full_queued >= 0 "
        "AND full_done >= 0 AND failed >= 0",
    )
    op.create_unique_constraint("items_id_user_unique", "items", ["id", "user_id"])

    op.create_table(
        "import_entries",
        sa.Column("import_id", sa.String(), nullable=False),
        sa.Column("reservation_key", sa.String(length=64), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("item_id", sa.String(), nullable=True),
        sa.Column("source_url_norm", sa.Text(), nullable=False),
        sa.Column("source_bucket", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("work_queued_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "owner_id"],
            ["imports.id", "imports.user_id"],
            name="import_entries_import_owner_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["item_id", "owner_id"],
            ["items.id", "items.user_id"],
            name="import_entries_item_owner_fk",
            ondelete="SET NULL (item_id)",
        ),
        sa.PrimaryKeyConstraint("import_id", "reservation_key"),
        sa.CheckConstraint(
            "reservation_key ~ '^[0-9a-f]{64}$'",
            name="import_entries_reservation_key_valid",
        ),
        sa.CheckConstraint(
            "source_bucket IN ('instagram','other')",
            name="import_entries_source_bucket_valid",
        ),
        sa.CheckConstraint(
            "state IN ('queued','text_indexed','completed','retryable_failed','terminal_failed')",
            name="import_entries_state_valid",
        ),
    )
    op.create_index("import_entries_owner_import_idx", "import_entries", ["owner_id", "import_id"])
    op.create_index("import_entries_import_owner_idx", "import_entries", ["import_id", "owner_id"])
    op.create_index("import_entries_item_idx", "import_entries", ["item_id"])
    op.create_index("import_entries_import_state_idx", "import_entries", ["import_id", "state"])

    op.create_table(
        "import_entry_occurrences",
        sa.Column("import_id", sa.String(), nullable=False),
        sa.Column("occurrence_key", sa.String(length=64), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("source_position", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("entry_reservation_key", sa.String(length=64), nullable=True),
        sa.Column("source_url_norm", sa.Text(), nullable=True),
        sa.Column("collection_key", sa.String(length=64), nullable=True),
        sa.Column("collection_name", sa.Text(), nullable=True),
        sa.Column("saved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "owner_id"],
            ["imports.id", "imports.user_id"],
            name="import_occurrences_import_owner_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["import_id", "entry_reservation_key"],
            ["import_entries.import_id", "import_entries.reservation_key"],
            name="import_occurrences_entry_fk",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("import_id", "occurrence_key"),
        sa.UniqueConstraint(
            "import_id", "source_position", name="import_occurrences_import_position_unique"
        ),
        sa.CheckConstraint(
            "source_position >= 0", name="import_occurrences_position_nonnegative"
        ),
        sa.CheckConstraint(
            "occurrence_key ~ '^[0-9a-f]{64}$'",
            name="import_occurrences_key_valid",
        ),
        sa.CheckConstraint(
            "entry_reservation_key IS NULL OR entry_reservation_key ~ '^[0-9a-f]{64}$'",
            name="import_occurrences_entry_key_valid",
        ),
        sa.CheckConstraint(
            "source_kind IN ('media','audio','unsupported')",
            name="import_occurrences_kind_valid",
        ),
        sa.CheckConstraint(
            "outcome IN ('admitted','already_reserved','unsupported','audio_metadata','excluded')",
            name="import_occurrences_outcome_valid",
        ),
        sa.CheckConstraint(
            "((outcome IN ('admitted','already_reserved') AND entry_reservation_key IS NOT NULL) "
            "OR (outcome IN ('unsupported','audio_metadata','excluded') "
            "AND entry_reservation_key IS NULL))",
            name="import_occurrences_outcome_entry_consistent",
        ),
    )
    op.create_index(
        "import_occurrences_import_entry_idx",
        "import_entry_occurrences",
        ["import_id", "entry_reservation_key"],
    )
    op.create_index(
        "import_occurrences_import_owner_idx",
        "import_entry_occurrences",
        ["import_id", "owner_id"],
    )

    for table in ("import_entries", "import_entry_occurrences"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC, anon, authenticated;")
        op.execute(
            f"""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                    GRANT ALL ON TABLE {table} TO service_role;
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    op.drop_index("import_occurrences_import_entry_idx", table_name="import_entry_occurrences")
    op.drop_index("import_occurrences_import_owner_idx", table_name="import_entry_occurrences")
    op.drop_table("import_entry_occurrences")
    op.drop_index("import_entries_import_state_idx", table_name="import_entries")
    op.drop_index("import_entries_item_idx", table_name="import_entries")
    op.drop_index("import_entries_owner_import_idx", table_name="import_entries")
    op.drop_index("import_entries_import_owner_idx", table_name="import_entries")
    op.drop_table("import_entries")
    op.drop_constraint("items_id_user_unique", "items", type_="unique")
    op.drop_constraint("imports_user_request_key_unique", "imports", type_="unique")
    op.drop_constraint("imports_counters_nonnegative", "imports", type_="check")
    op.drop_constraint("imports_request_identity_valid", "imports", type_="check")
    op.drop_column("imports", "request_fingerprint")
    op.drop_column("imports", "request_key_hash")
    op.drop_constraint("imports_id_user_unique", "imports", type_="unique")
