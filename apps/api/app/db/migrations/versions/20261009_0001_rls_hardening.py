"""RLS hardening follow-up: search_path pin + UPDATE WITH CHECK.

0001_initial and the pgvector migration already applied to staging+prod,
so their files are frozen — this revision carries the fixes forward:

* Pin `search_path` on the SECURITY DEFINER `handle_new_user()` trigger
  function (search_path hijack hardening).
* Add the missing WITH CHECK clause to the UPDATE policy that only had
  USING (`item_embeddings_update_own`). The DELETE policy is deliberately
  untouched — Postgres rejects WITH CHECK on DELETE policies (USING
  fully gates deletes; there is no new row to check).

One statement per op.execute — asyncpg rejects multi-command statements.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20261009_0001_rls_hardening"
down_revision = "20260930_0001_pgvector_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER FUNCTION public.handle_new_user() SET search_path = public;")
    op.execute("DROP POLICY IF EXISTS item_embeddings_update_own ON item_embeddings;")
    op.execute(
        "CREATE POLICY item_embeddings_update_own ON item_embeddings "
        "FOR UPDATE USING (auth.uid() = user_id) "
        "WITH CHECK (auth.uid() = user_id);"
    )


def downgrade() -> None:
    op.execute("ALTER FUNCTION public.handle_new_user() RESET search_path;")
    op.execute("DROP POLICY IF EXISTS item_embeddings_update_own ON item_embeddings;")
    op.execute(
        "CREATE POLICY item_embeddings_update_own ON item_embeddings "
        "FOR UPDATE USING (auth.uid() = user_id);"
    )
