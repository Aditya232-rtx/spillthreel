"""RAG search pipeline for POST /v1/search.

Flow: PgVectorStore.search (user-scoped) → score threshold → hydrate
cards from the items table → answerer.generate → citation GUARD →
extractive fallback when the text cites out-of-range [n].

Timeouts: embed 5s + pgvector 3s (the store fuses both steps, so the
retriever budget is their sum: 8s), LLM 12s, and a 20s total pipeline
deadline covering hydration and overhead. The mobile search request uses a
25s deadline to allow for auth/dependency and transport overhead.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from typing import Protocol

from pydantic import BaseModel, Field, field_validator
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
from app.services.media.supabase_storage import canonicalize_signed_url

_logger = get_logger(__name__)

# Cosine-similarity cutoff — hits below this never reach the answerer.
# Tunable const: raise if answers feel loose, lower if recall feels thin.
SCORE_THRESHOLD = 0.55

EMBED_TIMEOUT_S = 5.0
PGVECTOR_TIMEOUT_S = 3.0
# PgVectorStore.search embeds + queries in one call, so its budget is the sum.
RETRIEVER_TIMEOUT_S = EMBED_TIMEOUT_S + PGVECTOR_TIMEOUT_S
LLM_TIMEOUT_S = 12.0
# Bounds retrieval + hydration + answer generation as one request. The mobile
# caller has a slightly longer deadline for auth/dependency/transport overhead.
SEARCH_TOTAL_TIMEOUT_S = 20.0

_CITATION_REF_RE = re.compile(r"\[(\d+)\]")
_WORD_RE = re.compile(r"[a-z0-9]+")
_QUERY_STOP_WORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "did",
        "do",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "what",
        "when",
        "where",
        "which",
        "who",
        "with",
        "you",
        "your",
    }
)


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

    @field_validator("thumbnail_url")
    @classmethod
    def fix_legacy_storage_url(cls, value: str | None) -> str | None:
        return canonicalize_signed_url(value)


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
    summary: str | None = None
    transcript: str | None = None
    on_screen_text: str | None = None
    caption: str | None = None
    hashtags: list[str] = Field(default_factory=list)

    @field_validator("thumbnail_url")
    @classmethod
    def fix_legacy_storage_url(cls, value: str | None) -> str | None:
        return canonicalize_signed_url(value)


# Bounds hydrated evidence characters only. JSON/prompt framing and model-token
# usage are separate and are not implied by this character limit.
_MAX_EVIDENCE_CHARS = 12_000
_MAX_EVIDENCE_FIELD_CHARS = 2_400
_MAX_CARD_TITLE_CHARS = 300


class CardLoader(Protocol):
    async def load(self, user_id: str, item_ids: list[str]) -> dict[str, CardFields]: ...


class _SessionCardLoader:
    """Default CardLoader — hydrates cards from the items table (user-scoped)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load(self, user_id: str, item_ids: list[str]) -> dict[str, CardFields]:
        if not item_ids:
            return {}
        stmt = select(Item).where(
            Item.user_id == user_id,
            Item.id.in_(item_ids),
            Item.state.in_(("fully_indexed", "text_indexed")),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {
            row.id: CardFields(
                title=row.title or "",
                creator=row.owner_username or row.owner_name,
                platform=row.platform,
                thumbnail_url=row.thumbnail_url,
                source_url=row.source_url,
                summary=row.summary,
                transcript=row.transcript,
                on_screen_text=row.on_screen_text,
                caption=row.caption,
                hashtags=row.hashtags or [],
            )
            for row in rows
        }


def _fallback_text(cards: list[SearchCard], hits: list[AnswerHit]) -> str:
    evidence_by_id = {
        hit.item_id: next(
            (
                value
                for value in (
                    hit.summary,
                    hit.transcript,
                    hit.on_screen_text,
                    hit.caption,
                )
                if value
            ),
            None,
        )
        for hit in hits
    }
    listed_parts = []
    for index, card in enumerate(cards, start=1):
        excerpt = evidence_by_id.get(card.item_id)
        listed_parts.append(f"[{index}] {card.title}" + (f" — {excerpt[:240]}" if excerpt else ""))
    listed = "; ".join(listed_parts)
    return f"Found {len(cards)} saves: {listed}."


def _bounded(value: str | None, limit: int = _MAX_EVIDENCE_FIELD_CHARS) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized[:limit] or None


def _grounded_span(span: str | None, hit: AnswerHit) -> str | None:
    """Keep an optional quote only when it occurs in this hit's evidence."""
    bounded = _bounded(span, 240)
    if bounded is None:
        return None
    needle = bounded.casefold()
    evidence = (
        hit.title,
        hit.summary,
        hit.transcript,
        hit.on_screen_text,
        hit.caption,
        *hit.hashtags,
    )
    return bounded if any(needle in (text or "").casefold() for text in evidence) else None


def _one_edit_apart(left: str, right: str) -> bool:
    """Return whether two tokens differ by one insertion, deletion, or substitution."""
    if abs(len(left) - len(right)) > 1 or left == right:
        return False
    left_index = right_index = edits = 0
    while left_index < len(left) and right_index < len(right):
        if left[left_index] == right[right_index]:
            left_index += 1
            right_index += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(left) > len(right):
            left_index += 1
        elif len(right) > len(left):
            right_index += 1
        else:
            left_index += 1
            right_index += 1
    edits += (len(left) - left_index) + (len(right) - right_index)
    return edits == 1


def _query_term_matches(query_term: str, evidence_terms: set[str]) -> bool:
    if query_term in evidence_terms:
        return True
    # A one-character typo such as “paste” for “pasta” should not outweigh a
    # second, exact topical anchor such as “recipe”. Keep fuzzy matching local
    # to query terms and short item evidence rather than adding another model call.
    if len(query_term) < 4:
        return False
    return any(
        abs(len(query_term) - len(term)) <= 1 and _one_edit_apart(query_term, term)
        for term in evidence_terms
    )


def _filter_weak_lexical_matches(
    query: str, ordered: list[tuple[MemoryHit, CardFields]]
) -> list[tuple[MemoryHit, CardFields]]:
    """Use query terms to break close vector ties when one hit is anchored.

    Vector search remains the primary recall mechanism. When a multi-term query
    has at least two lexical anchors in one candidate, discard only near-tied
    candidates matching less than two thirds of that best anchor count. Lower
    scoring candidates and semantic-only queries remain untouched. For now,
    multilingual queries/evidence bypass this English-oriented lexical check.
    """
    if any(ord(char) > 127 and char.isalnum() for char in query):
        return ordered
    query_terms = {
        term
        for term in _WORD_RE.findall(query.casefold())
        if len(term) >= 3 and term not in _QUERY_STOP_WORDS
    }
    if len(query_terms) < 2 or len(ordered) < 2:
        return ordered

    counts: list[int] = []
    for _, fields in ordered:
        evidence = " ".join(
            part
            for part in (
                fields.title,
                fields.summary,
                fields.transcript,
                fields.on_screen_text,
                fields.caption,
                *fields.hashtags,
            )
            if part
        )
        if any(ord(char) > 127 and char.isalnum() for char in evidence):
            return ordered
        evidence_terms = set(_WORD_RE.findall(evidence.casefold()))
        counts.append(sum(_query_term_matches(term, evidence_terms) for term in query_terms))

    best_count = max(counts, default=0)
    if best_count < 2:
        return ordered
    minimum_matches = max(2, math.ceil(best_count * (2 / 3)))
    best_vector_score = max(hit.score for hit, _ in ordered)
    return [
        candidate
        for candidate, match_count in zip(ordered, counts, strict=True)
        if match_count >= minimum_matches or candidate[0].score < best_vector_score - 0.05
    ]


def _build_answer_hits(
    ordered: list[tuple[MemoryHit, CardFields]],
) -> list[AnswerHit]:
    """Build a bounded evidence set while preserving the final card order."""
    result: list[AnswerHit] = []
    budget = _MAX_EVIDENCE_CHARS
    for index, (hit, fields) in enumerate(ordered, start=1):
        values = {
            "title": _bounded(fields.title, _MAX_CARD_TITLE_CHARS),
            "summary": _bounded(fields.summary),
            "transcript": _bounded(fields.transcript),
            "on_screen_text": _bounded(fields.on_screen_text),
            "caption": _bounded(fields.caption),
        }
        tags: list[str] = []
        for tag in fields.hashtags[:30]:
            bounded_tag = _bounded(tag, 120)
            if bounded_tag is not None:
                tags.append(bounded_tag)
        cost = 0
        for value in values.values():
            if value is not None:
                cost += len(value)
        for tag in tags:
            cost += len(tag)
        if cost > budget:
            # Keep title first, then use the remaining budget in source-field order.
            remaining = budget
            for key in ("title", "summary", "transcript", "on_screen_text", "caption"):
                value = values[key]
                if value:
                    trimmed = value[:remaining]
                    values[key] = trimmed or None
                    remaining -= len(trimmed)
            bounded_tags: list[str] = []
            for tag in tags:
                if remaining <= 0:
                    break
                bounded_tag = tag[:remaining]
                if bounded_tag:
                    bounded_tags.append(bounded_tag)
                    remaining -= len(bounded_tag)
            tags = bounded_tags
            budget = 0
        else:
            budget -= cost
        result.append(
            AnswerHit(
                item_id=hit.item_id,
                index=index,
                title=values["title"],
                summary=values["summary"],
                transcript=values["transcript"],
                on_screen_text=values["on_screen_text"],
                caption=values["caption"],
                hashtags=tags,
            )
        )
    return result


async def _search_impl(
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

    # Preserve retrieval order and the highest-ranked occurrence of each item.
    kept: list[MemoryHit] = []
    seen_ids: set[str] = set()
    for hit in hits:
        if hit.score >= SCORE_THRESHOLD and hit.item_id not in seen_ids:
            kept.append(hit)
            seen_ids.add(hit.item_id)
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
    loader: CardLoader
    if card_loader is not None:
        loader = card_loader
        fields = await loader.load(user_id, hit_ids)
    elif session is not None:
        fields = await _SessionCardLoader(session).load(user_id, hit_ids)
    else:
        async with session_scope() as scoped:
            fields = await _SessionCardLoader(scoped).load(user_id, hit_ids)
    ordered = [(hit, fields[hit.item_id]) for hit in kept if hit.item_id in fields]
    unfiltered_count = len(ordered)
    ordered = _filter_weak_lexical_matches(query, ordered)
    if len(ordered) < unfiltered_count:
        _logger.info(
            "search.relevance_filter",
            query_hash=query_hash,
            candidates_before=unfiltered_count,
            candidates_after=len(ordered),
        )
    # This one hydrated order owns every public/evidence index downstream.
    cards = [
        SearchCard(
            item_id=hit.item_id,
            title=_bounded(fields_for_hit.title, _MAX_CARD_TITLE_CHARS) or "Untitled save",
            creator=_bounded(fields_for_hit.creator, 300),
            platform=fields_for_hit.platform,
            thumbnail_url=fields_for_hit.thumbnail_url,
            deep_link=f"app://item/{hit.item_id}",
            url=fields_for_hit.source_url,
            score=hit.score,
        )
        for hit, fields_for_hit in ordered
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
            meta=SearchMeta(latency_ms=latency_ms, hits=0, model=active_answerer.model_name),
        )

    # 4. Answer under the LLM budget.
    answer_hits = _build_answer_hits(ordered)
    has_evidence = any(
        hit.title
        or hit.summary
        or hit.transcript
        or hit.on_screen_text
        or hit.caption
        or hit.hashtags
        for hit in answer_hits
    )
    if not has_evidence:
        citations = [
            SearchCitation(item_id=card.item_id, index=index)
            for index, card in enumerate(cards, start=1)
        ]
        return SearchResponse(
            text=_fallback_text(cards, answer_hits),
            cards=cards,
            citations=citations,
            meta=SearchMeta(
                latency_ms=int((time.perf_counter() - started) * 1000),
                hits=len(cards),
                model=active_answerer.model_name,
                fallback=True,
            ),
        )
    try:
        answer = await asyncio.wait_for(
            active_answerer.generate(query, answer_hits),
            timeout=LLM_TIMEOUT_S,
        )
    except TimeoutError as e:
        raise SearchTimeoutError("answer model timed out") from e
    except (RuntimeError, ValueError) as e:
        raise SearchUnavailableError(f"answer model unavailable: {e}") from e

    # 5. Text markers and structured citations must describe the same exact
    #    index→ID map. Any malformed grounding discards all model prose.
    index_to_id = {index: card.item_id for index, card in enumerate(cards, start=1)}
    markers = [int(marker) for marker in _CITATION_REF_RE.findall(answer.text)]
    citation_pairs = [(citation.index, citation.item_id) for citation in answer.citations]
    expected_pairs = {(index, index_to_id[index]) for index in markers if index in index_to_id}
    citations_valid = (
        bool(answer.text.strip())
        and bool(markers)
        and all(index in index_to_id for index in markers)
        and len(citation_pairs) == len(set(citation_pairs))
        and all(index_to_id.get(index) == item_id for index, item_id in citation_pairs)
        and set(citation_pairs) == expected_pairs
    )

    fallback = not citations_valid
    if fallback:
        text = _fallback_text(cards, answer_hits)
        citations = [SearchCitation(item_id=c.item_id, index=i + 1) for i, c in enumerate(cards)]
    else:
        text = answer.text
        citations = [
            SearchCitation(
                item_id=item_id,
                index=index,
                span=_grounded_span(citation.span, answer_hits[index - 1]),
            )
            for citation, (index, item_id) in zip(answer.citations, citation_pairs, strict=True)
        ]

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
            hits=len(cards),
            model=active_answerer.model_name,
            fallback=fallback,
        ),
    )


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
    """Run the complete retrieval, hydration, and answer path under one deadline."""
    try:
        return await asyncio.wait_for(
            _search_impl(
                query,
                filters,
                user_id,
                top_k,
                session=session,
                store=store,
                answerer=answerer,
                card_loader=card_loader,
            ),
            timeout=SEARCH_TOTAL_TIMEOUT_S,
        )
    except TimeoutError as exc:
        if isinstance(exc, SearchTimeoutError):
            raise
        raise SearchTimeoutError("search deadline exceeded") from exc


__all__ = [
    "EMBED_TIMEOUT_S",
    "LLM_TIMEOUT_S",
    "PGVECTOR_TIMEOUT_S",
    "RETRIEVER_TIMEOUT_S",
    "SEARCH_TOTAL_TIMEOUT_S",
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
