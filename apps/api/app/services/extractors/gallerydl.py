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
from app.services.ingest.platform_detect import UnsupportedPlatformError, detect_platform

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


def _extractor_proxy() -> str | None:
    """Controlled extractor egress proxy URL when configured, else None.

    Env ``EXTRACTOR_EGRESS_PROXY_URL`` first, then settings
    ``extractor_egress_proxy_url``. This is distinct from
    ``OUTBOUND_PROXY_URL`` for paid provider API calls. Never logged —
    the URL may embed `user:pass@` credentials.
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
    """Reject malformed proxy values instead of risking a downloader fallback.

    This is only an application-side guard. The configured proxy must enforce
    destination policy, and the worker network must deny direct egress.
    """
    try:
        parsed = urlsplit(proxy)
        port = parsed.port
    except ValueError:
        raise ExtractorError("gallery-dl extractor proxy configuration is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(ch.isspace() for ch in proxy)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ExtractorError("gallery-dl extractor proxy configuration is invalid")
    return proxy


def _write_proxy_config(proxy: str) -> Path:
    """Write a short-lived mode-0600 gallery-dl config to keep secrets out of argv."""
    fd, raw_path = tempfile.mkstemp(prefix="spillthereel-gallerydl-proxy-", suffix=".json")
    path = Path(raw_path)
    try:
        os.chmod(path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as config_file:
            json.dump(
                {"extractor": {"proxy": proxy}, "downloader": {"proxy": proxy}},
                config_file,
            )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _gallerydl_subprocess_env() -> dict[str, str]:
    """Give the downloader only runtime necessities, not API/service secrets."""
    allowed = {
        "PATH",
        "HOME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "SYSTEMROOT",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
    }
    env = {name: value for name, value in os.environ.items() if name in allowed}
    env.setdefault("PATH", os.defpath)
    return env


class GalleryDlExtractor:
    """Extractor Protocol impl (image-only)."""

    def __init__(self, platform: Platform) -> None:
        self.platform: Platform = platform

    async def can_handle(self, url: str) -> bool:
        # Registry scopes us to instagram + x (the image-post platforms,
        # TRD §12.3) and tries us last, so video never reaches us while
        # yt-dlp is healthy. Trust the dispatch like the other
        # extractors do — no URL-path sniffing (see module docstring).
        if self.platform not in ("instagram", "x"):
            return False
        try:
            return detect_platform(url) == self.platform
        except (UnsupportedPlatformError, ValueError):
            return False

    async def extract(self, url: str) -> ExtractResult:
        if not await self.can_handle(url):
            raise ExtractorError("gallery-dl URL does not match the configured platform")
        redacted = _redact_url(url)
        configured_proxy = _extractor_proxy()
        if not configured_proxy:
            # gallery-dl fetches metadata and media itself; neither phase
            # uses the pinned-DNS media downloader.
            raise ExtractorError("gallery-dl extraction requires EXTRACTOR_EGRESS_PROXY_URL")
        proxy = _validate_proxy(configured_proxy)
        proxy_config = _write_proxy_config(proxy)
        try:
            metadata_out, _ = await _run_gallery_dl(
                [
                    "--dump-json",
                    "--range",
                    f"1-{_MAX_IMAGES}",
                    "--config-ignore",
                    "--config-json",
                    str(proxy_config),
                    url,
                ],
                timeout=_TIMEOUT_SECONDS,
                redacted_url=redacted,
            )
            caption, owner = _parse_metadata(metadata_out)

            # Pipeline owns deletion of returned paths (TRD §15.7); on any
            # failure below we clean up the tmpdir ourselves.
            tmp_dir = Path(tempfile.mkdtemp(prefix="spillthereel-gallerydl-"))
            try:
                _, _ = await _run_gallery_dl(
                    [
                        "--destination",
                        str(tmp_dir),
                        "--range",
                        f"1-{_MAX_IMAGES}",
                        "--config-ignore",
                        "--config-json",
                        str(proxy_config),
                        url,
                    ],
                    timeout=_TIMEOUT_SECONDS,
                    redacted_url=redacted,
                )
                image_paths = _collect_images(tmp_dir)
                if not image_paths:
                    _raise_no_images(tmp_dir, redacted)
            except BaseException:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise
        finally:
            proxy_config.unlink(missing_ok=True)

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
            env=_gallerydl_subprocess_env(),
        )
    except OSError:
        raise ExtractorError("could not spawn gallery-dl") from None

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError as e:
        proc.kill()
        await proc.wait()
        raise ExtractorError(f"gallery-dl timed out after {timeout:g}s for {redacted_url}") from e

    if proc.returncode != 0:
        # stderr may contain a signed source URL or authorization token.
        raise ExtractorError(f"gallery-dl exited with status {proc.returncode}")
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


def _raise_no_images(tmp_dir: Path, redacted_url: str) -> NoReturn:
    videos = [p for p in tmp_dir.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_SUFFIXES]
    if videos:
        raise ExtractorError(
            f"gallery-dl returned video-only content for {redacted_url} "
            f"({len(videos)} video file(s)); leaving video to yt-dlp"
        )
    # Do not surface stderr: gallery-dl may echo signed source URLs or
    # authorization data in its diagnostics.
    raise ExtractorError(f"gallery-dl downloaded no images for {redacted_url}")


def _redact_url(url: str) -> str:
    """Strip query, fragment, and URL userinfo before logging."""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return "<unparseable-url>"
    if not hostname:
        return "<unparseable-url>"
    # Rebuild netloc from hostname/port; retaining parts.netloc would expose
    # `username:password@` if a source URL unexpectedly contains userinfo.
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
