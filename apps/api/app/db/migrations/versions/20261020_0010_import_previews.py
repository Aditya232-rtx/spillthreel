"""Add durable import preview sessions and selectable candidate identities.

Revision ID: 20261020_0010_import_previews
Revises: 20261020_0009_user_deletion_checkpoints
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_0010_import_previews"
down_revision = "20261020_0009_user_deletion_checkpoints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_previews",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("state", sa.String(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("candidate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("parser_schema_hash", sa.String(), nullable=False),
        sa.Column("semantic_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("selection_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="import_previews_pkey"),
        sa.UniqueConstraint("id", "owner_id", name="import_previews_id_owner_unique"),
        sa.UniqueConstraint(
            "owner_id", "request_key_hash", name="import_previews_owner_request_key_unique"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["profiles.id"], ondelete="CASCADE", name="import_previews_owner_fk"
        ),
        sa.CheckConstraint(
            "id ~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$'", name="import_previews_id_ulid_valid"
        ),
        sa.CheckConstraint(
            "state IN ('open','committed','expired')", name="import_previews_state_valid"
        ),
        sa.CheckConstraint(
            "candidate_count >= 0", name="import_previews_candidate_count_nonnegative"
        ),
        sa.CheckConstraint(
            "semantic_fingerprint ~ '^[0-9a-f]{64}$'",
            name="import_previews_semantic_fingerprint_valid",
        ),
        sa.CheckConstraint(
            "(request_key_hash IS NULL AND request_fingerprint IS NULL) OR "
            "(request_key_hash ~ '^[0-9a-f]{64}$' AND request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="import_previews_request_identity_valid",
        ),
        sa.CheckConstraint(
            "selection_digest IS NULL OR selection_digest ~ '^[0-9a-f]{64}$'",
            name="import_previews_selection_digest_valid",
        ),
        sa.CheckConstraint(
            "(state = 'committed' AND selection_digest IS NOT NULL) OR "
            "(state <> 'committed' AND selection_digest IS NULL)",
            name="import_previews_commit_digest_consistent",
        ),
    )
    op.create_index(
        "import_previews_owner_expiry_idx", "import_previews", ["owner_id", "state", "expires_at"]
    )
    op.create_index(
        "import_previews_open_expiry_idx",
        "import_previews",
        ["expires_at"],
        postgresql_where=sa.text("state = 'open'"),
    )

    op.create_table(
        "import_preview_candidates",
        sa.Column("preview_id", sa.String(), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("reservation_key", sa.String(length=64), nullable=False),
        sa.Column("source_url_norm", sa.Text(), nullable=True),
        sa.Column("source_bucket", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("artist", sa.Text(), nullable=True),
        sa.Column("saved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("collection_names", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("preview_id", "candidate_id", name="import_preview_candidates_pkey"),
        sa.ForeignKeyConstraint(
            ["preview_id", "owner_id"],
            ["import_previews.id", "import_previews.owner_id"],
            ondelete="CASCADE",
            name="import_preview_candidates_preview_owner_fk",
        ),
        sa.CheckConstraint("ordinal >= 0", name="import_preview_candidates_ordinal_nonnegative"),
        sa.CheckConstraint("kind IN ('media','audio')", name="import_preview_candidates_kind_valid"),
        sa.CheckConstraint(
            "source_bucket IN ('instagram','other')", name="import_preview_candidates_bucket_valid"
        ),
        sa.CheckConstraint(
            "reservation_key ~ '^[0-9a-f]{64}$'", name="import_preview_candidates_key_valid"
        ),
        sa.CheckConstraint(
            "(kind = 'media' AND source_url_norm IS NOT NULL) OR "
            "(kind = 'audio' AND source_url_norm IS NULL)",
            name="import_preview_candidates_source_consistent",
        ),
        sa.UniqueConstraint(
            "preview_id", "ordinal", name="import_preview_candidates_preview_ordinal_unique"
        ),
        sa.UniqueConstraint(
            "preview_id", "reservation_key", name="import_preview_candidates_preview_identity_unique"
        ),
    )
    op.create_index(
        "import_preview_candidates_page_idx", "import_preview_candidates", ["preview_id", "ordinal"]
    )

    op.execute("ALTER TABLE public.import_previews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.import_preview_candidates ENABLE ROW LEVEL SECURITY")
    op.execute(
        """DO $$ DECLARE role_name text; BEGIN
        REVOKE ALL ON TABLE public.import_previews FROM PUBLIC;
        REVOKE ALL ON TABLE public.import_preview_candidates FROM PUBLIC;
        FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON TABLE public.import_previews FROM %I', role_name);
            EXECUTE format('REVOKE ALL ON TABLE public.import_preview_candidates FROM %I', role_name);
          END IF;
        END LOOP;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
          GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.import_previews TO service_role;
          GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.import_preview_candidates TO service_role;
        END IF;
        END $$"""
    )

    op.add_column("imports", sa.Column("preview_id", sa.String(), nullable=True))
    op.create_unique_constraint("imports_preview_unique", "imports", ["preview_id"])
    op.create_foreign_key(
        "imports_preview_owner_fk",
        "imports",
        "import_previews",
        ["preview_id", "user_id"],
        ["id", "owner_id"],
        ondelete="SET NULL (preview_id)",
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Import preview rows can reference private staged archives and committed imports; "
        "clean them through the application before removing this schema."
    )
