"""Extractor protocol — the swappable interface for media extraction.

Implementations: cobalt.py (primary), ytdlp.py (fallback), gallerydl.py
(image content). All live behind this Protocol so the ingestion pipeline
never imports a concrete extractor directly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel


Platform = Literal["instagram", "tiktok", "youtube", "x"]


class Owner(BaseModel):
    url: str | None
    name: str | None
    username: str | None


class ExtractResult(BaseModel):
    media_type: Literal["video", "image", "text"]
    video_path: Path | None = None
    audio_path: Path | None = None
    image_paths: list[Path] = []
    original_caption: str | None = None
    original_owner: Owner | None = None
    duration_seconds: float | None = None
    resolved_at: datetime


class ExtractorError(Exception):
    """Raised by an extractor when it cannot resolve the given URL.

    Distinct from unexpected exceptions — an ExtractorError signals a
    known, recoverable failure the registry should walk past and try
    the next extractor for. Any other exception aborts the pipeline.
    """


class NoExtractorSucceededError(Exception):
    """All extractors in the registry failed for this URL."""


class Extractor(Protocol):
    platform: Platform

    async def can_handle(self, url: str) -> bool: ...

    async def extract(self, url: str) -> ExtractResult: ...
