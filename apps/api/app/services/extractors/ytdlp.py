"""yt-dlp extractor — fallback path when Cobalt returns an error.

Invoked in-process via yt-dlp's Python API (not subprocess) — a shell
fork per save adds ~50ms and yt-dlp is happy to be embedded. Cookies
default to none; per-platform cookie files can be added via Secret
Manager later (TRD §12.2).

yt-dlp lands metadata (uploader, title, description, duration) as a
side-effect of the download — we capture what we can into ExtractResult
so downstream stages get a richer picture than Cobalt alone provides.
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.logging import get_logger
from app.services.extractors.base import (
    ExtractResult,
    ExtractorError,
    Owner,
    Platform,
)

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


class YtDlpExtractor:
    """Extractor Protocol impl."""

    def __init__(self, platform: Platform) -> None:
        self.platform: Platform = platform

    async def can_handle(self, url: str) -> bool:
        return True

    async def extract(self, url: str) -> ExtractResult:
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

        # yt-dlp is synchronous — offload to a thread so we don't block
        # the event loop for the ~5-30s a download can take.
        try:
            info, video_path = await asyncio.to_thread(_run_ytdlp, yt_dlp, ydl_opts, url)
        except Exception as e:  # yt-dlp throws many concrete types
            raise ExtractorError(f"yt-dlp failed: {e}") from e

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
