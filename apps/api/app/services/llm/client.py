"""Shared Google Gen AI client construction for supported backends.

Imports the SDK lazily so importing API modules does not require the worker's
Google Gen AI dependency. Enterprise mode uses Application Default
Credentials; Developer API and Vertex API-key modes use the explicitly
configured API key.
"""

from __future__ import annotations

from typing import Any


def create_genai_client(settings: Any, *, backend: str | None = None) -> Any:
    """Create a Google Gen AI client from the configured backend settings.

    ``gemini_backend`` is kept duck-typed so this module remains importable in
    the lean API image. Settings defines the supported values and validation.
    """
    backend = backend or getattr(settings, "gemini_backend", "developer_api")
    if backend == "developer_api":
        api_key = getattr(settings, "gemini_api_key", None)
        if not api_key:
            raise RuntimeError("gemini_api_key not configured")
        from google import genai

        return genai.Client(api_key=api_key)

    if backend == "enterprise":
        project = getattr(settings, "gcp_project_id", None)
        region = getattr(settings, "gemini_location", None)
        if not project:
            raise RuntimeError("gcp_project_id not configured for Gemini enterprise backend")
        if not region:
            raise RuntimeError("gemini_location not configured for Gemini enterprise backend")
        from google import genai

        # Google Gen AI SDK uses ADC for Enterprise / Vertex AI when no api_key
        # is passed. Do not fall back to the Developer API key on this branch.
        return genai.Client(enterprise=True, project=project, location=region)

    if backend == "vertex_api_key":
        api_key = getattr(settings, "gemini_api_key", None)
        if not api_key:
            raise RuntimeError("gemini_api_key not configured")
        from google import genai

        # Vertex AI Express mode uses a standard Google Cloud API key. Do not
        # pass project/location here: the SDK otherwise prefers ADC-backed
        # project mode and ignores the API-key authentication path.
        return genai.Client(vertexai=True, api_key=api_key)

    raise RuntimeError(
        "gemini_backend must be 'developer_api', 'enterprise', or 'vertex_api_key'"
    )


def create_embedding_client(settings: Any) -> Any:
    """Create an embedding-capable client for the selected Gemini setup.

    Vertex AI Express API-key mode supports generation but not embeddings.
    Keep generation on the configured key while routing embeddings through the
    runtime's ADC-backed full Vertex AI endpoint.
    """
    if getattr(settings, "gemini_backend", "developer_api") == "vertex_api_key":
        return create_genai_client(settings, backend="enterprise")
    return create_genai_client(settings)


def generation_temperature(model: str, temperature: float) -> float | None:
    """Retain legacy sampling behavior, but use Gemini 3.x defaults.

    Google recommends leaving temperature at its model default for Gemini 3.x.
    ``None`` means callers should omit the field entirely.
    """
    model_id = model.rsplit("/", 1)[-1].lower()
    if model_id.startswith(("gemini-3.", "gemini-3-")):
        return None
    return temperature
