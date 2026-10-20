"""Add durable background-task intent for transactional dispatch.

Revision ID: 20261018_0001_task_outbox
Revises: 20261009_0001_rls_hardening
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261018_0001_task_outbox"
down_revision = "20261009_0001_rls_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_outbox",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "available_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_token", sa.String(), nullable=True),
        sa.Column("lease_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dispatched_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "task_type",
            "target_type",
            "target_id",
            "generation",
            name="task_outbox_logical_job_unique",
        ),
        sa.CheckConstraint("generation > 0", name="task_outbox_generation_positive"),
        sa.CheckConstraint("attempt_count >= 0", name="task_outbox_attempts_nonnegative"),
    )
    op.create_index(
        "task_outbox_dispatch_idx",
        "task_outbox",
        ["dispatched_at", "available_at", "lease_until"],
    )
    # Internal dispatcher/worker state must not be directly available through
    # Supabase's client roles. No client policy is defined for this relation.
    op.execute("ALTER TABLE task_outbox ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    op.drop_index("task_outbox_dispatch_idx", table_name="task_outbox")
    op.drop_table("task_outbox")
