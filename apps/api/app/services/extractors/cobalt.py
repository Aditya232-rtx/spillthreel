"""Cobalt extractor — primary path for IG Reels, TikTok, YT Shorts, X.

Talks to a self-hosted Cobalt instance (upstream `imputnet/cobalt`). Cobalt
returns a signed, tunneled download URL and we stream the media to a
tmpfs path the ingestion pipeline hands off to ffmpeg. Zero persistence
in Cobalt — this matches TRD §12.1.

Fallback chain: if Cobalt returns an error / times out / hits a platform
block, the extractor registry (services/extractors/registry.py) walks
to the next extractor (yt-dlp).
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.observability.logging import get_logger
from app.services.extractors.base import (
    ExtractResult,
    ExtractorError,
    Platform,
)
from app.settings import get_settings

_logger = get_logger(__name__)

# Per Cobalt's HTTP contract — anything above ~120s means either the
# source platform is soft-rate-limiting us or Cobalt itself is stuck.
# Either way we want to give up and let the fallback chain try yt-dlp.
_REQUEST_TIMEOUT_SECONDS = 60.0
_STREAM_TIMEOUT_SECONDS = 120.0

# Cobalt exposes `POST /` (older versions used `POST /api/json`); its
# response shape is `{status: 'stream' | 'redirect' | 'error', url, ...}`.
_COBALT_API_PATH = "/"


class CobaltExtractor:
    """Extractor Protocol impl."""

    def __init__(self, platform: Platform) -> None:
        self.platform: Platform = platform

    async def can_handle(self, url: str) -> bool:
        # Registry already called platform_detect on this URL — we trust
        # its dispatch. Cobalt supports all four platforms we ship in v1.
        return True

    async def extract(self, url: str) -> ExtractResult:
        settings = get_settings()
        if not settings.cobalt_base_url:
            raise ExtractorError("cobalt_base_url not configured")

        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.cobalt_api_key:
            headers["Authorization"] = f"Api-Key {settings.cobalt_api_key}"

        # Cobalt's request body — `downloadMode: auto` lets it decide
        # video vs audio-only vs muxed based on the source's best stream.
        payload = {"url": url, "downloadMode": "auto"}

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                cobalt_url = settings.cobalt_base_url.rstrip("/") + _COBALT_API_PATH
                response = await client.post(cobalt_url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            raise ExtractorError(f"cobalt HTTP error: {e}") from e

        if response.status_code >= 400:
            raise ExtractorError(
                f"cobalt returned {response.status_code}: {response.text[:200]}"
            )

        try:
            body = response.json()
        except ValueError as e:
            raise ExtractorError(f"cobalt returned non-JSON: {e}") from e

        status = body.get("status")
        if status == "error":
            code = body.get("error", {}).get("code", "unknown")
            raise ExtractorError(f"cobalt error: {code}")
        if status not in ("stream", "redirect", "tunnel"):
            raise ExtractorError(f"unexpected cobalt status: {status!r}")

        stream_url = body.get("url")
        if not stream_url:
            raise ExtractorError("cobalt returned no download url")

        # Cobalt's `stream` URLs point back at Cobalt itself so it can
        # proxy the media to us — we download to tmpfs.
        video_path = await _download_to_tmp(stream_url)

        return ExtractResult(
            media_type="video",
            video_path=video_path,
            audio_path=None,
            image_paths=[],
            original_caption=None,  # Cobalt does not surface captions
            original_owner=None,
            duration_seconds=None,  # ffmpeg probes this downstream
            resolved_at=datetime.now(UTC),
        )


async def _download_to_tmp(url: str) -> Path:
    """Stream a media URL to a random tmpfs path.

    Returns the path; caller is responsible for deletion (typically the
    ingest pipeline's finally-block per TRD §15.7).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="spillthereel-ingest-"))
    # We don't know the extension until we read the response headers;
    # ffmpeg autodetects from magic bytes so a generic name is fine.
    video_path = tmp_dir / "source.bin"

    try:
        async with httpx.AsyncClient(timeout=_STREAM_TIMEOUT_SECONDS) as client:
            async with client.stream("GET", url) as r:
                r.raise_for_status()
                with video_path.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 64):
                        f.write(chunk)
    except httpx.HTTPError as e:
        # Clean up the empty file on failure — pipeline should never see
        # a half-written source that ffmpeg then chokes on.
        video_path.unlink(missing_ok=True)
        tmp_dir.rmdir()
        raise ExtractorError(f"failed to download from cobalt: {e}") from e

    _logger.info(
        "extractor.cobalt.downloaded",
        path=str(video_path),
        size_bytes=video_path.stat().st_size,
    )
    return video_path
