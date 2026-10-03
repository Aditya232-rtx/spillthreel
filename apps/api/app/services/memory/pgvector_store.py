"""PgVector MemoryStore — semantic search via pgvector + Gemini embeddings.

Every query is scoped to the authenticated user_id through the
`item_embeddings.user_id` foreign key. No separate namespace string is
needed the way Cognee required one — the database enforces isolation,
which is the security-critical property on this path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector

from app.observability.logging import get_logger
from app.services.memory.base import IndexedItem, MemoryHit, SearchFilters

_logger = get_logger(__name__)

# gemini-embedding-001 natively outputs 3072 dims but supports Matryoshka
# truncation; we request 768 explicitly (cheaper storage + index, plenty
# for short-form captions/transcripts). The migration pins vector(768).
EMBEDDING_DIM = 768


class PgVectorStore:
    """MemoryStore Protocol impl using pgvector + Gemini embeddings."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._embedding_model: Any | None = None

    def _model(self) -> Any:
        # Deferred import: keeps google-genai off the api container's
        # import graph (worker image bundles it; api image doesn't need
        # it until a memory call actually runs).
        if self._embedding_model is None:
            from app.services.llm.gemini import GeminiEmbeddingModel

            self._embedding_model = GeminiEmbeddingModel()
        return self._embedding_model

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """One batched Gemini call for many texts (cost + latency)."""
        if not texts:
            return []
        embeddings: list[list[float]] = await self._model().embed_batch(texts)
        return embeddings

    async def write(self, user_id: str, item: IndexedItem) -> str:
        """Embed one corpus and upsert it. Returns item_id (the row PK)."""
        (embedding,) = await self._embed_texts([item.text_corpus])
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            await conn.execute(
                """
                INSERT INTO item_embeddings (item_id, user_id, embedding, created_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (item_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    created_at = EXCLUDED.created_at
                """,
                item.item_id,
                user_id,
                embedding,
                datetime.now(UTC),
            )
        _logger.info("memory.pgvector.wrote", user_id=user_id, item_id=item.item_id)
        return item.item_id

    async def write_batch(self, user_id: str, items: list[IndexedItem]) -> list[str]:
        """One batched embedding call, then one transaction for the rows."""
        if not items:
            return []
        embeddings = await self._embed_texts([i.text_corpus for i in items])
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            async with conn.transaction():
                for item, embedding in zip(items, embeddings, strict=True):
                    await conn.execute(
                        """
                        INSERT INTO item_embeddings (item_id, user_id, embedding, created_at)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (item_id) DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            created_at = EXCLUDED.created_at
                        """,
                        item.item_id,
                        user_id,
                        embedding,
                        datetime.now(UTC),
                    )
        _logger.info("memory.pgvector.wrote_batch", user_id=user_id, count=len(items))
        return [i.item_id for i in items]

    def _filtered_search_sql(self, filters: SearchFilters) -> tuple[str, str, list[Any]]:
        """WHERE fragments + params for SearchFilters, joined vs items.

        Returns (join_sql, where_sql, params) with $1 reserved for user_id
        and $2 for the query embedding; filter params start at $3.
        """
        joins: list[str] = []
        wheres = ["e.user_id = $1"]
        params: list[Any] = []
        idx = 3

        if filters.platforms:
            wheres.append(f"i.platform = ANY(${idx})")
            params.append(filters.platforms)
            idx += 1
        if filters.only_fully_indexed:
            wheres.append("i.state = 'fully_indexed'")
        if filters.since is not None:
            wheres.append(f"i.saved_at >= ${idx}")
            params.append(filters.since)
            idx += 1
        if filters.categories:
            joins.append(
                "JOIN item_categories ic ON ic.item_id = e.item_id "
                f"JOIN categories c ON c.id = ic.category_id AND c.name = ANY(${idx})"
            )
            params.append(filters.categories)
            idx += 1
        if filters.collection_ids:
            joins.append(
                f"JOIN collection_items ci ON ci.item_id = e.item_id AND ci.collection_id = ANY(${idx})"
            )
            params.append(filters.collection_ids)
            idx += 1

        join_sql = " ".join(joins)
        if join_sql:
            join_sql = "JOIN items i ON i.id = e.item_id " + join_sql
        else:
            # Still join items when its columns are filtered.
            needs_items = (
                filters.platforms or filters.only_fully_indexed or filters.since is not None
            )
            join_sql = "JOIN items i ON i.id = e.item_id" if needs_items else ""
        return join_sql, " AND ".join(wheres), params

    async def search(
        self, user_id: str, query: str, filters: SearchFilters, top_k: int = 8
    ) -> list[MemoryHit]:
        (query_embedding,) = await self._embed_texts([query])
        join_sql, where_sql, params = self._filtered_search_sql(filters)
        sql = f"""
            SELECT e.item_id, 1 - (e.embedding <=> $2) AS score
            FROM item_embeddings e {join_sql}
            WHERE {where_sql}
            ORDER BY e.embedding <=> $2
            LIMIT {top_k:d}
        """
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            rows = await conn.fetch(sql, user_id, query_embedding, *params)
        return [
            MemoryHit(item_id=r["item_id"], score=float(r["score"]), matched_chunks=[])
            for r in rows
        ]

    async def similar(self, user_id: str, item_id: str, top_k: int = 8) -> list[MemoryHit]:
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            row = await conn.fetchrow(
                "SELECT embedding FROM item_embeddings WHERE user_id = $1 AND item_id = $2",
                user_id,
                item_id,
            )
            if row is None:
                return []
            rows = await conn.fetch(
                """
                SELECT item_id, 1 - (embedding <=> $2) AS score
                FROM item_embeddings
                WHERE user_id = $1 AND item_id != $3
                ORDER BY embedding <=> $2
                LIMIT $4
                """,
                user_id,
                row["embedding"],
                item_id,
                top_k,
            )
        return [
            MemoryHit(item_id=r["item_id"], score=float(r["score"]), matched_chunks=[])
            for r in rows
        ]

    async def delete(self, user_id: str, item_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM item_embeddings WHERE user_id = $1 AND item_id = $2",
                user_id,
                item_id,
            )

    async def delete_namespace(self, user_id: str) -> None:
        """Explicit purge for interface compat.

        Normally redundant: the user_id FK is ON DELETE CASCADE, so
        deleting the profile wipes embeddings automatically.
        """
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM item_embeddings WHERE user_id = $1", user_id)
        _logger.info("memory.pgvector.namespace_deleted", user_id=user_id)
