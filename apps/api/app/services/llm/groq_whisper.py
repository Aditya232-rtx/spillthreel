"""Groq Whisper TranscriptionModel — long-audio fallback (≥ 5 min).

Only invoked by the ingest pipeline for content Gemini can't do
efficiently as multimodal input. Groq's `whisper-large-v3-turbo` runs
at ~$0.04/hr and returns transcripts fast enough that we can still
land items in <60s per the PRD F2.3 median target.
"""

from __future__ import annotations

from app.observability.logging import get_logger
from app.services.llm.base import AudioTrack, TranscriptionModel
from app.settings import get_settings

_logger = get_logger(__name__)


class GroqWhisperModel(TranscriptionModel):
    """TranscriptionModel Protocol impl."""

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.groq_asr_model

    async def transcribe(self, audio: AudioTrack) -> str:
        from groq import AsyncGroq

        settings = get_settings()
        if not settings.groq_api_key:
            raise RuntimeError("groq_api_key not configured")

        client = AsyncGroq(api_key=settings.groq_api_key)
        with audio.path.open("rb") as fp:
            response = await client.audio.transcriptions.create(
                model=self._model,
                file=(audio.path.name, fp.read()),
                # `text` gives us the plain transcript without segment
                # metadata — we don't need timecodes at this stage.
                response_format="text",
                temperature=0.0,  # deterministic transcription
            )

        # AsyncGroq returns either a str (text format) or a BaseModel
        # depending on SDK version.
        transcript = str(response) if isinstance(response, str) else response.text  # type: ignore[union-attr]
        _logger.info(
            "llm.groq.transcribed",
            duration=audio.duration_seconds,
            transcript_chars=len(transcript),
        )
        return transcript
