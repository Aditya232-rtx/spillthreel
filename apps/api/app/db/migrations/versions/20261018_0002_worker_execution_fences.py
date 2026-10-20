"""Separate worker execution leases from durable delivery leases.

Revision ID: 20261018_0002_worker_execution_fences
Revises: 20261018_0001_task_outbox
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261018_0002_worker_execution_fences"
down_revision = "20261018_0001_task_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("worker_lease_token", sa.String(), nullable=True))
    op.add_column(
        "items",
        sa.Column("worker_lease_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("items", sa.Column("worker_task_type", sa.String(), nullable=True))
    op.add_column("items", sa.Column("worker_generation", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "items_worker_lease_complete",
        "items",
        "(worker_lease_token IS NULL AND worker_lease_until IS NULL "
        "AND worker_task_type IS NULL AND worker_generation IS NULL) OR "
        "(worker_lease_token IS NOT NULL AND worker_lease_until IS NOT NULL "
        "AND worker_task_type IS NOT NULL AND worker_generation IS NOT NULL)",
    )
    op.create_check_constraint(
        "items_worker_generation_positive",
        "items",
        "worker_generation IS NULL OR worker_generation > 0",
    )

    op.add_column(
        "task_outbox",
        sa.Column(
            "execution_status",
            sa.String(),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.add_column(
        "task_outbox",
        sa.Column(
            "worker_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "task_outbox",
        sa.Column("worker_completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "task_outbox", sa.Column("worker_last_error_code", sa.String(length=120), nullable=True)
    )
    op.add_column("task_outbox", sa.Column("execution_lease_token", sa.String(), nullable=True))
    op.add_column(
        "task_outbox",
        sa.Column("execution_lease_until", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "task_outbox_execution_status_valid",
        "task_outbox",
        "execution_status IN ('pending','completed','stale','terminal')",
    )
    op.create_check_constraint(
        "task_outbox_worker_attempts_nonnegative",
        "task_outbox",
        "worker_attempt_count >= 0",
    )
    op.create_check_constraint(
        "task_outbox_execution_terminal_timestamp",
        "task_outbox",
        "execution_status NOT IN ('completed','stale','terminal') "
        "OR worker_completed_at IS NOT NULL",
    )
    op.create_check_constraint(
        "task_outbox_execution_lease_complete",
        "task_outbox",
        "(execution_lease_token IS NULL AND execution_lease_until IS NULL) OR "
        "(execution_lease_token IS NOT NULL AND execution_lease_until IS NOT NULL)",
    )
    op.create_check_constraint(
        "task_outbox_terminal_without_execution_lease",
        "task_outbox",
        "execution_status = 'pending' OR "
        "(execution_lease_token IS NULL AND execution_lease_until IS NULL)",
    )
    op.create_index(
        "task_outbox_target_generation_idx",
        "task_outbox",
        ["target_type", "target_id", "task_type", sa.text("generation DESC")],
    )


def downgrade() -> None:
    op.drop_index("task_outbox_target_generation_idx", table_name="task_outbox")
    op.drop_constraint(
        "task_outbox_terminal_without_execution_lease", "task_outbox", type_="check"
    )
    op.drop_constraint("task_outbox_execution_lease_complete", "task_outbox", type_="check")
    op.drop_constraint(
        "task_outbox_execution_terminal_timestamp", "task_outbox", type_="check"
    )
    op.drop_constraint("task_outbox_worker_attempts_nonnegative", "task_outbox", type_="check")
    op.drop_constraint("task_outbox_execution_status_valid", "task_outbox", type_="check")
    op.drop_column("task_outbox", "worker_last_error_code")
    op.drop_column("task_outbox", "worker_completed_at")
    op.drop_column("task_outbox", "worker_attempt_count")
    op.drop_column("task_outbox", "execution_lease_until")
    op.drop_column("task_outbox", "execution_lease_token")
    op.drop_column("task_outbox", "execution_status")
    op.drop_constraint("items_worker_generation_positive", "items", type_="check")
    op.drop_constraint("items_worker_lease_complete", "items", type_="check")
    op.drop_column("items", "worker_generation")
    op.drop_column("items", "worker_task_type")
    op.drop_column("items", "worker_lease_until")
    op.drop_column("items", "worker_lease_token")
