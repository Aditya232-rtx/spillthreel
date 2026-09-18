"""LLM protocol interfaces — SummaryModel + TranscriptionModel.

Implementations:
- gemini.py       → SummaryModel (multimodal, primary)
- groq_whisper.py → TranscriptionModel (long-audio fallback, ≥ 5 min)

Rule: the ingestion pipeline (services.ingest.pipeline) never imports a
concrete implementation; it takes a SummaryModel + TranscriptionModel
from the registry so we can hot-swap the LLM without touching ingest.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class Sentiment(str, Enum):
    INFORMATIVE = "informative"
    FUNNY = "funny"
    INSPIRATIONAL = "inspirational"
    TUTORIAL = "tutorial"
    PRODUCT = "product"
    OTHER = "other"


class StructuredSummary(BaseModel):
    """The multimodal-summary schema Gemini must return (PRD F2.5)."""

    title: str = Field(..., description="~8-word natural title")
    summary: str = Field(..., description="2–3 sentence description")
    transcript: str = Field(default="", description="full audio transcript")
    on_screen_text: str = Field(default="", description="OCR of on-screen text")
    objects: list[str] = Field(default_factory=list)
    scenes: list[str] = Field(default_factory=list, description="3–5 scene descriptions")
    topics: list[str] = Field(default_factory=list, description="auto-categorization tags")
    sentiment: Sentiment = Sentiment.OTHER
    primary_language: str = "en"
    duration_seconds: float = 0.0


@dataclass
class Frame:
    """A sampled video frame passed to the multimodal model."""

    path: Path
    timestamp_seconds: float


@dataclass
class AudioTrack:
    path: Path
    duration_seconds: float


class SummaryModel(Protocol):
    async def summarize(
        self,
        frames: list[Frame],
        audio: AudioTrack | None,
        transcript: str | None,
    ) -> StructuredSummary: ...


class TranscriptionModel(Protocol):
    async def transcribe(self, audio: AudioTrack) -> str: ...
