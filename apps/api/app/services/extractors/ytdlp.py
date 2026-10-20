"""yt-dlp extractor — primary video path for IG Reels, TikTok, YT Shorts, X.

Invoked in-process via yt-dlp's Python API (not subprocess) — a shell
fork per save adds ~50ms and yt-dlp is happy to be embedded. Cookies
default to none; per-platform cookie files can be added via Secret
Manager later (TRD §12.2).

yt-dlp lands metadata (uploader, title, description, duration) as a
side-effect of the download — we capture what we can into ExtractResult
so downstream stages get uploader, duration, and caption alongside media.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.observability.logging import get_logger
from app.services.extractors.base import (
    ExtractorError,
    ExtractResult,
    Owner,
    Platform,
)
from app.services.ingest.platform_detect import UnsupportedPlatformError, detect_platform

_logger = get_logger(__name__)

# yt-dlp is a heavy import (~200ms cold); defer it so `import app` on the
# api container doesn't pay the tax (only the worker container will hit
# this path).
_yt_dlp: Any = None


def _lazy_import_ytdlp() -> Any:
    global _yt_dlp
    if _yt_dlp is None:
        import yt_dlp

        _yt_dlp = yt_dlp
    return _yt_dlp


def _extractor_proxy() -> str | None:
    """Controlled extractor egress proxy URL when configured, else None.

    Env ``EXTRACTOR_EGRESS_PROXY_URL`` first, then settings
    ``extractor_egress_proxy_url``. This is intentionally separate from
    ``OUTBOUND_PROXY_URL`` which configures paid provider API calls only.
    The value is only passed to yt-dlp's ``proxy`` option — never logged
    (it may embed `user:pass@` credentials).
    """
    raw = os.environ.get("EXTRACTOR_EGRESS_PROXY_URL", "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings

        value = getattr(get_settings(), "extractor_egress_proxy_url", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


def _validate_proxy(proxy: str) -> str:
    """Reject malformed proxy values instead of relying on downloader fallback.

    A proxy URL is not itself proof of an egress policy; deployment must
    configure a policy-enforcing proxy and deny direct worker egress. This
    check only ensures the option is an explicit HTTP(S) proxy endpoint.
    """
    try:
        parsed = urlsplit(proxy)
        port = parsed.port
    except ValueError:
        raise ExtractorError("yt-dlp extractor proxy configuration is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(ch.isspace() for ch in proxy)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ExtractorError("yt-dlp extractor proxy configuration is invalid")
    return proxy


class YtDlpExtractor:
    """Extractor Protocol impl."""

    def __init__(self, platform: Platform) -> None:
        self.platform: Platform = platform

    async def can_handle(self, url: str) -> bool:
        try:
            return detect_platform(url) == self.platform
        except (UnsupportedPlatformError, ValueError):
            return False

    async def extract(self, url: str) -> ExtractResult:
        if not await self.can_handle(url):
            raise ExtractorError("yt-dlp URL does not match the configured platform")
        configured_proxy = _extractor_proxy()
        if not configured_proxy:
            # yt-dlp resolves and fetches both metadata and media itself. It
            # does not use the pinned-DNS downloader, so direct egress is
            # disabled until a controlled outbound proxy is configured.
            raise ExtractorError("yt-dlp extraction requires EXTRACTOR_EGRESS_PROXY_URL")
        proxy = _validate_proxy(configured_proxy)

        yt_dlp = _lazy_import_ytdlp()
        tmp_dir = Path(tempfile.mkdtemp(prefix="spillthereel-ytdlp-"))
        outtmpl = str(tmp_dir / "source.%(ext)s")

        # Options: merge best video+audio; keep the intermediate title
        # metadata; write no side files (no thumbs, no description
        # sidecar, no info json) — we pull what we need out of the info
        # dict directly.
        ydl_opts = {
            "outtmpl": outtmpl,
            "format": "bv*+ba/b",  # best video + best audio, else best combined
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "no_playlist": True,
            "nocheckcertificate": False,
            "socket_timeout": 30,
        }
        ydl_opts["proxy"] = proxy

        # yt-dlp is synchronous — offload to a thread so we don't block
        # the event loop for the ~5-30s a download can take.
        try:
            info, video_path = await asyncio.to_thread(_run_ytdlp, yt_dlp, ydl_opts, url)
        except Exception as e:  # yt-dlp throws many concrete types
            # yt-dlp exception text can include the signed source URL and
            # proxy credentials. Keep the failure class for diagnosis only.
            raise ExtractorError(f"yt-dlp failed ({type(e).__name__})") from None

        owner = None
        uploader = info.get("uploader") or info.get("channel")
        uploader_id = info.get("uploader_id") or info.get("channel_id")
        uploader_url = info.get("uploader_url") or info.get("channel_url")
        if uploader or uploader_id or uploader_url:
            owner = Owner(
                url=uploader_url,
                name=uploader,
                username=uploader_id,
            )

        _logger.info(
            "extractor.ytdlp.extracted",
            path=str(video_path),
            duration=info.get("duration"),
            uploader=uploader,
        )

        return ExtractResult(
            media_type="video",
            video_path=video_path,
            audio_path=None,
            image_paths=[],
            original_caption=info.get("description"),
            original_owner=owner,
            duration_seconds=info.get("duration"),
            resolved_at=datetime.now(UTC),
        )


def _run_ytdlp(yt_dlp: Any, opts: dict[str, Any], url: str) -> tuple[dict[str, Any], Path]:
    """Synchronous inner call executed in a worker thread."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # After download, yt-dlp writes the final path into requested_downloads.
        # Fall back to `filename` field on older yt-dlp versions.
        requested = info.get("requested_downloads") or []
        filepath = (requested[0].get("filepath") if requested else info.get("filepath")) or ""
        if not filepath:
            raise RuntimeError("yt-dlp did not report a downloaded filepath")
        return info, Path(filepath)
