"""pgvector memory layer: extension + item_embeddings, drop items.cognee_id.

Replaces Cognee Cloud with pgvector + Gemini embeddings (output pinned to
768 dims via output_dimensionality). Raw SQL is used throughout: the
vector type and ANN index have no Alembic-native rendering, and raw DDL
keeps exactly what's applied visible.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260930_0001_pgvector_memory"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# Must match PgVectorStore.EMBEDDING_DIM and the output_dimensionality
# requested from gemini-embedding-001 (native dim is 3072; Matryoshka
# truncation to 768 keeps storage/index small for short-form text).
EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute(
        f"""
        CREATE TABLE item_embeddings (
            item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            embedding vector({EMBEDDING_DIM}) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX ix_item_embeddings_user_id ON item_embeddings (user_id);")
    # HNSW (not IVFFlat): the modern pgvector default, robust at small row
    # counts where IVFFlat's lists probing underperforms, and Supabase's
    # bundled pgvector (>=0.7) supports it. Cosine distance matches the
    # `<=>` queries in pgvector_store.py.
    op.execute(
        """
        CREATE INDEX ix_item_embeddings_embedding_hnsw
        ON item_embeddings USING hnsw (embedding vector_cosine_ops);
        """
    )
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS cognee_id;")


def downgrade() -> None:
    op.execute("ALTER TABLE items ADD COLUMN cognee_id TEXT;")
    op.execute("DROP INDEX IF EXISTS ix_item_embeddings_embedding_hnsw;")
    op.execute("DROP INDEX IF EXISTS ix_item_embeddings_user_id;")
    op.execute("DROP TABLE IF EXISTS item_embeddings;")
