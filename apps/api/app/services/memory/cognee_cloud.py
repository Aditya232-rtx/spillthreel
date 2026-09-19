"""Cognee Cloud MemoryStore — writes + semantic search per user namespace.

Every call scopes to `dataset=f"user_{user_id}"` (TRD §10.2). The
service-role key is our tenant credential; per-user isolation is
enforced by Cognee's namespace boundary, not by policies on our side.

Cognee returns a document id on write which we persist on
`items.cognee_id` so we can delete or update the row without touching
its content again.
"""

from __future__ import annotations

import httpx

from app.observability.logging import get_logger
from app.services.memory.base import (
    IndexedItem,
    MemoryHit,
    SearchFilters,
)
from app.settings import get_settings

_logger = get_logger(__name__)

_TIMEOUT_SECONDS = 60.0


def _namespace(user_id: str) -> str:
    """Cognee's dataset name for a given app user. Kept in one place so
    account-deletion always targets the same slug."""
    return f"user_{user_id}"


class CogneeCloudStore:
    """MemoryStore Protocol impl calling Cognee's hosted API.

    We hit Cognee's REST surface directly (not the Python SDK) — the SDK
    is optimized for notebook use and pulls a lot of transitive deps we
    don't want in the worker image. httpx + a thin wrapper is enough.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._base = settings.cognee_base_url.rstrip("/")
        self._api_key = settings.cognee_api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise RuntimeError("cognee_api_key not configured")
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def write(self, user_id: str, item: IndexedItem) -> str:
        """Cognify one item into the user's namespace.

        Returns Cognee's document id for later delete/update.
        """
        payload = {
            "dataset_name": _namespace(user_id),
            "documents": [
                {
                    "content": item.text_corpus,
                    "metadata": {**item.metadata, "item_id": item.item_id},
                }
            ],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{self._base}/v1/cognify",
                json=payload,
                headers=self._headers(),
            )
        r.raise_for_status()
        body = r.json()
        doc_id = (body.get("documents") or [{}])[0].get("id") or ""
        _logger.info(
            "memory.cognee.wrote",
            user_id=user_id,
            item_id=item.item_id,
            doc_id=doc_id,
        )
        return doc_id

    async def write_batch(
        self, user_id: str, items: list[IndexedItem]
    ) -> list[str]:
        """Batch write for bulk imports (TRD §14.4 — up to 50 per call)."""
        if not items:
            return []
        payload = {
            "dataset_name": _namespace(user_id),
            "documents": [
                {
                    "content": i.text_corpus,
                    "metadata": {**i.metadata, "item_id": i.item_id},
                }
                for i in items
            ],
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{self._base}/v1/cognify",
                json=payload,
                headers=self._headers(),
            )
        r.raise_for_status()
        docs = r.json().get("documents", [])
        return [d.get("id", "") for d in docs]

    async def delete(self, user_id: str, item_id: str) -> None:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.delete(
                f"{self._base}/v1/datasets/{_namespace(user_id)}/documents/{item_id}",
                headers=self._headers(),
            )
        if r.status_code == 404:
            return  # already gone
        r.raise_for_status()

    async def search(
        self,
        user_id: str,
        query: str,
        filters: SearchFilters,
        top_k: int = 8,
    ) -> list[MemoryHit]:
        payload = {
            "dataset_name": _namespace(user_id),
            "query": query,
            "top_k": top_k,
            "filters": filters.model_dump(exclude_none=True),
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{self._base}/v1/search",
                json=payload,
                headers=self._headers(),
            )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [
            MemoryHit(
                item_id=hit["metadata"]["item_id"],
                score=float(hit.get("score", 0.0)),
                matched_chunks=hit.get("chunks", []),
            )
            for hit in results
        ]

    async def similar(
        self,
        user_id: str,
        item_id: str,
        top_k: int = 8,
    ) -> list[MemoryHit]:
        payload = {
            "dataset_name": _namespace(user_id),
            "reference_item_id": item_id,
            "top_k": top_k,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{self._base}/v1/similar",
                json=payload,
                headers=self._headers(),
            )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [
            MemoryHit(
                item_id=hit["metadata"]["item_id"],
                score=float(hit.get("score", 0.0)),
                matched_chunks=hit.get("chunks", []),
            )
            for hit in results
        ]

    async def delete_namespace(self, user_id: str) -> None:
        """Nuke the user's dataset — for account deletion (F5.7)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.delete(
                f"{self._base}/v1/datasets/{_namespace(user_id)}",
                headers=self._headers(),
            )
        if r.status_code == 404:
            return
        r.raise_for_status()
        _logger.info("memory.cognee.namespace_deleted", user_id=user_id)
