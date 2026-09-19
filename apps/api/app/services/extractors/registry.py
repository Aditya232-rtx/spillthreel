"""Extractor registry — priority-ordered lookup per platform.

Given a URL:
  1. Detect platform (services/ingest/platform_detect.py).
  2. Iterate the registry entries for that platform in priority order.
  3. Return the first successful ExtractResult.
  4. If all fail, raise NoExtractorSucceededError so the pipeline can
     transition the item to `failed` with a debuggable failure_reason.

Per-platform success rates are the metric we watch to reorder this list
if a source starts blocking Cobalt (TRD §17.3 `extractor_success_rate`).
"""

from __future__ import annotations

from app.observability.logging import get_logger
from app.services.extractors.base import (
    Extractor,
    ExtractResult,
    ExtractorError,
    NoExtractorSucceededError,
    Platform,
)
from app.services.extractors.cobalt import CobaltExtractor
from app.services.extractors.ytdlp import YtDlpExtractor
from app.services.ingest.platform_detect import detect_platform

_logger = get_logger(__name__)


def _build_registry() -> dict[Platform, list[Extractor]]:
    """Priority order per platform. First entry is tried first."""
    return {
        "instagram": [CobaltExtractor("instagram"), YtDlpExtractor("instagram")],
        "tiktok":    [CobaltExtractor("tiktok"),    YtDlpExtractor("tiktok")],
        "youtube":   [CobaltExtractor("youtube"),   YtDlpExtractor("youtube")],
        "x":         [CobaltExtractor("x"),         YtDlpExtractor("x")],
    }


# Built once at module import — swap via `_registry = ...` in tests.
_registry: dict[Platform, list[Extractor]] = _build_registry()


async def extract(url: str) -> ExtractResult:
    """Extract media from a URL by walking the platform's registry.

    Raises NoExtractorSucceededError if every extractor for the URL's
    platform failed. Individual extractor errors are logged at warn but
    don't propagate — that's the whole point of the fallback chain.
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

    raise NoExtractorSucceededError(
        f"all extractors failed for {url}: last_error={last_error}"
    )
