"""Persist a bound on explicit import-entry retries.

Revision ID: 20261018_0007_import_retry_bound
Revises: 20261018_0006_import_identity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261018_0007_import_retry_bound"
down_revision = "20261018_0006_import_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "import_entries",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint(
        "import_entries_retry_count_nonnegative", "import_entries", "retry_count >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(
        "import_entries_retry_count_nonnegative", "import_entries", type_="check"
    )
    op.drop_column("import_entries", "retry_count")
