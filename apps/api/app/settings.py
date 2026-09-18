"""Runtime configuration for the SpillTheReel API and worker.

Every environment variable the app reads is declared here — missing required
vars fail-fast at import time, so deploys never get partway through boot
before crashing on a lookup deep in a request handler.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from env vars.

    Naming: SCREAMING_SNAKE_CASE in the env, snake_case here. Fields marked
    `= ...` are required (Pydantic raises on missing).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # ---------------- Runtime ----------------
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARN", "ERROR"] = "INFO"

    # ---------------- Auth ----------------
    firebase_project_id: str = Field(..., description="Firebase project id")
    firebase_credentials_json: str | None = Field(
        default=None,
        description=(
            "JSON service-account credentials for Firebase Admin. "
            "If unset, falls back to Application Default Credentials "
            "(GOOGLE_APPLICATION_CREDENTIALS)."
        ),
    )

    # ---------------- Persistence ----------------
    database_url: str = Field(
        ...,
        description="Postgres SQLAlchemy async URL (postgresql+asyncpg://...)",
    )
    database_echo: bool = False

    # ---------------- GCP ----------------
    gcp_project_id: str | None = None
    gcp_region: str = "us-central1"
    gcs_bucket_media: str | None = None

    # Cloud Tasks — three queues per architecture.md
    cloud_tasks_queue_ingest: str = "ingest"
    cloud_tasks_queue_import: str = "import"
    cloud_tasks_queue_enhance: str = "enhance"
    worker_base_url: str | None = Field(
        default=None,
        description="Cloud Run worker service URL — Cloud Tasks POSTs here",
    )

    # ---------------- Extractors ----------------
    cobalt_base_url: str | None = None
    cobalt_api_key: str | None = None

    # ---------------- LLM providers ----------------
    gemini_api_key: str | None = None
    gemini_model_flash_lite: str = "gemini-2.5-flash-lite"
    gemini_model_flash: str = "gemini-2.5-flash"
    gemini_model_pro: str = "gemini-2.5-pro"

    groq_api_key: str | None = None
    groq_asr_model: str = "whisper-large-v3-turbo"

    # ---------------- Memory layer ----------------
    cognee_api_key: str | None = None
    cognee_base_url: str = "https://api.cognee.ai"

    # ---------------- Observability ----------------
    sentry_dsn: str | None = None

    # ---------------- Cost guards (per TRD §14.3) ----------------
    per_user_daily_gemini_budget_usd: float = 0.50
    per_user_daily_gemini_hard_cap_usd: float = 1.00


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Import via `from app.settings import get_settings`."""
    return Settings()  # type: ignore[call-arg]  # env vars populate required fields
