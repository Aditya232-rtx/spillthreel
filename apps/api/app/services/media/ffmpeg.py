"""ffmpeg utilities — frame sampling, audio extraction, thumbnail crop.

We shell out to ffmpeg because Python bindings (pyav, moviepy) don't
give us the format-jugglery flexibility we need for Gemini's multimodal
input requirements (specific frame sizes, mono/16kHz WAV for Whisper).

Every subprocess call:
  * uses `-nostdin` so a broken pipe on stdin can't hang us.
  * has an aggressive timeout.
  * runs offloaded to a thread (asyncio.to_thread) so the event loop
    stays free.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.observability.logging import get_logger

_logger = get_logger(__name__)

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Frame sampling default per PRD F2.4 (1 frame every 2 seconds). Configurable
# because long-form YT branches to 1 frame every 10 seconds.
DEFAULT_FRAME_INTERVAL_SECONDS = 2.0

_SUBPROCESS_TIMEOUT_SECONDS = 300  # 5 min hard cap on any single ffmpeg call


class MediaError(RuntimeError):
    """Raised when ffmpeg produces no output or exits non-zero."""


@dataclass(frozen=True)
class MediaProbe:
    duration_seconds: float
    width: int
    height: int
    has_video: bool
    has_audio: bool


def _require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise MediaError(f"{name} not found on PATH — install ffmpeg in worker image")


async def probe(src: Path) -> MediaProbe:
    """ffprobe → structured MediaProbe."""
    _require_binary(FFPROBE)
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,width,height",
        "-of", "json",
        str(src),
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd,
        capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe failed: {proc.stderr.decode()[:200]}")

    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(data.get("format", {}).get("duration", 0.0))
    return MediaProbe(
        duration_seconds=duration,
        width=int((video or {}).get("width", 0)),
        height=int((video or {}).get("height", 0)),
        has_video=video is not None,
        has_audio=audio is not None,
    )


async def sample_frames(
    src: Path, out_dir: Path, interval_seconds: float = DEFAULT_FRAME_INTERVAL_SECONDS
) -> list[Path]:
    """Extract 1 JPEG per `interval_seconds`.

    Files land as `out_dir / frame_%04d.jpg`. Directory is created.
    """
    _require_binary(FFMPEG)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / "frame_%04d.jpg"
    cmd = [
        FFMPEG, "-nostdin", "-y",
        "-i", str(src),
        # 1 frame every `interval_seconds`, capped to 720p to control Gemini
        # cost — sampling denser than that adds tokens without meaningfully
        # improving the summary.
        "-vf", f"fps=1/{interval_seconds},scale=iw*min(1280/iw\\,720/ih):ih*min(1280/iw\\,720/ih)",
        "-qscale:v", "3",
        str(pattern),
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd,
        capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg frame-sample failed: {proc.stderr.decode()[:400]}")

    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not frames:
        raise MediaError("ffmpeg produced no frames")
    _logger.info("media.frames_sampled", count=len(frames), interval=interval_seconds)
    return frames


async def extract_audio(src: Path, out: Path) -> Path:
    """Extract mono 16kHz WAV for Whisper.

    16kHz mono is Whisper-large-v3-turbo's native sample rate; higher
    rates just cost more without accuracy gains.
    """
    _require_binary(FFMPEG)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-nostdin", "-y",
        "-i", str(src),
        "-vn",  # no video
        "-ac", "1",  # mono
        "-ar", "16000",  # 16kHz
        "-c:a", "pcm_s16le",
        str(out),
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd,
        capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg audio-extract failed: {proc.stderr.decode()[:400]}")
    return out


async def make_thumbnail(src: Path, out: Path, at_seconds: float | None = None) -> Path:
    """Extract a single JPEG thumbnail.

    If `at_seconds` is None, grabs the frame at 50% duration (midpoint
    is usually the most representative for short reels).
    """
    _require_binary(FFMPEG)
    out.parent.mkdir(parents=True, exist_ok=True)

    if at_seconds is None:
        p = await probe(src)
        at_seconds = max(0.0, p.duration_seconds * 0.5)

    cmd = [
        FFMPEG, "-nostdin", "-y",
        "-ss", f"{at_seconds:.3f}",
        "-i", str(src),
        "-vframes", "1",
        "-vf", "scale=720:-2",  # 720px wide, preserve aspect
        "-qscale:v", "3",
        str(out),
    ]
    proc = await asyncio.to_thread(
        subprocess.run, cmd,
        capture_output=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg thumbnail failed: {proc.stderr.decode()[:400]}")
    return out
