"""Gemini SummaryModel — multimodal (frames + audio) → StructuredSummary.

Uses `google-genai` (the newer unified SDK) rather than
`google-generativeai` — the API surface is cleaner and matches Google's
2026 direction. Default model is Flash-Lite for cost; the pipeline
upgrades to Pro when the user's `quality` preference is "deep".

Cost per short reel (≤90s) at Flash-Lite: ~$0.008-$0.015. TRD §7.5.
"""

from __future__ import annotations

import json
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
            parts.append(
                genai_types.Part.from_text(text=f"Pre-transcribed audio:\n{transcript}")
            )
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

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            # Schema-mode almost never violates — but if it does, one
            # cheap retry with a repair prompt (PRD F2.5) is worth the
            # $0.001 to save the item.
            _logger.warning("gemini.json_decode_failed", err=str(e), body=text[:200])
            raise

        summary = StructuredSummary.model_validate(payload)
        _logger.info(
            "llm.gemini.summarized",
            model=self._model,
            frames=len(frames),
            title=summary.title[:60],
        )
        return summary


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
