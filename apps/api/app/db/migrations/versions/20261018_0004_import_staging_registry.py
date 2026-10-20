"""Track staged import sources until admission or bounded cleanup.

Revision ID: 20261018_0004_import_staging_registry
Revises: 20261018_0003_bulk_import_reservations
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261018_0004_import_staging_registry"
down_revision = "20261018_0003_bulk_import_reservations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_staging",
        sa.Column("import_id", sa.String(), primary_key=True),
        # Intentionally no Profile FK: retain the cleanup pointer across account
        # hard deletion and its earlier Storage prefix sweep.
        sa.Column("owner_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("bucket", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default=sa.text("'staging'")),
        sa.Column("lease_until", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("cleanup_token", sa.String(), nullable=True),
        sa.Column("cleanup_lease_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cleanup_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error_code", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("bucket", "storage_key", name="import_staging_bucket_key_unique"),
        sa.CheckConstraint(
            "storage_key = owner_id::text || '/' || import_id || '.zip'",
            name="import_staging_key_matches_owner",
        ),
        sa.CheckConstraint(
            "import_id ~ '^[0-7][0-9A-HJKMNP-TV-Z]{25}$'",
            name="import_staging_id_ulid_valid",
        ),
        sa.CheckConstraint(
            "state IN ('staging','cleanup_pending')", name="import_staging_state_valid"
        ),
        sa.CheckConstraint(
            "(state = 'staging' AND cleanup_token IS NULL AND cleanup_lease_until IS NULL) OR "
            "(state = 'cleanup_pending' AND cleanup_token IS NOT NULL "
            "AND cleanup_lease_until IS NOT NULL)",
            name="import_staging_cleanup_lease_complete",
        ),
        sa.CheckConstraint("cleanup_attempts >= 0", name="import_staging_attempts_nonnegative"),
    )
    op.create_index("import_staging_owner_idx", "import_staging", ["owner_id"])
    op.create_index(
        "import_staging_state_lease_idx", "import_staging", ["state", "lease_until"]
    )
    op.create_index(
        "import_staging_cleanup_lease_idx",
        "import_staging",
        ["state", "cleanup_lease_until"],
    )
    op.execute("ALTER TABLE import_staging ENABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON TABLE import_staging FROM PUBLIC, anon, authenticated;")
    # Supabase's service_role may be present, but it is not guaranteed in local
    # PostgreSQL test installations. The API's owner role retains table access.
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                GRANT ALL ON TABLE import_staging TO service_role;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.drop_index("import_staging_cleanup_lease_idx", table_name="import_staging")
    op.drop_index("import_staging_state_lease_idx", table_name="import_staging")
    op.drop_index("import_staging_owner_idx", table_name="import_staging")
    op.drop_table("import_staging")
