"""URL → platform classifier + URL normalization.

Normalization produces `source_url_norm`, the dedup key on
`items.(user_id, source_url_norm)`:

* Generic rule (all platforms): lowercase host, force https, drop
  query/fragment/trailing slash, strip user-info. The ORIGINAL url
  (query string included) is always kept in `items.source_url` —
  extraction runs off `source_url`, never the norm (see
  services/ingest/pipeline.py `_run` → `extractor_extract(item.source_url)`
  and api/v1/saves.py `source_url=url_str`).
* YouTube: `watch?v=ID`, `youtu.be/ID`, `/shorts/ID` (including
  `m.`/`music.` subdomains) collapse to ONE canonical
  `https://www.youtube.com/watch?v=<id>`. Missing/garbage ids fall back
  to the generic rule (never crash).
* Instagram: `/reel/`, `/reels/`, `/p/`, `/tv/` path shapes are
  PRESERVED — only tracking params are stripped. `/reels/` is NOT
  folded to `/reel/` because nothing in the codebase treats them
  identically (extractors deliberately avoid path sniffing — see
  gallerydl.py; ig_parser only checks the host), so folding would risk
  merging distinct posts.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse, urlunparse

from app.services.extractors.base import Platform


class UnsupportedPlatformError(ValueError):
    pass


_PLATFORM_RULES: tuple[tuple[Platform, re.Pattern[str]], ...] = (
    ("instagram", re.compile(r"(?:^|\.)instagram\.com$", re.IGNORECASE)),
    ("tiktok", re.compile(r"(?:^|\.)tiktok\.com$", re.IGNORECASE)),
    ("youtube", re.compile(r"(?:^|\.)(youtube\.com|youtu\.be)$", re.IGNORECASE)),
    ("x", re.compile(r"(?:^|\.)(x\.com|twitter\.com)$", re.IGNORECASE)),
)

# YouTube video ids are 11-char base64url.
_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}\Z")

_YT_CANON_HOST = "www.youtube.com"


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
      * YouTube plain-video links → `https://www.youtube.com/watch?v=<id>`
      * otherwise: drop query, fragment, and trailing slash from path
      * strip user-info
    """
    try:
        canonical = _youtube_canonical(url)
    except Exception:
        canonical = None
    if canonical is not None:
        return canonical
    return _generic_normalize(url)


def is_shorts_url(original_url: str) -> bool:
    """True iff the ORIGINAL url was a YouTube `/shorts/` link.

    Inspects the pre-normalization form (the shorts path segment is lost
    in the canonical dedup key by design). Never raises — garbage in
    yields False.
    """
    try:
        parsed = urlparse(original_url.strip())
    except Exception:
        return False
    host = parsed.netloc.lower().rsplit("@", 1)[-1].split(":", 1)[0]
    if not re.search(r"(?:^|\.)youtube\.com$", host):
        return False
    segments = [s for s in parsed.path.split("/") if s]
    return len(segments) >= 1 and segments[0] == "shorts"


def _generic_normalize(url: str) -> str:
    """Pre-existing generic rule: kept byte-identical for non-YouTube URLs."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().rsplit("@", 1)[-1]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(("https", host, path, "", "", ""))


def _youtube_canonical(url: str) -> str | None:
    """Canonical `https://www.youtube.com/watch?v=<id>`, or None.

    None means "not a recognizable plain-video YouTube link" — the caller
    falls back to `_generic_normalize`. Covers `youtube.com/watch?v=`,
    `youtu.be/<id>`, `youtube.com/shorts/<id>` on any `*.youtube.com`
    host (`www.`, `m.`, `music.`, …).
    """
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().rsplit("@", 1)[-1].split(":", 1)[0]
    segments = [s for s in parsed.path.split("/") if s]

    video_id: str | None = None
    if re.search(r"(?:^|\.)youtube\.com$", host):
        if len(segments) >= 2 and segments[0] == "shorts":
            video_id = segments[1]
        elif segments[:1] == ["watch"]:
            values = parse_qs(parsed.query).get("v")
            video_id = values[0] if values else None
    elif re.search(r"(?:^|\.)youtu\.be$", host) and segments:
        video_id = segments[0]

    if video_id is None or _YT_ID_RE.match(video_id) is None:
        return None
    return f"https://{_YT_CANON_HOST}/watch?v={video_id}"
