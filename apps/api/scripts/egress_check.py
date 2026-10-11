#!/usr/bin/env python3
"""Part F egress probe: verify outbound network paths per platform.

Usage:
    uv run python scripts/egress_check.py urls.txt [--full] [--provider scrapecreators|apify] [--timeout 90]

Reads a URLs file (one per line, blanks and `#` comments skipped) and, per URL,
detects the platform and probes its egress path:

* instagram / tiktok -> scraper provider `fetch` (--provider override, else
  SCRAPER_PROVIDER env, else `scrapecreators`) + `download_media` of the first
  media URL into a per-URL temp dir (removed afterwards).
* youtube -> metadata-only by default (oEmbed, plus Data API v3 when
  YOUTUBE_API_KEY is set; NO Gemini call). With --full, calls
  `GeminiSummaryModel.summarize_youtube` instead (burns tokens; warns first).
* x -> best-effort yt-dlp metadata-only probe (`skip_download`, short
  timeout). Clearly labeled; never downloads bytes.

Prints a table (`url | platform | provider/extractor | success/error-class |
media bytes | seconds`) plus per-platform counts and an error-class histogram.
Exit 0 on a completed run (per-URL failures are data, not exit codes).
Exit 2 on missing args / unreadable URLs file / missing keys / heavy imports.

No database, no ffmpeg: if any import pulls in sqlalchemy / app.db /
ffmpeg-related / gallery-dl modules the script fails fast with a message.
Secrets are accepted ONLY via env vars and are never printed or logged
(only key *presence* is checked, values never leave `os.environ`).

# ---------------------------------------------------------------------------
# Cloud Shell setup (Python + uv + env keys only):
#   git clone <repo-url> spillthereelproduct
#   cd spillthereelproduct/apps/api
#   uv sync --frozen --extra worker
#   export SCRAPECREATORS_API_KEY=...   # IG/TikTok default provider
#   # ...or: export APIFY_TOKEN=... APIFY_IG_ACTOR=... APIFY_TIKTOK_ACTOR=...
#   export YOUTUBE_API_KEY=...          # optional: enriches YouTube metadata mode
#   export GEMINI_API_KEY=...           # only needed for --full
#   uv run python scripts/egress_check.py urls.txt [--full] [--provider scrapecreators|apify] [--timeout 90]
# ---------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import parse_qs, urlparse

import httpx

if TYPE_CHECKING:
    from app.services.extractors.base import Platform
    from app.services.extractors.scraper.base import ScrapedPost

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDER = "scrapecreators"
_VALID_PROVIDERS = ("scrapecreators", "apify")

_URL_COL_W = 50
_PLAT_COL_W = 10
_EXT_COL_W = 16
_RES_COL_W = 16

_YOUTUBE_OEMBED_URL = "https://www.youtube.com/oembed"
_YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
_YT_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_ISO8601_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?"
    r")?$"
)

_GONE_MARKERS = (
    "not found",
    "not_found",
    "deleted",
    "private",
    "unavailable",
    "no longer",
    "gone",
)
_QUOTA_MARKERS = (
    "quota",
    "credit",
    "unauthorized",
    "unauthenticated",
    "forbidden",
    "api key",
    "apikey",
    "x-api-key",
)

# Modules that must never be loaded by this script (no DB, no ffmpeg,
# no gallery-dl; yt-dlp only lazily inside the X probe, google-genai only
# inside --full — both load AFTER the startup guard, by design).
_BANNED_MODULE_PREFIXES = (
    "app.db",
    "sqlalchemy",
    "asyncpg",
    "app.services.media.ffmpeg",
    "app.services.extractors.ytdlp",
    "app.services.extractors.gallerydl",
    "gallery_dl",
    "yt_dlp",
    "google.genai",
    "google.generativeai",
)


# ---------------------------------------------------------------------------
# Local errors
# ---------------------------------------------------------------------------


class _UsageError(Exception):
    """Args/env misconfiguration -> stderr message + exit 2."""


class YouTubeGoneError(Exception):
    """Video is private/deleted/unlisted/age-gated (terminal, not retryable)."""


class YouTubeError(Exception):
    """Transport/payload failure talking to oEmbed or the Data API."""


# ---------------------------------------------------------------------------
# Small value types
# ---------------------------------------------------------------------------


@dataclass
class _Args:
    urls_file: str
    full: bool
    provider: str | None
    timeout: float


@dataclass
class _Ctx:
    provider: str
    full: bool
    timeout: float


@dataclass
class _Row:
    url: str
    platform: str
    extractor: str
    result: str
    media_bytes: int
    seconds: float


# ---------------------------------------------------------------------------
# CLI / file loading
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> _Args:
    parser = argparse.ArgumentParser(
        prog="egress_check.py",
        description="Probe outbound egress per platform (IG/TikTok scraper+download, "
        "YouTube metadata-only, X yt-dlp metadata probe).",
    )
    parser.add_argument(
        "urls_file", help="Text file with one URL per line (# comments, blanks skipped)."
    )
    parser.add_argument(
        "--full", action="store_true", help="YouTube: call summarize_youtube (burns Gemini tokens)."
    )
    parser.add_argument("--provider", choices=list(_VALID_PROVIDERS), default=None)
    parser.add_argument(
        "--timeout", type=float, default=90.0, help="Per-URL budget in seconds (default 90)."
    )
    parsed = parser.parse_args(argv)
    urls_file: str = parsed.urls_file
    full: bool = parsed.full
    provider: str | None = parsed.provider
    timeout: float = parsed.timeout
    if timeout <= 0:
        parser.error("--timeout must be positive")
    return _Args(urls_file=urls_file, full=full, provider=provider, timeout=timeout)


def _load_urls(path: str) -> list[str]:
    urls: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            urls.append(stripped)
    return urls


# ---------------------------------------------------------------------------
# Import hygiene: app modules + fail-fast heavy-import guard
# ---------------------------------------------------------------------------


def _ensure_app_importable() -> None:
    try:
        import app  # noqa: F401
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _import_app_modules(full: bool) -> None:
    """Eagerly import every app module this script may touch.

    Raises _UsageError when the checkout/env cannot provide them (hint: sync
    the worker extra). Importing here (before the guard below) means any
    transitive heavy import fails fast instead of mid-run.
    """
    try:
        import app.services.extractors.scraper.apify
        import app.services.extractors.scraper.scrapecreators
        import app.services.ingest.platform_detect
        import app.services.media.download

        if full:
            import app.services.llm.gemini  # noqa: F401
    except ImportError as e:
        raise _UsageError(
            f"could not import app modules ({e}); "
            "run from apps/api after `uv sync --frozen --extra worker`"
        ) from e


def _assert_no_heavy_imports(baseline: set[str] | None = None) -> None:
    """Fail if the script's own app imports pulled in heavy modules.

    Compares sys.modules against a baseline snapshot taken before the
    script's app imports (see main()); only modules imported BY the script
    (i.e. absent from the baseline) are flagged. Ambiently-present modules
    — e.g. sqlalchemy/app.db/ffmpeg pre-imported by other test modules
    under full-suite collection — are ignored. A baseline of None falls
    back to checking every loaded module (strictest; standalone use).
    """
    if baseline is None:
        baseline = set()
    offenders = sorted(
        {
            mod
            for mod in sys.modules
            if mod not in baseline
            and any(
                mod == prefix or mod.startswith(prefix + ".") for prefix in _BANNED_MODULE_PREFIXES
            )
        }
    )
    if offenders:
        raise _UsageError(
            "refusing to run: heavy module(s) loaded by imports "
            f"({', '.join(offenders)}); this script must not touch "
            "database/ffmpeg/gallery-dl code paths"
        )


# ---------------------------------------------------------------------------
# Env / key presence (names only — values are never read into locals)
# ---------------------------------------------------------------------------


def _key_present(env_name: str, settings_attr: str) -> bool:
    if os.environ.get(env_name, "").strip():
        return True
    try:
        from app.settings import get_settings

        value = getattr(get_settings(), settings_attr, None)
        return isinstance(value, str) and bool(value.strip())
    except Exception:
        return False


def _resolve_provider(cli: str | None, need_scraper: bool) -> str:
    if cli is not None:
        return cli
    raw = os.environ.get("SCRAPER_PROVIDER", "").strip().lower()
    if not raw:
        try:
            from app.settings import get_settings

            candidate = getattr(get_settings(), "scraper_provider", None)
            if isinstance(candidate, str) and candidate.strip():
                raw = candidate.strip().lower()
        except Exception:
            raw = ""
    if not raw:
        return _DEFAULT_PROVIDER
    if raw not in _VALID_PROVIDERS:
        if need_scraper:
            raise _UsageError(f"SCRAPER_PROVIDER={raw!r} is not scrapecreators|apify")
        return _DEFAULT_PROVIDER
    return raw


def _preflight(platforms: list[str], provider: str, full: bool) -> None:
    need_scraper = any(p in ("instagram", "tiktok") for p in platforms)
    if (
        need_scraper
        and provider == "scrapecreators"
        and not _key_present("SCRAPECREATORS_API_KEY", "scrapecreators_api_key")
    ):
        raise _UsageError(
            "set SCRAPECREATORS_API_KEY or use --provider apify (with APIFY_TOKEN set)"
        )
    if need_scraper and provider == "apify" and not _key_present("APIFY_TOKEN", "apify_token"):
        raise _UsageError(
            "set APIFY_TOKEN (+ APIFY_IG_ACTOR/APIFY_TIKTOK_ACTOR) "
            "or use --provider scrapecreators (with SCRAPECREATORS_API_KEY set)"
        )
    if full and "youtube" in platforms and not _key_present("GEMINI_API_KEY", "gemini_api_key"):
        raise _UsageError("set GEMINI_API_KEY to use --full (YouTube Gemini summarization)")


def _youtube_api_key() -> str | None:
    value = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if value:
        return value
    try:
        from app.settings import get_settings

        raw = getattr(get_settings(), "youtube_api_key", None)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Network seams (each is independently monkeypatchable; no network in tests)
# ---------------------------------------------------------------------------


async def _scrape_post(provider: str, platform: str, url: str) -> ScrapedPost:
    """Run the configured scraper provider fetch (IG/TikTok)."""
    if provider == "scrapecreators":
        from app.services.extractors.scraper.scrapecreators import ScrapeCreatorsProvider

        return await ScrapeCreatorsProvider().fetch(cast("Platform", platform), url)
    from app.services.extractors.scraper.apify import ApifyProvider

    return await ApifyProvider().fetch(cast("Platform", platform), url)


async def _download_media_file(
    media_url: str, dest_dir: Path, kind: Literal["video", "image"], timeout_s: float
) -> Path:
    from app.services.media.download import download_media

    return await download_media(media_url, dest_dir, kind=kind, timeout_s=timeout_s)


def _youtube_video_id(url: str) -> str | None:
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return None
    host = parsed.netloc.lower().rsplit("@", 1)[-1].split(":", 1)[0]
    segments = [s for s in parsed.path.split("/") if s]
    candidate: str | None = None
    if re.search(r"(?:^|\.)youtube\.com$", host):
        values = parse_qs(parsed.query).get("v")
        if values and values[0] and _YT_ID_RE.match(values[0].strip()):
            return values[0].strip()
        if len(segments) >= 2 and segments[0] in ("shorts", "embed", "live", "v"):
            candidate = segments[1]
    elif re.search(r"(?:^|\.)youtu\.be$", host):
        if segments:
            candidate = segments[0]
    if candidate is not None and _YT_ID_RE.match(candidate.strip()):
        return candidate.strip()
    return None


def _parse_iso8601_duration(raw: Any) -> float | None:
    if not isinstance(raw, str) or not raw:
        return None
    match = _ISO8601_DURATION_RE.match(raw.strip())
    if match is None:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    return days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds


async def _youtube_metadata(url: str, timeout_s: float) -> dict[str, Any]:
    """oEmbed (+ Data API v3 when YOUTUBE_API_KEY is set). No Gemini call."""
    video_id = _youtube_video_id(url)
    canonical = f"https://www.youtube.com/watch?v={video_id}" if video_id else url
    http_timeout = min(10.0, timeout_s)
    async with httpx.AsyncClient(timeout=http_timeout) as client:
        try:
            resp = await client.get(
                _YOUTUBE_OEMBED_URL, params={"url": canonical, "format": "json"}
            )
        except httpx.HTTPError as e:
            raise YouTubeError(f"oembed request failed: {type(e).__name__}") from e
        if resp.status_code in (401, 403, 404):
            raise YouTubeGoneError("youtube_not_public")
        if resp.status_code >= 400:
            raise YouTubeError(f"oembed http {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as e:
            raise YouTubeError(f"oembed invalid JSON: {e}") from e
        if not isinstance(data, dict):
            raise YouTubeError("oembed unexpected payload")
        meta: dict[str, Any] = {
            "title": data.get("title") if isinstance(data.get("title"), str) else None,
            "channel": data.get("author_name")
            if isinstance(data.get("author_name"), str)
            else None,
            "channel_url": data.get("author_url")
            if isinstance(data.get("author_url"), str)
            else None,
            "description": None,
            "duration_seconds": None,
            "video_id": video_id,
            "used_data_api": False,
        }
        api_key = _youtube_api_key()
        if api_key and video_id:
            details = await _fetch_data_api(client, video_id, api_key)
            if details is not None:
                meta.update(details)
                meta["used_data_api"] = True
    return meta


async def _fetch_data_api(
    client: httpx.AsyncClient, video_id: str, api_key: str
) -> dict[str, Any] | None:
    """Data API enrichment. None = unreachable/quota -> degrade to oEmbed-only."""
    try:
        resp = await client.get(
            _YOUTUBE_VIDEOS_URL,
            params={"part": "snippet,contentDetails,status", "id": video_id, "key": api_key},
        )
    except httpx.HTTPError:
        return None
    if resp.status_code >= 400:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    items = data.get("items", []) if isinstance(data, dict) else []
    if not items:
        raise YouTubeGoneError("youtube_not_public")
    video = items[0] if isinstance(items[0], dict) else {}
    status = video.get("status", {})
    if not isinstance(status, dict):
        status = {}
    if status.get("privacyStatus") != "public" or status.get("uploadStatus") in (
        "deleted",
        "failed",
        "rejected",
    ):
        raise YouTubeGoneError("youtube_not_public")
    content = video.get("contentDetails", {})
    if not isinstance(content, dict):
        content = {}
    rating = content.get("contentRating", {})
    if isinstance(rating, dict) and rating.get("ytRating") == "ytAgeRestricted":
        raise YouTubeGoneError("youtube_not_public")
    snippet = video.get("snippet", {})
    if not isinstance(snippet, dict):
        snippet = {}
    channel_id = snippet.get("channelId")
    return {
        "title": snippet.get("title") if isinstance(snippet.get("title"), str) else None,
        "channel": snippet.get("channelTitle")
        if isinstance(snippet.get("channelTitle"), str)
        else None,
        "channel_url": f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
        "description": snippet.get("description")
        if isinstance(snippet.get("description"), str)
        else None,
        "duration_seconds": _parse_iso8601_duration(content.get("duration")),
    }


async def _youtube_full(url: str, meta: dict[str, Any], timeout_s: float) -> None:
    """Gemini native YouTube summarization (paid). Timeout enforced by caller."""
    _ = timeout_s
    from app.services.llm.gemini import GeminiSummaryModel

    model = GeminiSummaryModel()
    duration = meta.get("duration_seconds")
    await model.summarize_youtube(
        url,
        title=meta.get("title"),
        channel=meta.get("channel"),
        description=meta.get("description"),
        duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
    )


def _x_probe_sync(url: str) -> dict[str, Any]:
    """yt-dlp metadata-only probe (skip_download). Import is lazy on purpose."""
    try:
        from app.services.extractors.base import ExtractorError
    except ImportError as e:
        raise RuntimeError("app modules not importable for X probe") from e
    import yt_dlp

    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        raise ExtractorError(f"yt-dlp probe failed: {type(e).__name__}") from e
    if not isinstance(info, dict):
        raise ExtractorError("yt-dlp probe returned no info")
    title = info.get("title")
    return {
        "title": title if isinstance(title, str) else None,
        "uploader": info.get("uploader"),
        "duration_seconds": info.get("duration"),
    }


async def _x_probe(url: str, timeout_s: float) -> dict[str, Any]:
    _ = timeout_s  # budget enforced by the caller's wait_for + socket_timeout
    return await asyncio.to_thread(_x_probe_sync, url)


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------


def _first_media(post: ScrapedPost) -> tuple[str, Literal["video", "image"]]:
    if post.media_type == "video":
        if not post.video_url:
            raise _missing_media_error()
        return post.video_url, "video"
    if post.media_type == "carousel":
        if post.video_url:
            return post.video_url, "video"
        if post.image_urls:
            return post.image_urls[0], "image"
        raise _missing_media_error()
    if post.image_urls:
        return post.image_urls[0], "image"
    raise _missing_media_error()


def _missing_media_error() -> Exception:
    from app.services.extractors.base import ExtractorError

    return ExtractorError("scraper post has no downloadable media")


def _classify_error(exc: BaseException) -> str:
    if isinstance(exc, YouTubeGoneError):
        return "source_gone"
    if isinstance(exc, YouTubeError):
        lowered = str(exc).lower()
        if any(m in lowered for m in _GONE_MARKERS):
            return "source_gone"
        return "transient_error"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ImportError):
        return "missing_dep"
    if isinstance(exc, RuntimeError) and "not configured" in str(exc):
        return "missing_key"
    try:
        from app.services.extractors.base import ExtractorError
        from app.services.extractors.scraper.base import ProviderQuotaError, SourceGoneError
    except ImportError:
        return "unexpected_error"
    if isinstance(exc, SourceGoneError):
        return "source_gone"
    if isinstance(exc, ProviderQuotaError):
        return "provider_quota"
    if isinstance(exc, ExtractorError):
        lowered = str(exc).lower()
        if any(m in lowered for m in _QUOTA_MARKERS):
            return "provider_quota"
        if any(m in lowered for m in _GONE_MARKERS):
            return "source_gone"
        if "actor not configured" in lowered or "not enabled" in lowered:
            return "config_error"
        if "no downloadable media" in lowered or "no media" in lowered or "no playable" in lowered:
            return "no_media"
        return "transient_error"
    return "unexpected_error"


def _extractor_label(platform: str, ctx: _Ctx) -> str:
    if platform in ("instagram", "tiktok"):
        return ctx.provider
    if platform == "youtube":
        return "gemini-full" if ctx.full else "oembed"
    if platform == "x":
        return "yt-dlp(probe)"
    return "-"


async def _do_probe(url: str, platform: str, ctx: _Ctx) -> tuple[str, str, int]:
    """Returns (result, extractor_label, media_bytes). Raises on failure."""
    if platform in ("instagram", "tiktok"):
        post = await _scrape_post(ctx.provider, platform, url)
        media_url, kind = _first_media(post)
        tmp_dir = Path(tempfile.mkdtemp(prefix="egress-check-"))
        try:
            path = await _download_media_file(media_url, tmp_dir, kind, ctx.timeout)
            size = path.stat().st_size
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        return "ok", ctx.provider, size
    if platform == "youtube":
        meta = await _youtube_metadata(url, ctx.timeout)
        label = "oembed+dataapi" if meta.get("used_data_api") else "oembed"
        if ctx.full:
            await _youtube_full(url, meta, ctx.timeout)
            label = "gemini-full"
        return "ok", label, 0
    if platform == "x":
        await _x_probe(url, min(ctx.timeout, 30.0))
        return "ok", "yt-dlp(probe)", 0
    raise YouTubeError(f"no probe defined for platform {platform!r}")


async def _probe(url: str, platform: str, ctx: _Ctx) -> _Row:
    started = time.monotonic()
    label = _extractor_label(platform, ctx)
    try:
        result, label, nbytes = await asyncio.wait_for(_do_probe(url, platform, ctx), ctx.timeout)
    except TimeoutError:
        return _Row(
            url=url,
            platform=platform,
            extractor=label,
            result="timeout",
            media_bytes=0,
            seconds=time.monotonic() - started,
        )
    except BaseException as e:  # per-URL failures are data, not crashes
        return _Row(
            url=url,
            platform=platform,
            extractor=label,
            result=_classify_error(e),
            media_bytes=0,
            seconds=time.monotonic() - started,
        )
    return _Row(
        url=url,
        platform=platform,
        extractor=label,
        result=result,
        media_bytes=nbytes,
        seconds=time.monotonic() - started,
    )


def _detect_platform(url: str) -> str:
    from app.services.ingest.platform_detect import UnsupportedPlatformError, detect_platform

    try:
        return str(detect_platform(url))
    except UnsupportedPlatformError:
        return "unsupported"
    except Exception:
        return "unsupported"


async def _run_all(items: list[tuple[str, str]], ctx: _Ctx) -> list[_Row]:
    rows: list[_Row] = []
    for url, platform in items:
        if platform == "unsupported":
            rows.append(
                _Row(
                    url=url,
                    platform=platform,
                    extractor="-",
                    result="unsupported_platform",
                    media_bytes=0,
                    seconds=0.0,
                )
            )
            continue
        rows.append(await _probe(url, platform, ctx))
    return rows


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _shorten(url: str, width: int) -> str:
    if len(url) <= width:
        return url
    return url[: max(0, width - 3)] + "..."


def _print_table(rows: list[_Row]) -> None:
    print(
        f"{'url':<{_URL_COL_W}} {'platform':<{_PLAT_COL_W}} "
        f"{'extractor':<{_EXT_COL_W}} {'result':<{_RES_COL_W}} "
        f"{'bytes':>10} {'secs':>7}"
    )
    for row in rows:
        print(
            f"{_shorten(row.url, _URL_COL_W):<{_URL_COL_W}} "
            f"{row.platform:<{_PLAT_COL_W}} "
            f"{row.extractor:<{_EXT_COL_W}} {row.result:<{_RES_COL_W}} "
            f"{row.media_bytes:>10} {row.seconds:>7.1f}"
        )


def _print_summary(rows: list[_Row]) -> None:
    platform_counts: dict[str, int] = {}
    platform_order: list[str] = []
    result_counts: dict[str, int] = {}
    for row in rows:
        if row.platform not in platform_counts:
            platform_counts[row.platform] = 0
            platform_order.append(row.platform)
        platform_counts[row.platform] += 1
        result_counts[row.result] = result_counts.get(row.result, 0) + 1
    print("")
    print("platforms: " + " ".join(f"{p}={platform_counts[p]}" for p in platform_order))
    ordered_results = ["ok", *sorted(c for c in result_counts if c != "ok")]
    print(
        "results: "
        + " ".join(f"{c}={result_counts[c]}" for c in ordered_results if c in result_counts)
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        urls = _load_urls(args.urls_file)
    except OSError as e:
        print(f"egress_check: cannot read urls file {args.urls_file!r}: {e}", file=sys.stderr)
        return 2
    if not urls:
        print(f"egress_check: no URLs in {args.urls_file!r}", file=sys.stderr)
        return 2

    _ensure_app_importable()
    try:
        baseline = set(sys.modules)
        _import_app_modules(args.full)
        _assert_no_heavy_imports(baseline)
    except _UsageError as e:
        print(f"egress_check: {e}", file=sys.stderr)
        return 2

    items: list[tuple[str, str]] = [(url, _detect_platform(url)) for url in urls]
    platforms = [platform for _, platform in items]
    try:
        provider = _resolve_provider(
            args.provider, any(p in ("instagram", "tiktok") for p in platforms)
        )
        _preflight(platforms, provider, args.full)
    except _UsageError as e:
        print(f"egress_check: {e}", file=sys.stderr)
        return 2

    if args.full and "youtube" in platforms:
        print(
            "egress_check: --full enabled: calling summarize_youtube for YouTube URLs "
            "(burns Gemini tokens)",
            file=sys.stderr,
        )

    ctx = _Ctx(provider=provider, full=args.full, timeout=args.timeout)
    rows = asyncio.run(_run_all(items, ctx))
    _print_table(rows)
    _print_summary(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
