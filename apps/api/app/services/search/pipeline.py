"""RAG search pipeline for POST /v1/search.

Flow: PgVectorStore.search (user-scoped) → score threshold → hydrate
cards from the items table → answerer.generate → citation GUARD →
extractive fallback when the text cites out-of-range [n].

Timeouts: embed 5s + pgvector 3s (the store fuses both steps, so the
retriever budget is their sum: 8s), LLM 12s. Retriever failures map to
SearchUnavailableError (route → 503); LLM timeouts map to
SearchTimeoutError (route → 504).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import get_pg_pool, session_scope
from app.db.models import Item
from app.observability.logging import get_logger
from app.services.llm.answer import (
    NO_MATCH_TEXT,
    AnswerHit,
    AnswerModel,
    GeminiAnswerModel,
)
from app.services.llm.budget import check_search_budget
from app.services.memory.base import MemoryHit, SearchFilters
from app.services.memory.pgvector_store import PgVectorStore

_logger = get_logger(__name__)

# Cosine-similarity cutoff — hits below this never reach the answerer.
# Tunable const: raise if answers feel loose, lower if recall feels thin.
SCORE_THRESHOLD = 0.55

EMBED_TIMEOUT_S = 5.0
PGVECTOR_TIMEOUT_S = 3.0
# PgVectorStore.search embeds + queries in one call, so its budget is the sum.
RETRIEVER_TIMEOUT_S = EMBED_TIMEOUT_S + PGVECTOR_TIMEOUT_S
LLM_TIMEOUT_S = 12.0

_CITATION_REF_RE = re.compile(r"\[(\d+)\]")


class SearchUnavailableError(RuntimeError):
    """Embeddings store or LLM unavailable (route maps to 503)."""


class SearchTimeoutError(TimeoutError):
    """LLM answer timed out (route maps to 504)."""


class SearchCard(BaseModel):
    item_id: str
    title: str
    creator: str | None = None
    platform: str
    thumbnail_url: str | None = None
    deep_link: str
    # Original https source URL (items.source_url) — mobile fallback until
    # the item detail screen lands. deep_link stays the primary opener.
    url: str | None = None
    score: float


class SearchCitation(BaseModel):
    item_id: str
    index: int
    span: str | None = None


class SearchMeta(BaseModel):
    latency_ms: int
    hits: int
    model: str
    fallback: bool = False


class SearchResponse(BaseModel):
    text: str
    cards: list[SearchCard]
    citations: list[SearchCitation]
    meta: SearchMeta


class Retriever(Protocol):
    async def search(
        self, user_id: str, query: str, filters: SearchFilters, top_k: int = 8
    ) -> list[MemoryHit]: ...


class CardFields(BaseModel):
    title: str
    creator: str | None = None
    platform: str
    thumbnail_url: str | None = None
    source_url: str | None = None


class CardLoader(Protocol):
    async def load(self, user_id: str, item_ids: list[str]) -> dict[str, CardFields]: ...


class _SessionCardLoader:
    """Default CardLoader — hydrates cards from the items table (user-scoped)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, user_id: str, item_ids: list[str]) -> dict[str, CardFields]:
        if not item_ids:
            return {}
        stmt = select(Item).where(Item.user_id == user_id, Item.id.in_(item_ids))
        rows = (await self._session.execute(stmt)).scalars().all()
        return {
            row.id: CardFields(
                title=row.title or "Untitled save",
                creator=row.owner_username or row.owner_name,
                platform=row.platform,
                thumbnail_url=row.thumbnail_url,
                source_url=row.source_url,
            )
            for row in rows
        }


def _fallback_text(cards: list[SearchCard]) -> str:
    listed = "; ".join(f"[{i + 1}] {c.title}" for i, c in enumerate(cards))
    return f"Found {len(cards)} saves: {listed}."


async def search(
    query: str,
    filters: SearchFilters,
    user_id: str,
    top_k: int = 8,
    *,
    session: AsyncSession | None = None,
    store: Retriever | None = None,
    answerer: AnswerModel | None = None,
    card_loader: CardLoader | None = None,
) -> SearchResponse:
    """Run the full RAG pipeline for one user query."""
    started = time.perf_counter()
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    active_answerer: AnswerModel = answerer if answerer is not None else GeminiAnswerModel()

    # Cost pre-flight (TRD §14.3 honest minimal): hard per-request cap +
    # estimated-cost log line. BudgetExceededError bubbles to the route (→ 429).
    # True daily cumulative enforcement is deferred to a usage-table
    # migration (see services/llm/budget.py).
    check_search_budget(query, top_k, user_id)

    # 1. Retrieve (user-scoped inside the store) under the embed+pg budget.
    active_store: Retriever = store if store is not None else PgVectorStore(await get_pg_pool())
    try:
        hits = await asyncio.wait_for(
            active_store.search(user_id, query, filters, top_k),
            timeout=RETRIEVER_TIMEOUT_S,
        )
    except TimeoutError as e:
        raise SearchUnavailableError("retriever timed out") from e
    except RuntimeError as e:
        raise SearchUnavailableError(f"retriever unavailable: {e}") from e

    kept = [h for h in hits if h.score >= SCORE_THRESHOLD]
    scores = [h.score for h in kept]
    latency_ms = int((time.perf_counter() - started) * 1000)

    # 2. Zero-hits short-circuit — no LLM call, exact no-match sentence.
    if not kept:
        _logger.info(
            "search.completed",
            query_hash=query_hash,
            top_k=top_k,
            scores=scores,
            threshold=SCORE_THRESHOLD,
            model=active_answerer.model_name,
            latency_ms=latency_ms,
            fallback=False,
            citations_valid=True,
        )
        return SearchResponse(
            text=NO_MATCH_TEXT,
            cards=[],
            citations=[],
            meta=SearchMeta(latency_ms=latency_ms, hits=0, model=active_answerer.model_name),
        )

    # 3. Hydrate cards from the items table, preserving retriever order.
    #    Rows missing here (deleted between index and read) are skipped.
    hit_ids = [h.item_id for h in kept]
    score_by_id = {h.item_id: h.score for h in kept}
    loader: CardLoader
    if card_loader is not None:
        loader = card_loader
        fields = await loader.load(user_id, hit_ids)
    elif session is not None:
        fields = await _SessionCardLoader(session).load(user_id, hit_ids)
    else:
        async with session_scope() as scoped:
            fields = await _SessionCardLoader(scoped).load(user_id, hit_ids)
    cards = [
        SearchCard(
            item_id=item_id,
            title=fields[item_id].title,
            creator=fields[item_id].creator,
            platform=fields[item_id].platform,
            thumbnail_url=fields[item_id].thumbnail_url,
            deep_link=f"app://item/{item_id}",
            url=fields[item_id].source_url,
            score=score_by_id[item_id],
        )
        for item_id in hit_ids
        if item_id in fields
    ]
    if not cards:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _logger.info(
            "search.completed",
            query_hash=query_hash,
            top_k=top_k,
            scores=scores,
            threshold=SCORE_THRESHOLD,
            model=active_answerer.model_name,
            latency_ms=latency_ms,
            fallback=False,
            citations_valid=True,
        )
        return SearchResponse(
            text=NO_MATCH_TEXT,
            cards=[],
            citations=[],
            meta=SearchMeta(
                latency_ms=latency_ms, hits=len(kept), model=active_answerer.model_name
            ),
        )

    # 4. Answer under the LLM budget.
    answer_hits = [
        AnswerHit(
            item_id=item_id,
            index=i + 1,
            title=fields[item_id].title,
            snippet=" ".join(kept[i].matched_chunks[:2]) or None,
        )
        for i, item_id in enumerate(hit_ids)
        if item_id in fields
    ]
    try:
        answer = await asyncio.wait_for(
            active_answerer.generate(query, answer_hits),
            timeout=LLM_TIMEOUT_S,
        )
    except TimeoutError as e:
        raise SearchTimeoutError("answer model timed out") from e
    except (RuntimeError, ValueError) as e:
        raise SearchUnavailableError(f"answer model unavailable: {e}") from e

    # 5. GUARD: drop citations pointing outside the retriever set; fall
    #    back to the extractive template when the text cites out-of-range
    #    [n] (or is empty — nothing grounded to show).
    valid_ids = {c.item_id for c in cards}
    filtered = [
        SearchCitation(item_id=c.item_id, index=c.index, span=c.span)
        for c in answer.citations
        if c.item_id in valid_ids and 1 <= c.index <= len(cards)
    ]
    reffed = {int(m) for m in _CITATION_REF_RE.findall(answer.text)}
    out_of_range = any(n < 1 or n > len(cards) for n in reffed)
    dropped = len(filtered) != len(answer.citations)
    citations_valid = not dropped and not out_of_range

    fallback = out_of_range or not answer.text.strip()
    if fallback:
        text = _fallback_text(cards)
        citations = [SearchCitation(item_id=c.item_id, index=i + 1) for i, c in enumerate(cards)]
    else:
        text = answer.text
        citations = filtered

    latency_ms = int((time.perf_counter() - started) * 1000)
    _logger.info(
        "search.completed",
        query_hash=query_hash,
        top_k=top_k,
        scores=scores,
        threshold=SCORE_THRESHOLD,
        model=active_answerer.model_name,
        latency_ms=latency_ms,
        fallback=fallback,
        citations_valid=citations_valid,
    )
    return SearchResponse(
        text=text,
        cards=cards,
        citations=citations,
        meta=SearchMeta(
            latency_ms=latency_ms,
            hits=len(kept),
            model=active_answerer.model_name,
            fallback=fallback,
        ),
    )


__all__ = [
    "EMBED_TIMEOUT_S",
    "LLM_TIMEOUT_S",
    "PGVECTOR_TIMEOUT_S",
    "RETRIEVER_TIMEOUT_S",
    "SCORE_THRESHOLD",
    "CardFields",
    "CardLoader",
    "Retriever",
    "SearchCard",
    "SearchCitation",
    "SearchMeta",
    "SearchResponse",
    "SearchTimeoutError",
    "SearchUnavailableError",
    "search",
]
