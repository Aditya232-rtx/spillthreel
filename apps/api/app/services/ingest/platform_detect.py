"""URL → platform classifier + URL normalization.

Normalization: strip query strings (except platform-critical ones — nothing
in v1), lowercase host, drop trailing slashes. The `source_url_norm` this
produces is the dedup key on `items.(user_id, source_url_norm)`.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

from app.services.extractors.base import Platform


class UnsupportedPlatformError(ValueError):
    pass


_PLATFORM_RULES: tuple[tuple[Platform, re.Pattern[str]], ...] = (
    ("instagram", re.compile(r"(?:^|\.)instagram\.com$", re.IGNORECASE)),
    ("tiktok", re.compile(r"(?:^|\.)tiktok\.com$", re.IGNORECASE)),
    ("youtube", re.compile(r"(?:^|\.)(youtube\.com|youtu\.be)$", re.IGNORECASE)),
    ("x", re.compile(r"(?:^|\.)(x\.com|twitter\.com)$", re.IGNORECASE)),
)


def detect_platform(url: str) -> Platform:
    """Classify a URL to one of the four supported platforms.

    Raises UnsupportedPlatformError so `/v1/saves` can return HTTP 400
    (matching PRD F1.7 — surface "not supported yet" back to the user).
    """
    parsed = urlparse(url)
    if not parsed.netloc:
        raise UnsupportedPlatformError(f"no host in url: {url!r}")

    host = parsed.netloc.lower()
    # strip user:pass@ and :port
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]

    for platform, pattern in _PLATFORM_RULES:
        if pattern.search(host):
            return platform

    raise UnsupportedPlatformError(f"unsupported host: {host}")


def normalize_url(url: str) -> str:
    """Return the canonical form used for dedup.

    Rules:
      * lowercase scheme + host
      * scheme forced to https
      * drop query, fragment, and trailing slash from path
      * strip user-info
    """
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().rsplit("@", 1)[-1]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", "", ""))
