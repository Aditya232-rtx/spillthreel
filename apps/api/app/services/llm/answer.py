"""AnswerModel — grounded RAG answerer for POST /v1/search.

Mirrors the GeminiSummaryModel pattern (services/llm/gemini.py): the
google-genai SDK import is deferred inside the method so the lean api
image never imports it at module load.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

# Exact sentence returned when there is nothing to ground on. Shared with
# services/search/pipeline.py so the zero-hits path is identical whether
# or not the LLM is ever called.
NO_MATCH_TEXT = "I couldn't find any matching saves for that query."

_SYSTEM_PROMPT = """\
You answer questions about the user's saved videos using ONLY the provided
hits. Rules:
- Ground every claim in the hits; cite each claim with [n] using the hit's
  1-based index. One citation per claim, inline.
- If there are zero hits, reply with exactly this sentence and no citations:
  "I couldn't find any matching saves for that query."
- Never invent titles, creators, URLs, or facts not present in the hits.
- Return a single JSON object with this shape:
  {"text": "<answer with [n] citations>", "citations": [{"item_id": "<id>", "index": <n>, "span": "<optional short quote>"}]}
"""

_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["text", "citations"],
    "properties": {
        "text": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["item_id", "index"],
                "properties": {
                    "item_id": {"type": "string"},
                    "index": {"type": "integer"},
                    "span": {"type": "string"},
                },
            },
        },
    },
}


class AnswerHit(BaseModel):
    """One numbered retrieval hit passed to the answerer as grounding."""

    item_id: str
    index: int  # 1-based position — matches [n] markers and cards order
    title: str | None = None
    snippet: str | None = None


class AnswerCitation(BaseModel):
    item_id: str
    index: int
    span: str | None = None


class AnswerResult(BaseModel):
    text: str
    citations: list[AnswerCitation] = Field(default_factory=list)


class AnswerModel(Protocol):
    @property
    def model_name(self) -> str: ...

    async def generate(self, query: str, hits: list[AnswerHit]) -> AnswerResult: ...


class GeminiAnswerModel:
    """AnswerModel Protocol impl (Flash-Lite, temperature ~0.2)."""

    def __init__(self, model: str | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.gemini_model_flash_lite

    @property
    def model_name(self) -> str:
        return self._model

    async def generate(self, query: str, hits: list[AnswerHit]) -> AnswerResult:
        if not hits:
            return AnswerResult(text=NO_MATCH_TEXT, citations=[])

        # Deferred so `import app.services.llm.answer` doesn't force the
        # SDK on the api container (worker image bundles google-genai;
        # api image doesn't need it until an answer is actually generated).
        from google import genai
        from google.genai import types as genai_types

        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("gemini_api_key not configured")

        client = genai.Client(api_key=settings.gemini_api_key)

        context_lines = []
        for hit in hits:
            title = hit.title or "Untitled save"
            snippet = f" — {hit.snippet}" if hit.snippet else ""
            context_lines.append(f"[{hit.index}] item_id={hit.item_id} title={title}{snippet}")
        user_prompt = f"Question: {query}\n\nSaves:\n" + "\n".join(context_lines)

        parts: list[Any] = [
            genai_types.Part.from_text(text=_SYSTEM_PROMPT),
            genai_types.Part.from_text(text=user_prompt),
        ]
        response = await client.aio.models.generate_content(
            model=self._model,
            contents=parts,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ANSWER_SCHEMA,
                # Low temperature: answers must stay grounded, not creative.
                temperature=0.2,
                max_output_tokens=2048,
            ),
        )

        text = response.text
        if not text:
            raise RuntimeError("gemini returned empty answer")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            _logger.warning("llm.answer.json_decode_failed", err=str(e), body=text[:200])
            raise RuntimeError("answer model returned invalid JSON") from e

        result = AnswerResult.model_validate(payload)
        _logger.info("llm.answer.generated", model=self._model, hits=len(hits))
        return result
