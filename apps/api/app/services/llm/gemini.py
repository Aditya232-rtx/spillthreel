"""Gemini SummaryModel — multimodal (frames + audio) → StructuredSummary.

Uses `google-genai` (the newer unified SDK) rather than
`google-generativeai` — the API surface is cleaner and matches Google's
2026 direction. Default model is Flash-Lite for cost; the pipeline
upgrades to Pro when the user's `quality` preference is "deep".

Cost per short reel (≤90s) at Flash-Lite: ~$0.008-$0.015. TRD §7.5.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.observability.logging import get_logger
from app.services.llm.base import (
    AudioTrack,
    Frame,
    StructuredSummary,
)
from app.settings import get_settings

_logger = get_logger(__name__)

# Gemini's structured-output support lets us request a JSON schema and
# the response is guaranteed valid — no repair-prompt round-trip needed
# in the common case. See PRD F2.5.
_SUMMARY_SCHEMA = {
    "type": "object",
    "required": ["title", "summary"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "transcript": {"type": "string"},
        "on_screen_text": {"type": "string"},
        "objects": {"type": "array", "items": {"type": "string"}},
        "scenes": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}},
        "sentiment": {
            "type": "string",
            "enum": ["informative", "funny", "inspirational", "tutorial", "product", "other"],
        },
        "primary_language": {"type": "string"},
        "duration_seconds": {"type": "number"},
    },
}

_SYSTEM_PROMPT = """\
You are analyzing a short-form social media video. Return a single JSON
object matching the provided schema. Rules:
- title: 6-10 words, capture the video's core action or claim.
- summary: 2-3 sentences, factual, no fluff.
- transcript: full spoken audio, verbatim, punctuated. Empty string if no speech.
- on_screen_text: any visible text (OCR). Empty string if none.
- objects: 3-8 concrete nouns visible in the frames (people count as "person").
- scenes: 3-5 sentence fragments describing distinct moments.
- topics: 2-4 broad category tags (e.g. "recipe", "workout", "travel").
- sentiment: choose ONE from the enum. When in doubt, use "informative".
- primary_language: ISO-639-1 code of the spoken audio (or on-screen text if silent).
- duration_seconds: numeric length in seconds.

If the content is silent, still fill transcript with "" — do not omit the key.
"""


class GeminiSummaryModel:
    """SummaryModel Protocol impl."""

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.gemini_model_flash_lite

    async def summarize(
        self,
        frames: list[Frame],
        audio: AudioTrack | None,
        transcript: str | None,
    ) -> StructuredSummary:
        # Deferred so `import app.services.llm.gemini` doesn't force the
        # SDK on the api container (worker image bundles google-genai;
        # api image doesn't need it).
        from google import genai
        from google.genai import types as genai_types

        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("gemini_api_key not configured")

        client = genai.Client(api_key=settings.gemini_api_key)

        # Compose the multimodal parts:
        #   * system prompt (text)
        #   * pre-computed transcript (text) — only when audio was too long
        #     for direct upload and we ran Groq Whisper upstream.
        #   * every sampled frame (image bytes)
        #   * the audio track (bytes) — only when short enough for
        #     Gemini's native ASR (< 5min per PRD F2.3).
        parts: list[Any] = [
            genai_types.Part.from_text(text=_SYSTEM_PROMPT),
        ]
        if transcript:
            parts.append(genai_types.Part.from_text(text=f"Pre-transcribed audio:\n{transcript}"))
        for frame in frames:
            parts.append(_part_from_image(frame.path))
        if audio is not None:
            parts.append(_part_from_audio(audio.path))

        response = await client.aio.models.generate_content(
            model=self._model,
            contents=parts,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_SUMMARY_SCHEMA,
                # Deterministic-ish: some creativity in the title is fine
                # but we don't want the transcript hallucinating.
                temperature=0.3,
                max_output_tokens=8192,
            ),
        )

        text = response.text
        if not text:
            raise RuntimeError("gemini returned empty response")

        summary = _structured_summary_from_text(text)
        _logger.info(
            "llm.gemini.summarized",
            model=self._model,
            frames=len(frames),
            title=summary.title[:60],
        )
        return summary

    async def summarize_youtube(
        self,
        url: str,
        *,
        title: str | None = None,
        channel: str | None = None,
        description: str | None = None,
        duration_seconds: float | None = None,
    ) -> StructuredSummary:
        """Summarize a PUBLIC YouTube video without downloading it.

        Per the current video-understanding docs, a public YouTube URL is
        passed as a FileData video part (``file_uri`` = the watch URL, no
        bytes, no MIME type)::

            Part(file_data=FileData(file_uri="https://www.youtube.com/watch?v=..."))

        Constraints from the docs: the video MUST be public, max ONE
        YouTube URL per request. Same system prompt + response schema as
        :meth:`summarize`, so the returned StructuredSummary is identical
        in shape. Media resolution defaults to LOW (cost/latency) via
        ``YOUTUBE_GEMINI_MEDIA_RESOLUTION``.
        """
        from google import genai
        from google.genai import types as genai_types

        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("gemini_api_key not configured")

        client = genai.Client(api_key=settings.gemini_api_key)

        parts: list[Any] = [
            genai_types.Part.from_text(text=_SYSTEM_PROMPT),
            genai_types.Part(file_data=genai_types.FileData(file_uri=url)),
        ]
        hints = [
            line
            for line in (
                f"Known title: {title}" if title else "",
                f"Known channel: {channel}" if channel else "",
                f"Known description: {description[:2000]}" if description else "",
                f"Known duration: {duration_seconds:.0f}s" if duration_seconds else "",
            )
            if line
        ]
        if hints:
            parts.append(
                genai_types.Part.from_text(
                    text=(
                        "Known metadata about this video (hints only — "
                        "the video itself is the source of truth):\n" + "\n".join(hints)
                    )
                )
            )

        base_config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": _SUMMARY_SCHEMA,
            "temperature": 0.3,
            "max_output_tokens": 8192,
        }
        resolution = _youtube_media_resolution()
        try:
            if resolution:
                config = genai_types.GenerateContentConfig(
                    media_resolution=resolution, **base_config
                )
            else:
                config = genai_types.GenerateContentConfig(**base_config)
        except TypeError:
            # Older google-genai SDKs predate the media_resolution field —
            # fall back to defaults rather than failing the item.
            _logger.warning("llm.gemini.no_media_resolution_support")
            config = genai_types.GenerateContentConfig(**base_config)

        response = await client.aio.models.generate_content(
            model=self._model,
            contents=parts,
            config=config,
        )

        text = response.text
        if not text:
            raise RuntimeError("gemini returned empty response")

        summary = _structured_summary_from_text(text)
        _logger.info(
            "llm.gemini.summarized_youtube",
            model=self._model,
            url=url,
            resolution=resolution,
            title=summary.title[:60],
        )
        return summary


def _structured_summary_from_text(text: str) -> StructuredSummary:
    """Parse a schema-mode JSON response into a StructuredSummary.

    Shared by summarize() and summarize_youtube() — both use the same
    prompt + schema, so the payload shape is identical.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        # Schema-mode almost never violates — but if it does, one
        # cheap retry with a repair prompt (PRD F2.5) is worth the
        # $0.001 to save the item.
        _logger.warning("gemini.json_decode_failed", err=str(e), body=text[:200])
        raise
    return StructuredSummary.model_validate(payload)


_YOUTUBE_RESOLUTION_ALIASES = {
    "LOW": "MEDIA_RESOLUTION_LOW",
    "MEDIA_RESOLUTION_LOW": "MEDIA_RESOLUTION_LOW",
    "MEDIUM": "MEDIA_RESOLUTION_MEDIUM",
    "MEDIA_RESOLUTION_MEDIUM": "MEDIA_RESOLUTION_MEDIUM",
    "HIGH": "MEDIA_RESOLUTION_HIGH",
    "MEDIA_RESOLUTION_HIGH": "MEDIA_RESOLUTION_HIGH",
    "UNSPECIFIED": "MEDIA_RESOLUTION_UNSPECIFIED",
    "MEDIA_RESOLUTION_UNSPECIFIED": "MEDIA_RESOLUTION_UNSPECIFIED",
}


def _youtube_media_resolution() -> str | None:
    """Resolve the media-resolution setting for YouTube native calls.

    Backed by settings ``youtube_gemini_media_resolution`` (env
    ``YOUTUBE_GEMINI_MEDIA_RESOLUTION``). LOW by default: ~64 tokens/frame on
    Gemini 2.5 (~100 tok/s incl. audio) vs 256/frame at MEDIUM/HIGH
    (~300 tok/s). Accepts ``low``/``medium``/``high``/``unspecified``
    (case-insensitive) or the full ``MEDIA_RESOLUTION_*`` enum names.
    """
    settings = get_settings()
    raw = getattr(settings, "youtube_gemini_media_resolution", None)
    if raw is None:
        raw = os.getenv("YOUTUBE_GEMINI_MEDIA_RESOLUTION", "MEDIA_RESOLUTION_LOW")
    resolved = _YOUTUBE_RESOLUTION_ALIASES.get(str(raw).strip().upper())
    if resolved is None:
        _logger.warning("llm.gemini.unknown_media_resolution", raw=str(raw))
        return "MEDIA_RESOLUTION_LOW"
    if resolved == "MEDIA_RESOLUTION_UNSPECIFIED":
        return None
    return resolved


def _part_from_image(path: Path) -> Any:
    from google.genai import types as genai_types

    data = path.read_bytes()
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return genai_types.Part.from_bytes(data=data, mime_type=mime)


def _part_from_audio(path: Path) -> Any:
    from google.genai import types as genai_types

    data = path.read_bytes()
    # 16kHz mono WAV per media/ffmpeg.py extract_audio conventions.
    return genai_types.Part.from_bytes(data=data, mime_type="audio/wav")


# ---------------------------------------------------------------------------
# Gemini Embedding Model — for pgvector embeddings
# ---------------------------------------------------------------------------


class GeminiEmbeddingModel:
    """Embedding model using Gemini's embedding API.

    Uses `gemini-embedding-001` which outputs 768-dimensional vectors.
    Cost: $0.15/1M input tokens (as of 2026).
    """

    def __init__(self, model: str | None = None) -> None:
        self._model = model or "gemini-embedding-001"

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts using Gemini's embedding API.

        Returns a list of embeddings, one per input text.
        Each embedding is a list of 768 floats.
        """
        if not texts:
            return []

        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("gemini_api_key not configured")

        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=settings.gemini_api_key)

        # Batch embed - gemini-embedding-001 supports batch inputs
        response = await client.aio.models.embed_content(
            model=self._model,
            contents=texts,
            config=genai_types.EmbedContentConfig(
                task_type="SEMANTIC_SIMILARITY",
                output_dimensionality=768,
            ),
        )

        if not response.embeddings:
            raise RuntimeError("gemini returned no embeddings")
        embeddings: list[list[float]] = []
        for embedding in response.embeddings:
            if embedding.values is None:
                raise RuntimeError("gemini returned an empty embedding vector")
            embeddings.append([float(v) for v in embedding.values])
        if len(embeddings) != len(texts):
            raise RuntimeError(
                f"gemini returned {len(embeddings)} embeddings for {len(texts)} texts"
            )
        return embeddings
