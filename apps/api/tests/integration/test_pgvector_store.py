"""PgVectorStore write + search round-trip against a real Postgres.

Needs a reachable Postgres with the pgvector extension: the local
`docker compose` stack (pgvector/pgvector:pg16 image) or the CI postgres
service. Skips cleanly when no database answers.

The Gemini embedding call is mocked (deterministic keyword vectors) so
this exercises OUR code — SQL, scoping, filters — never the network.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from app.services.llm.gemini import GeminiEmbeddingModel
from app.services.memory.base import IndexedItem, SearchFilters
from app.services.memory.pgvector_store import PgVectorStore

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://spill:spill@localhost:5432/spillthereel",
)
TEST_USER_ID = "pgvector-test-user"

DIM = 768


def _keyword_vector(texts: list[str]) -> list[list[float]]:
    """Deterministic stand-in: recipe-ish vs workout-ish corners."""

    def vec(text: str) -> list[float]:
        v = [0.0] * DIM
        low = text.lower()
        if "recipe" in low or "ramen" in low or "miso" in low:
            v[0] = 1.0
        elif "workout" in low or "band" in low:
            v[1] = 1.0
        else:
            v[2] = 1.0
        return v

    return [vec(t) for t in texts]


@pytest_asyncio.fixture(scope="module")
async def pg_pool() -> AsyncIterator[asyncpg.Pool]:
    import asyncio

    try:
        pool: asyncpg.Pool = await asyncio.wait_for(
            asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=2),
            timeout=10,
        )
    except Exception:
        pytest.skip(f"no test postgres at {TEST_DATABASE_URL}")

    # Real schema via Alembic (idempotent `upgrade head`), so this tests
    # the actual item_embeddings table, FKs, and HNSW index — not a mock.
    # Fresh process: alembic env.py reads DATABASE_URL at import time.
    env = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        check=True,
        capture_output=True,
    )
    async with pool.acquire() as conn:
        version = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        )
        assert version, "pgvector extension missing — use the pgvector/pgvector image"

    # Idempotent seed: clean slate, then minimal parent rows (FK targets).
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM profiles WHERE id = $1", TEST_USER_ID)
        await conn.execute(
            "INSERT INTO profiles (id) VALUES ($1)",
            TEST_USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO items
                (id, user_id, source_url, source_url_norm, platform, state, saved_at, source_type)
            VALUES
                ('pgv-item-a', $1, 'https://x.test/a', 'https://x.test/a',
                 'instagram', 'fully_indexed', now(), 'share_extension'),
                ('pgv-item-b', $1, 'https://x.test/b', 'https://x.test/b',
                 'tiktok', 'fully_indexed', now(), 'share_extension')
            """,
            TEST_USER_ID,
        )
    yield pool
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM profiles WHERE id = $1", TEST_USER_ID)
    await pool.close()


@pytest_asyncio.fixture()
async def store(pg_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> PgVectorStore:
    async def fake_embed(self: GeminiEmbeddingModel, texts: list[str]) -> list[list[float]]:
        return _keyword_vector(texts)

    monkeypatch.setattr(GeminiEmbeddingModel, "embed_batch", fake_embed)
    return PgVectorStore(pg_pool)


class TestPgVectorRoundTrip:
    async def test_write_then_search_finds_it(self, store: PgVectorStore) -> None:
        await store.write(
            TEST_USER_ID,
            IndexedItem(item_id="pgv-item-a", text_corpus="miso ramen recipe", metadata={}),
        )
        await store.write(
            TEST_USER_ID,
            IndexedItem(item_id="pgv-item-b", text_corpus="resistance band workout", metadata={}),
        )
        hits = await store.search(TEST_USER_ID, "ramen dinner recipe", SearchFilters(), top_k=5)
        assert [h.item_id for h in hits] == ["pgv-item-a", "pgv-item-b"]
        assert hits[0].score > hits[1].score

    async def test_search_respects_platform_filter(self, store: PgVectorStore) -> None:
        hits = await store.search(
            TEST_USER_ID,
            "recipe",
            SearchFilters(platforms=["tiktok"]),
            top_k=5,
        )
        assert [h.item_id for h in hits] == ["pgv-item-b"]

    async def test_similar_excludes_self(self, store: PgVectorStore) -> None:
        hits = await store.similar(TEST_USER_ID, "pgv-item-a", top_k=5)
        assert all(h.item_id != "pgv-item-a" for h in hits)
        assert [h.item_id for h in hits] == ["pgv-item-b"]

    async def test_delete_removes_from_search(self, store: PgVectorStore) -> None:
        await store.delete(TEST_USER_ID, "pgv-item-b")
        hits = await store.search(TEST_USER_ID, "workout", SearchFilters(), top_k=5)
        assert all(h.item_id != "pgv-item-b" for h in hits)
        # Restore for the other tests / teardown.
        await store.write(
            TEST_USER_ID,
            IndexedItem(item_id="pgv-item-b", text_corpus="resistance band workout", metadata={}),
        )

    async def test_delete_namespace_empties(self, store: PgVectorStore) -> None:
        await store.delete_namespace(TEST_USER_ID)
        hits = await store.search(TEST_USER_ID, "recipe", SearchFilters(), top_k=5)
        assert hits == []
