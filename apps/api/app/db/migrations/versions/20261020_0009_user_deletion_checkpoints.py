"""Persist resumable account-deletion progress outside the profile cascade.

Revision ID: 20261020_0009_user_deletion_checkpoints
Revises: 20261020_0008_deleted_profile_rls
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261020_0009_user_deletion_checkpoints"
down_revision = "20261020_0008_deleted_profile_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_deletion_jobs",
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "next_step",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'storage_media'"),
        ),
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
        sa.CheckConstraint(
            "next_step IN ('storage_media','storage_exports','vectors','profile','auth','complete')",
            name="user_deletion_jobs_step_valid",
        ),
        sa.PrimaryKeyConstraint("user_id", name="user_deletion_jobs_pkey"),
    )
    op.execute("ALTER TABLE public.user_deletion_jobs ENABLE ROW LEVEL SECURITY")
    op.execute(
        """DO $$ DECLARE role_name text; BEGIN
        REVOKE ALL ON TABLE public.user_deletion_jobs FROM PUBLIC;
        FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON TABLE public.user_deletion_jobs FROM %I', role_name);
          END IF;
        END LOOP;
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
          GRANT ALL ON TABLE public.user_deletion_jobs TO service_role;
        END IF;
        END $$"""
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Deletion checkpoints cannot be dropped safely while account purges may be pending"
    )
