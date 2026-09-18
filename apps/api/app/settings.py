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
    `= Field(...)` are required (Pydantic raises on missing).
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

    # ---------------- Supabase — Auth + Storage ----------------
    supabase_url: str = Field(
        ...,
        description="Base URL, e.g. https://<project-ref>.supabase.co",
    )
    supabase_publishable_key: str = Field(
        ...,
        description=(
            "New-format publishable API key (sb_publishable_…). "
            "Public — safe in mobile bundle. RLS gates all access."
        ),
    )
    supabase_secret_key: str = Field(
        ...,
        description=(
            "New-format secret API key (sb_secret_…). "
            "SECRET — backend only. Bypasses RLS."
        ),
    )
    supabase_legacy_jwt_secret: str | None = Field(
        default=None,
        description=(
            "Legacy HS256 JWT secret. Optional. Only set if the project "
            "still has a legacy secret active (older projects, or during "
            "a rotation window). New projects use asymmetric JWTs and can "
            "leave this blank."
        ),
    )
    supabase_bucket_media: str = "media"
    supabase_bucket_exports: str = "exports"

    # ---------------- Persistence (Supabase Postgres) ----------------
    database_url: str = Field(
        ...,
        description=(
            "Pooler URL on :6543 for app connections. "
            "postgresql+asyncpg://postgres.<ref>:<pw>@aws-0-<region>."
            "pooler.supabase.com:6543/postgres"
        ),
    )
    database_url_direct: str | None = Field(
        default=None,
        description="Direct :5432 URL — Alembic only, poolers don't like DDL.",
    )
    database_echo: bool = False

    # ---------------- GCP ----------------
    gcp_project_id: str | None = None
    gcp_region: str = "us-central1"

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

    # ---------------- Derived helpers ----------------
    @property
    def supabase_jwks_url(self) -> str:
        """Public JWKS endpoint used to verify asymmetric session JWTs.

        Cached by the JWT middleware for ~10 minutes (matching Supabase's
        edge-cache TTL) so verification stays a pure in-process call after
        first request warmup.
        """
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Import via `from app.settings import get_settings`."""
    return Settings()  # type: ignore[call-arg]  # env vars populate required fields
