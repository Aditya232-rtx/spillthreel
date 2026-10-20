"""Index active item leases for the per-owner worker capacity guard.

Revision ID: 20261020_0011_owner_worker_capacity
Revises: 20261020_0010_import_previews
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20261020_0011_owner_worker_capacity"
down_revision = "20261020_0010_import_previews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "items_active_worker_lease_idx",
        "items",
        ["user_id", "worker_lease_until"],
        postgresql_where=sa.text("worker_lease_token IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("items_active_worker_lease_idx", table_name="items")
