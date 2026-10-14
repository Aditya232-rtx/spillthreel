"""POST /v1/search — RAG flagship over the user's saves.

Contract:
  Request:  { query (1..500 chars), filters?, top_k? (default 8, max 20) }
  Response: { text, cards[], citations[], meta{latency_ms, hits, model, fallback} }
  Errors:   400 empty query · 401 no session · 422 bad filter ·
            503 embeddings/LLM unavailable · 504 LLM timeout

Thin route — validation + auth + rate limit here, everything else in
app/services/search/pipeline.py. user_id comes from the verified JWT,
never the body. The global DefaultRateLimitMiddleware already applies
the default limit; the decorator below adds a stricter per-route
budget since every call burns embeddings + LLM.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth.middleware import CurrentUser, DbSession
from app.observability.logging import get_logger
from app.ratelimit import limiter
from app.services.llm.budget import BudgetExceededError
from app.services.memory.base import SearchFilters
from app.services.search.pipeline import (
    SearchResponse,
    SearchTimeoutError,
    SearchUnavailableError,
    search,
)

router = APIRouter()
_logger = get_logger(__name__)

# Stricter than the 60/minute default: each search burns an embedding
# call plus an LLM call. Chat bursts stay interactive; abuse doesn't.
_SEARCH_BURST_LIMIT = "20/minute"


class SearchFiltersIn(BaseModel):
    platforms: list[str] | None = None
    categories: list[str] | None = None
    collection_ids: list[str] | None = None
    only_fully_indexed: bool = True
    since: datetime | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., max_length=500)
    filters: SearchFiltersIn | None = None
    top_k: int = Field(default=8, ge=1, le=20)


@router.post("/search", response_model=SearchResponse)
@limiter.limit(_SEARCH_BURST_LIMIT)
async def search_saves(
    request: Request,
    body: SearchRequest,
    user: CurrentUser,
    session: DbSession,
) -> SearchResponse:
    query = body.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query must not be empty",
        )

    filters_in = body.filters or SearchFiltersIn()
    filters = SearchFilters(
        platforms=filters_in.platforms,
        categories=filters_in.categories,
        collection_ids=filters_in.collection_ids,
        only_fully_indexed=filters_in.only_fully_indexed,
        since=filters_in.since,
    )

    try:
        return await search(query, filters, user.id, body.top_k, session=session)
    except BudgetExceededError as e:
        _logger.warning("search.budget_exceeded", user_id=user.id, reason=str(e))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="gemini budget exceeded",
        ) from e
    except SearchTimeoutError as e:
        _logger.warning("search.llm_timeout", user_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="answer timed out",
        ) from e
    except SearchUnavailableError as e:
        _logger.warning("search.unavailable", user_id=user.id, reason=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="search unavailable",
        ) from e
