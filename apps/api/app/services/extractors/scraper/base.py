"""Scraper shared types + error taxonomy (Part D).

ScrapedPost is the provider-neutral post view both vendors map into.
Registry + pipeline decisions branch ONLY on the error taxonomy here:

* SourceGoneError(ExtractorError) — deleted/private/not-found. Registry
  must NOT walk past it (re-raise); pipeline maps it to `source_gone`.
* ProviderQuotaError(ExtractorError) — credits exhausted / auth failures
  (HTTP 401/402/403). Logged at error level for Sentry, NEVER treated
  as source_gone; registry falls through to the next extractor.
* Any other ExtractorError — transient (5xx/timeout/parse/empty). The
  registry walks past it to the next extractor.
"""

from __future__ import annotations

import asyncio
import os
from typing import Literal, Protocol

from pydantic import BaseModel

from app.services.extractors.base import ExtractorError, Platform


class ScrapedPost(BaseModel):
    media_type: Literal["video", "image", "carousel"]
    video_url: str | None = None
    image_urls: list[str] = []
    audio_url: str | None = None
    caption: str | None = None
    owner_username: str | None = None
    owner_name: str | None = None
    owner_url: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None


class SourceGoneError(ExtractorError):
    """Post is deleted/private/not-found. Registry re-raises; no fallthrough."""


class ProviderQuotaError(ExtractorError):
    """Provider credits exhausted or auth rejected (401/402/403).

    Error-level log for Sentry. NEVER map to source_gone.
    """


class ScraperProvider(Protocol):
    name: str

    async def fetch(self, platform: Platform, url: str) -> ScrapedPost: ...


# Shared semaphore cap: SCRAPER_MAX_CONCURRENCY (default 4).
_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int = 0


def get_concurrency_limit() -> int:
    raw = os.environ.get("SCRAPER_MAX_CONCURRENCY", "").strip()
    if not raw:
        try:
            from app.settings import get_settings  # deferred: integrator owns field

            value = getattr(get_settings(), "scraper_max_concurrency", None)
            if isinstance(value, (int, float)) and int(value) > 0:
                return int(value)
        except Exception:
            pass
        return 4
    try:
        parsed = int(raw)
    except ValueError:
        return 4
    return parsed if parsed > 0 else 4


def get_scraper_semaphore() -> asyncio.Semaphore:
    global _semaphore, _semaphore_limit
    limit = get_concurrency_limit()
    if _semaphore is None or _semaphore_limit != limit:
        _semaphore = asyncio.Semaphore(limit)
        _semaphore_limit = limit
    return _semaphore


def get_cost_per_1k_usd() -> str:
    raw = os.environ.get("SCRAPER_COST_PER_1K_USD", "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "scraper_cost_per_1k_usd", None)
        if value is None:
            return ""
        return str(value)
    except Exception:
        return ""
