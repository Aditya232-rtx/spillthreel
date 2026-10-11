"""Extractor registry — priority-ordered lookup per platform.

Given a URL:
  1. Detect platform (services/ingest/platform_detect.py).
  2. Iterate the registry entries for that platform in priority order.
  3. Return the first successful ExtractResult.
  4. If all fail, raise NoExtractorSucceededError so the pipeline can
     transition the item to `failed` (or `source_gone` for
     SourceGoneError) with a debuggable failure_reason.

Chain order (Part D):
  * instagram: ScraperApiExtractor → YtDlpExtractor → GalleryDlExtractor
  * tiktok: ScraperApiExtractor → YtDlpExtractor
  * x: YtDlpExtractor → GalleryDlExtractor
  * youtube: YtDlpExtractor only (Part E owns YouTube; kept minimal here
    so the registry imports without the retired provider).

Error taxonomy:
  * SourceGoneError is NEVER walked past — it re-raises immediately so
    deleted/private/not-found posts map to `source_gone`, not `failed`.
  * ProviderQuotaError (credits/auth) logs at error level for Sentry and
    falls through to the next extractor — it is NEVER treated as gone.
  * Any other ExtractorError logs at warning and falls through.
"""

from __future__ import annotations

from app.observability.logging import get_logger
from app.services.extractors.base import (
    Extractor,
    ExtractorError,
    ExtractResult,
    NoExtractorSucceededError,
    Platform,
)
from app.services.extractors.gallerydl import GalleryDlExtractor
from app.services.extractors.scraper.base import ProviderQuotaError, SourceGoneError
from app.services.extractors.scraper.scraper_extractor import ScraperApiExtractor
from app.services.extractors.ytdlp import YtDlpExtractor
from app.services.ingest.platform_detect import detect_platform

_logger = get_logger(__name__)


def _build_registry() -> dict[Platform, list[Extractor]]:
    """Priority order per platform. First entry is tried first.

    The paid scraper head sits FIRST on instagram + tiktok and degrades
    to no-op (can_handle False) when SCRAPER_PROVIDER is `none`, so the
    free paths carry the load with zero paid calls. gallery-dl sits LAST
    on the image-post platforms (instagram, x): a video URL that an
    upstream extractor can handle never reaches it. TikTok/YouTube are
    video-only in v1, so no image fallback.
    """
    return {
        "instagram": [
            ScraperApiExtractor("instagram"),
            YtDlpExtractor("instagram"),
            GalleryDlExtractor("instagram"),
        ],
        "tiktok": [ScraperApiExtractor("tiktok"), YtDlpExtractor("tiktok")],
        "youtube": [YtDlpExtractor("youtube")],
        "x": [YtDlpExtractor("x"), GalleryDlExtractor("x")],
    }


# Built once at module import — swap via `_registry = ...` in tests.
_registry: dict[Platform, list[Extractor]] = _build_registry()


async def extract(url: str) -> ExtractResult:
    """Extract media from a URL by walking the platform's registry.

    Raises NoExtractorSucceededError if every extractor for the URL's
    platform failed. SourceGoneError re-raises immediately (no
    fallthrough); other ExtractorErrors fall through to the next
    extractor.
    """
    platform = detect_platform(url)
    candidates = _registry.get(platform, [])
    if not candidates:
        raise NoExtractorSucceededError(f"no extractors registered for {platform}")

    last_error: Exception | None = None
    for extractor in candidates:
        try:
            if not await extractor.can_handle(url):
                continue
            return await extractor.extract(url)
        except SourceGoneError as e:
            _logger.info(
                "extractor.source_gone",
                extractor=extractor.__class__.__name__,
                platform=platform,
                url=url,
                err=str(e),
            )
            raise
        except ProviderQuotaError as e:
            _logger.error(
                "extractor.quota",
                extractor=extractor.__class__.__name__,
                platform=platform,
                url=url,
                err=str(e),
            )
            last_error = e
            continue
        except ExtractorError as e:
            _logger.warning(
                "extractor.failed",
                extractor=extractor.__class__.__name__,
                platform=platform,
                url=url,
                err=str(e),
            )
            last_error = e
            continue

    raise NoExtractorSucceededError(f"all extractors failed for {url}: last_error={last_error}")
