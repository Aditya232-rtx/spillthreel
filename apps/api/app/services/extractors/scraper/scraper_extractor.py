"""ScraperApiExtractor — paid-provider head of the registry chain (Part D).

Provider selection comes from ``SCRAPER_PROVIDER`` (``scrapecreators`` |
``apify`` | ``brightdata`` | ``none``; default ``none`` → ``can_handle``
False so the chain falls through to yt-dlp/gallery-dl with zero paid calls).

Media bytes are downloaded via Part C
``services.media.download.download_media`` with the CTO-decreed signature
``async def download_media(url, dest_dir, *, kind="video", max_mb=None,
timeout_s=60.0) -> Path``. The import is resolved lazily and failures to
locate a matching callable raise loudly (RuntimeError) instead of
silently degrading.

Mixed-carousel rule (documented + tested): video wins — when a carousel
carries both a video URL and images, only the video is downloaded and the
result is ``media_type="video"``. Otherwise up to ``_MAX_IMAGES`` images
are downloaded and the result is ``media_type="image"``. ``audio_url``
is intentionally ignored (``audio_path`` stays None like yt-dlp/gallery-dl;
the pipeline derives audio from video via ffmpeg). ``thumbnail_url`` is
ignored (the pipeline mints its own thumbnail).

Handoff matches the pipeline analyze inputs: ``ExtractResult`` with
``video_path`` OR ``image_paths`` plus ``original_caption``,
``original_owner``, ``duration_seconds``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.logging import get_logger
from app.services.extractors.base import (
    ExtractorError,
    ExtractResult,
    Owner,
    Platform,
)
from app.services.extractors.scraper.apify import ApifyProvider
from app.services.extractors.scraper.base import ScrapedPost
from app.services.extractors.scraper.brightdata import BrightDataProvider
from app.services.extractors.scraper.scrapecreators import ScrapeCreatorsProvider

_logger = get_logger(__name__)

_MAX_IMAGES = 10


def _provider_name() -> str:
    raw = os.environ.get("SCRAPER_PROVIDER", "").strip().lower()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "scraper_provider", None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    except Exception:
        pass
    return "none"


def _resolve_download_media() -> Callable[..., Any]:
    try:
        from app.services.media.download import download_media as fn
    except ImportError as e:
        raise RuntimeError(
            "scraper extractor requires services.media.download.download_media "
            "(Part C) with signature "
            "async def download_media(url, dest_dir, *, kind='video', "
            "max_mb=None, timeout_s=60.0) -> Path"
        ) from e
    if not callable(fn):
        raise RuntimeError("download_media resolved but is not callable")
    return fn


class ScraperApiExtractor:
    """Extractor Protocol impl — paid scraper head for IG/TikTok."""

    def __init__(self, platform: Platform) -> None:
        self.platform: Platform = platform

    async def can_handle(self, url: str) -> bool:
        _ = url
        provider = _provider_name()
        if provider == "none":
            return False
        if provider not in ("scrapecreators", "apify", "brightdata"):
            return False
        return self.platform in ("instagram", "tiktok")

    async def extract(self, url: str) -> ExtractResult:
        provider_name = _provider_name()
        if provider_name == "scrapecreators":
            post = await ScrapeCreatorsProvider().fetch(self.platform, url)
        elif provider_name == "apify":
            post = await ApifyProvider().fetch(self.platform, url)
        elif provider_name == "brightdata":
            post = await BrightDataProvider().fetch(self.platform, url)
        else:
            raise ExtractorError(f"scraper provider {provider_name!r} not enabled")
        return await _materialize(self.platform, post)


async def _materialize(platform: Platform, post: ScrapedPost) -> ExtractResult:
    download_media = _resolve_download_media()
    tmp_dir = Path(tempfile.mkdtemp(prefix="spillthereel-scraper-"))
    try:
        owner: Owner | None = None
        if post.owner_username is not None or post.owner_name is not None:
            owner = Owner(
                url=post.owner_url,
                name=post.owner_name,
                username=post.owner_username,
            )
        if post.media_type == "video":
            if not post.video_url:
                raise ExtractorError("scraper post missing video_url")
            video_path = Path(await download_media(post.video_url, tmp_dir, kind="video"))
            _logger.info(
                "extractor.scraper.extracted",
                platform=platform,
                media_type="video",
                caption_chars=len(post.caption) if post.caption else 0,
            )
            return ExtractResult(
                media_type="video",
                video_path=video_path,
                audio_path=None,
                image_paths=[],
                original_caption=post.caption,
                original_owner=owner,
                duration_seconds=post.duration_seconds,
                resolved_at=datetime.now(UTC),
            )
        if post.media_type == "carousel":
            # Video-wins rule: mixed carousels resolve to video only.
            if post.video_url:
                video_path = Path(await download_media(post.video_url, tmp_dir, kind="video"))
                _logger.info(
                    "extractor.scraper.extracted",
                    platform=platform,
                    media_type="video",
                    carousel_rule="video_wins",
                    caption_chars=len(post.caption) if post.caption else 0,
                )
                return ExtractResult(
                    media_type="video",
                    video_path=video_path,
                    audio_path=None,
                    image_paths=[],
                    original_caption=post.caption,
                    original_owner=owner,
                    duration_seconds=post.duration_seconds,
                    resolved_at=datetime.now(UTC),
                )
            urls = post.image_urls[:_MAX_IMAGES]
            if not urls:
                raise ExtractorError("scraper carousel has no downloadable media")
            image_paths = [Path(await download_media(u, tmp_dir, kind="image")) for u in urls]
            _logger.info(
                "extractor.scraper.extracted",
                platform=platform,
                media_type="image",
                carousel_rule="images_up_to_n",
                images=len(image_paths),
                caption_chars=len(post.caption) if post.caption else 0,
            )
            return ExtractResult(
                media_type="image",
                video_path=None,
                audio_path=None,
                image_paths=image_paths,
                original_caption=post.caption,
                original_owner=owner,
                duration_seconds=post.duration_seconds,
                resolved_at=datetime.now(UTC),
            )
        # image
        urls = post.image_urls[:_MAX_IMAGES]
        if not urls:
            raise ExtractorError("scraper image post has no image_urls")
        image_paths = [Path(await download_media(u, tmp_dir, kind="image")) for u in urls]
        _logger.info(
            "extractor.scraper.extracted",
            platform=platform,
            media_type="image",
            images=len(image_paths),
            caption_chars=len(post.caption) if post.caption else 0,
        )
        return ExtractResult(
            media_type="image",
            video_path=None,
            audio_path=None,
            image_paths=image_paths,
            original_caption=post.caption,
            original_owner=owner,
            duration_seconds=post.duration_seconds,
            resolved_at=datetime.now(UTC),
        )
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
