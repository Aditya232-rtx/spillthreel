"""Add durable once-per-account bulk import reservations.

Revision ID: 20261018_0003_bulk_import_reservations
Revises: 20261018_0002_worker_execution_fences
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261018_0003_bulk_import_reservations"
down_revision = "20261018_0002_worker_execution_fences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing imports remain explicitly legacy-compatible. New admissions set
    # this fence so a keyless worker payload cannot bypass the quota filter.
    op.add_column(
        "imports",
        sa.Column("admission_enforced", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "bulk_import_reservations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reservation_key", sa.String(length=64), nullable=False),
        sa.Column("source_bucket", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id", "reservation_key", name="bulk_reservation_user_key_unique"
        ),
        sa.CheckConstraint(
            "source_bucket IN ('instagram','other')", name="bulk_reservation_bucket_valid"
        ),
        sa.CheckConstraint("tier IN ('free','premium')", name="bulk_reservation_tier_valid"),
    )
    op.create_index(
        "bulk_reservation_user_tier_bucket_idx",
        "bulk_import_reservations",
        ["user_id", "tier", "source_bucket"],
    )
    # The API service role owns admission. Mobile Supabase roles cannot read or
    # write accounting records directly, even if future default grants change.
    op.execute("ALTER TABLE bulk_import_reservations ENABLE ROW LEVEL SECURITY;")
    op.execute("REVOKE ALL ON TABLE bulk_import_reservations FROM PUBLIC, anon, authenticated;")


def downgrade() -> None:
    op.drop_index(
        "bulk_reservation_user_tier_bucket_idx", table_name="bulk_import_reservations"
    )
    op.drop_table("bulk_import_reservations")
    op.drop_column("imports", "admission_enforced")
