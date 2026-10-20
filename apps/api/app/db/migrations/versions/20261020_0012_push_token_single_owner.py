"""Ensure an installation push token belongs to only one account.

Revision ID: 20261020_0012_push_token_single_owner
Revises: 20261020_0011_owner_worker_capacity
"""

from __future__ import annotations

from alembic import op

revision = "20261020_0012_push_token_single_owner"
down_revision = "20261020_0011_owner_worker_capacity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Prevent a registration from inserting a duplicate between cleanup and
    # unique-constraint creation. This migration is intentionally transactional.
    # Bound the wait in case a staging client holds the table lock. Alembic
    # executes this revision in one transaction, so both limits are local to it.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '120s'")
    op.execute("LOCK TABLE push_tokens IN ACCESS EXCLUSIVE MODE")
    # Older clients could register the same installation token under multiple
    # accounts. Keep the most recently refreshed owner before enforcing the
    # invariant. Push sending is not enabled, so this only removes ambiguous
    # stale routing records.
    op.execute("""
        WITH ranked AS (
            SELECT user_id, token,
                   row_number() OVER (
                       PARTITION BY token
                       ORDER BY created_at DESC NULLS LAST, user_id ASC
                   ) AS owner_rank
            FROM push_tokens
        )
        DELETE FROM push_tokens AS target
        USING ranked
        WHERE target.user_id = ranked.user_id
          AND target.token = ranked.token
          AND ranked.owner_rank > 1
    """)
    op.create_unique_constraint(
        "push_tokens_token_unique", "push_tokens", ["token"]
    )


def downgrade() -> None:
    # Duplicate registrations removed by upgrade cannot be reconstructed.
    op.drop_constraint(
        "push_tokens_token_unique", "push_tokens", type_="unique"
    )
