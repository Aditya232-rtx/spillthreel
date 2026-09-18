"""MemoryStore protocol — the swappable interface to the memory layer.

Implementations: cognee_cloud.py (v1 default), cognee_oss.py (escape hatch).
Namespacing rule: every call MUST scope to `dataset=f"user_{user_id}"`
(TRD §10.2) — implementations enforce this internally.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel


class IndexedItem(BaseModel):
    """The unit written to the memory store — a text corpus + metadata."""

    item_id: str
    text_corpus: str
    metadata: dict[str, str | int | float | list[str] | None]


class SearchFilters(BaseModel):
    platforms: list[str] | None = None
    categories: list[str] | None = None
    collection_ids: list[str] | None = None
    only_fully_indexed: bool = False
    since: datetime | None = None


class MemoryHit(BaseModel):
    item_id: str
    score: float
    matched_chunks: list[str] = []


MemoryTier = Literal["text_only", "multimodal"]


class MemoryStore(Protocol):
    async def write(self, user_id: str, item: IndexedItem) -> str:
        """Returns a memory-layer id (stored on items.cognee_id)."""
        ...

    async def write_batch(self, user_id: str, items: list[IndexedItem]) -> list[str]:
        """Batch write for bulk imports — up to 50 items per call (TRD §14.4)."""
        ...

    async def delete(self, user_id: str, item_id: str) -> None: ...

    async def search(
        self,
        user_id: str,
        query: str,
        filters: SearchFilters,
        top_k: int = 8,
    ) -> list[MemoryHit]: ...

    async def similar(
        self,
        user_id: str,
        item_id: str,
        top_k: int = 8,
    ) -> list[MemoryHit]: ...

    async def delete_namespace(self, user_id: str) -> None:
        """Purge the entire per-user namespace — used by account deletion."""
        ...
