"""gallery-dl extractor — still-image path for IG carousels / X threads.

Runs the `gallery-dl` CLI as a subprocess in two phases:

  1. metadata: `gallery-dl --dump-json --range 1-N <url>` → caption/owner.
  2. download: `gallery-dl --destination <tmpdir> --range 1-N <url>` → files.

Why subprocess instead of an import: gallery-dl ships in the `worker`
extra (`gallery-dl>=1.28`, uv.lock pins 1.32.x) which the worker image
installs via frozen sync; the api process never imports it, so there is
no import at all here (not even a deferred one) and api boot pays nothing.

Fallback position: registered LAST on instagram + x (TRD §12.3 — IG
carousels, X image threads). Video stays with yt-dlp by chain
order: a video URL either succeeds upstream or fails there first, so it
never reaches us in the healthy case. URL-path sniffing (/p/ vs /reel/)
is deliberately NOT used — an IG /p/ post can itself hold a video — so
media-type fitness is decided post-download by file extension, and a
video-only result raises ExtractorError instead of mislabeling.

Pipeline handoff: ExtractResult(media_type="image", image_paths=[...],
original_caption=..., original_owner=...) — the analyze stage consumes
image_paths as multimodal frames plus original_caption alongside
summary.on_screen_text (see services/llm/base.py StructuredSummary).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit, urlunsplit

from app.observability.logging import get_logger
from app.services.extractors.base import (
    ExtractorError,
    ExtractResult,
    Owner,
    Platform,
)

_logger = get_logger(__name__)

# One gallery-dl run that hangs past this means the source site is
# soft-blocking us — give up and let the registry raise
# NoExtractorSucceededError so the pipeline can mark the item failed.
_TIMEOUT_SECONDS = 60.0

# Carousel cap: bound tmpfs + downstream Gemini frame cost (TRD §7.5).
_MAX_IMAGES = 10

# Still-image suffixes we hand to the multimodal model. No GIF: Gemini's
# image parts are stills (jpeg/png) and an animated file would mislabel.
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# Seen when a video URL falls all the way through to us — detected so we
# can refuse to mislabel video as image (video belongs to yt-dlp).
_VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mkv", ".mov", ".m4a", ".mp3", ".wav"})

# Enough stderr to debug (HTTP status, extractor name) without flooding logs.
_STDERR_TAIL_CHARS = 500


def _outbound_proxy() -> str | None:
    """Egress proxy URL when configured, else None.

    Env ``OUTBOUND_PROXY_URL`` first, then settings ``outbound_proxy_url``
    (same precedence as the Part D scraper providers). Never logged — the
    URL may embed `user:pass@` credentials.
    """
    raw = os.environ.get("OUTBOUND_PROXY_URL", "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings

        value = getattr(get_settings(), "outbound_proxy_url", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


def _proxy_args() -> list[str]:
    """CLI fragment for proxying, or [] when direct egress.

    gallery-dl documents `--proxy URL` under Networking Options. Callers
    splice this immediately before the URL so flag-position-sensitive
    assertions (and gallery-dl's own parsing) keep working.
    """
    proxy = _outbound_proxy()
    return ["--proxy", proxy] if proxy else []


class GalleryDlExtractor:
    """Extractor Protocol impl (image-only)."""

    def __init__(self, platform: Platform) -> None:
        self.platform: Platform = platform

    async def can_handle(self, url: str) -> bool:
        # Registry scopes us to instagram + x (the image-post platforms,
        # TRD §12.3) and tries us last, so video never reaches us while
        # yt-dlp is healthy. Trust the dispatch like the other
        # extractors do — no URL-path sniffing (see module docstring).
        return self.platform in ("instagram", "x")

    async def extract(self, url: str) -> ExtractResult:
        redacted = _redact_url(url)

        metadata_out, _ = await _run_gallery_dl(
            ["--dump-json", "--range", f"1-{_MAX_IMAGES}", *_proxy_args(), url],
            timeout=_TIMEOUT_SECONDS,
            redacted_url=redacted,
        )
        caption, owner = _parse_metadata(metadata_out)

        # Pipeline owns deletion of returned paths (TRD §15.7); on any failure below we clean up
        # the tmpdir ourselves so failed items leak nothing.
        tmp_dir = Path(tempfile.mkdtemp(prefix="spillthereel-gallerydl-"))
        try:
            _, dl_stderr = await _run_gallery_dl(
                [
                    "--destination",
                    str(tmp_dir),
                    "--range",
                    f"1-{_MAX_IMAGES}",
                    *_proxy_args(),
                    url,
                ],
                timeout=_TIMEOUT_SECONDS,
                redacted_url=redacted,
            )
            image_paths = _collect_images(tmp_dir)
            if not image_paths:
                _raise_no_images(tmp_dir, dl_stderr, redacted)
        except BaseException:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        _logger.info(
            "extractor.gallerydl.extracted",
            platform=self.platform,
            url=redacted,
            images=len(image_paths),
            caption_chars=len(caption) if caption else 0,
        )
        return ExtractResult(
            media_type="image",
            video_path=None,
            audio_path=None,
            image_paths=image_paths,
            original_caption=caption,
            original_owner=owner,
            duration_seconds=None,
            resolved_at=datetime.now(UTC),
        )


async def _run_gallery_dl(
    args: list[str], *, timeout: float, redacted_url: str
) -> tuple[bytes, bytes]:
    """Run one `gallery-dl` invocation; map every failure to ExtractorError.

    `args` holds the RAW url (gallery-dl needs it) — never log `args`
    itself, only `redacted_url` (query/fragment stripped, where tokens
    hide).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "gallery-dl",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        raise ExtractorError(f"could not spawn gallery-dl for {redacted_url}: {e}") from e

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise ExtractorError(f"gallery-dl timed out after {timeout:g}s for {redacted_url}") from e

    if proc.returncode != 0:
        tail = stderr.decode("utf-8", "replace")[-_STDERR_TAIL_CHARS:]
        raise ExtractorError(f"gallery-dl exit {proc.returncode} for {redacted_url}: {tail}")
    return stdout, stderr


def _parse_metadata(stdout: bytes) -> tuple[str | None, Owner | None]:
    """Best-effort caption/owner from `--dump-json` output. Never raises.

    The dump shape varies by site extractor (single object vs list vs
    one-object-per-line), so accept all three. Missing metadata degrades
    to None fields — the download phase is the real success gate.
    """
    objects = _load_json_objects(stdout)
    caption: str | None = None
    for obj in objects:
        for key in ("description", "caption", "content", "text"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                caption = value.strip()
                break
        if caption is not None:
            break

    owner: Owner | None = None
    if objects:
        first = objects[0]
        name = _first_str(first, "fullname", "uploader", "author")
        username = _first_str(first, "username", "uploader_id")
        url = _first_str(first, "uploader_url", "profile_url")
        if name is not None or username is not None or url is not None:
            owner = Owner(url=url, name=name, username=username)
    return caption, owner


def _load_json_objects(stdout: bytes) -> list[dict[str, Any]]:
    text = stdout.decode("utf-8", "replace").strip()
    if not text:
        return []
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [o for o in payload if isinstance(o, dict)]
    objects: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objects.append(obj)
    return objects


def _first_str(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _collect_images(tmp_dir: Path) -> list[Path]:
    """Still-image files under the download dir, sorted + capped.

    gallery-dl nests output (e.g. `instagram/<user>/...`), hence rglob.
    """
    found = sorted(
        p for p in tmp_dir.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )
    return found[:_MAX_IMAGES]


def _raise_no_images(tmp_dir: Path, stderr: bytes, redacted_url: str) -> NoReturn:
    videos = [p for p in tmp_dir.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_SUFFIXES]
    if videos:
        raise ExtractorError(
            f"gallery-dl returned video-only content for {redacted_url} "
            f"({len(videos)} video file(s)); leaving video to yt-dlp"
        )
    tail = stderr.decode("utf-8", "replace")[-_STDERR_TAIL_CHARS:]
    raise ExtractorError(f"gallery-dl downloaded no images for {redacted_url}: {tail}")


def _redact_url(url: str) -> str:
    """Strip query + fragment for log/error lines — tokens live there."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
