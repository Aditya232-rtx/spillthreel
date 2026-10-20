"""Runtime configuration for the SpillTheReel API and worker.

Every environment variable the app reads is declared here — missing required
vars fail-fast at import time, so deploys never get partway through boot
before crashing on a lookup deep in a request handler.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
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
    supabase_secret_key: str | None = Field(
        default=None,
        description=(
            "New-format secret API key (sb_secret_…). SECRET — backend only. "
            "Required only by Auth Admin and Storage operations; bypasses RLS."
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
    cloud_tasks_oidc_sa_email: str | None = Field(
        default=None,
        description=(
            "SA email that mints Cloud Tasks OIDC tokens — the api Cloud "
            "Run runtime SA. Required outside dev when WORKER_BASE_URL is set."
        ),
    )
    worker_allowed_sa_emails: list[str] = Field(
        default_factory=list,
        description=(
            "Exact Google service-account emails allowed to invoke worker routes. "
            "Required outside dev; configured as a JSON array."
        ),
    )
    worker_base_url: str | None = Field(
        default=None,
        description="Cloud Run worker service URL — Cloud Tasks POSTs here",
    )
    worker_max_active_items_per_owner: int = Field(
        default=2,
        gt=0,
        le=32,
        description=(
            "Maximum concurrently leased ingest/enhance item jobs per active owner. "
            "Workers return retryable capacity responses until a slot is free."
        ),
    )

    # Bulk imports remain fail-closed until the account-history and policy
    # decisions are explicitly approved and reconciled. These values are
    # server-only; clients cannot choose a tier or policy.
    bulk_import_policy_version: str | None = None
    bulk_import_history_reconciled: bool = False
    bulk_import_premium_history_mode: Literal["carry_forward", "separate_allowance"] | None = None
    bulk_import_existing_item_mode: Literal["exclude", "admit"] | None = None
    bulk_import_manual_share_mode: Literal["exclude", "admit"] | None = None
    bulk_import_saved_audio_mode: Literal["exclude", "admit"] | None = None
    # The owner delegated the preview-only retention choice on 2026-10-20:
    # uncommitted previews expire after one hour, then metadata is purged and
    # the private ZIP is removed by the existing staging grace/sweeper.
    # This does not decide retention for completed imports or deletion tombstones.
    import_preview_retention_policy_version: str | None = "preview-retention-1h-v1"
    import_preview_ttl_seconds: int | None = Field(
        default=60 * 60, gt=0, le=7 * 24 * 60 * 60
    )

    # ---------------- Bulk import archive safety ----------------
    # These are hard resource ceilings, separate from once-per-account
    # admission allowances and from any owner-approved retention policy.
    import_max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0, le=200 * 1024 * 1024)
    import_max_archive_members: int = Field(default=10_000, gt=0, le=50_000)
    import_max_member_uncompressed_bytes: int = Field(
        default=64 * 1024 * 1024, gt=0, le=512 * 1024 * 1024
    )
    import_max_uncompressed_bytes: int = Field(
        default=250 * 1024 * 1024, gt=0, le=1024 * 1024 * 1024
    )
    import_max_compression_ratio: float = Field(default=200.0, gt=1.0, le=1000.0)
    import_max_parsed_entries: int = Field(default=10_000, gt=0, le=50_000)
    import_parse_timeout_seconds: float = Field(default=30.0, gt=0.0, le=120.0)

    @field_validator("worker_base_url", mode="before")
    @classmethod
    def _empty_worker_base_url_is_unset(cls, v: str | None) -> str | None:
        """Treat empty/blank WORKER_BASE_URL as unset.

        `.env.example` ships `WORKER_BASE_URL=` (empty string), which
        Pydantic would otherwise keep as `""` — and `enqueue_task`
        checks `worker_base_url is None` for the dev local-dispatch
        shortcut. Normalizing here keeps "" behaving as unset
        everywhere without touching the dispatch check.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # ---------------- Media downloader (Part C) ----------------
    # SSRF allowlist + per-kind size caps + timeouts for
    # services/media/download.py. The downloader also honors
    # MEDIA_HOST_ALLOWLIST / MEDIA_MAX_*_MB / MEDIA_CONNECT_TIMEOUT_S env
    # vars as fallback; empty settings values fall through to env, then
    # to the module defaults.
    media_host_allowlist: str = (
        "cdninstagram.com,fbcdn.net,"
        "tiktokcdn.com,tiktokcdn-us.com,byteoversea.com,muscdn.com,"
        "tiktokv.com,googlevideo.com,ytimg.com,youtube.com,youtu.be,"
        "ggpht.com,twimg.com"
    )
    media_max_video_mb: float = 100.0
    media_max_image_mb: float = 20.0
    media_max_audio_mb: float = 20.0
    media_connect_timeout_s: float = 10.0
    # Read/idle timeout for media fetches. download_media() takes it as
    # the `timeout_s` param (default 60.0); this field is the documented
    # home for the value when callers want it centralized.
    media_read_timeout_s: float = 60.0

    # ---------------- Paid scraper providers (Part D) ----------------
    # SCRAPER_PROVIDER=none (default) is boot-safe: the scraper head
    # degrades to can_handle False with zero paid calls. Set to
    # "scrapecreators" | "apify" | "brightdata" to enable the paid head
    # on IG/TikTok.
    scraper_provider: str = "none"
    scrapecreators_api_key: str | None = None
    scrapecreators_base_url: str = "https://api.scrapecreators.com"
    apify_api_token: str | None = None
    apify_base_url: str = "https://api.apify.com"
    apify_ig_actor: str = "apify/instagram-scraper"
    apify_tiktok_actor: str = "clockworks/tiktok-scraper"
    # Bright Data Web Scraper API (Bearer token; no zone/customer needed —
    # those belong to Native Access proxy auth, not the Bearer scraper
    # API). Dataset IDs are documented public collectors
    # (see services/extractors/scraper/brightdata.py docstring), NOT
    # secrets — env-overridable with documented defaults.
    brightdata_api_token: str | None = None
    brightdata_base_url: str = "https://api.brightdata.com"
    brightdata_ig_post_dataset: str = "gd_lk5ns7kz21pck8jpis"
    brightdata_ig_reel_dataset: str = "gd_lyclm20il4r5helnj"
    brightdata_tiktok_dataset: str = "gd_lu702nij2f790tmv9h"
    scraper_max_concurrency: int = 4
    scraper_cost_per_1k_usd: str = ""
    # Egress proxy for paid-provider API calls (scrapecreators/apify/brightdata).
    # Deliberately NOT honored by services/media/download.py: that
    # downloader pins DNS per-hop as its DNS-rebinding defense, which is
    # incompatible with proxying — it egresses direct to allowlisted
    # CDN hosts only.
    outbound_proxy_url: str | None = None
    # Dedicated policy-enforcing egress proxy required by yt-dlp/gallery-dl.
    # This is deliberately separate from OUTBOUND_PROXY_URL: that setting is
    # for fixed paid-provider API endpoints and cannot be treated as an SSRF
    # boundary for arbitrary source/CDN URLs.
    extractor_egress_proxy_url: str | None = Field(
        default=None,
        description=(
            "Worker-only, SSRF-enforcing egress proxy for yt-dlp/gallery-dl. "
            "Extractors fail closed when it is unset."
        ),
    )

    # ---------------- YouTube native ingest (Part E) ----------------
    youtube_api_key: str | None = None
    youtube_native_enabled: bool = True
    youtube_max_duration_seconds: float = 600.0
    youtube_gemini_media_resolution: str = "MEDIA_RESOLUTION_LOW"

    # ---------------- LLM providers ----------------
    # `enterprise` uses Vertex with Application Default Credentials;
    # `vertex_api_key` uses a Google Cloud API key restricted to aiplatform;
    # `developer_api` uses the AI Studio Gemini Developer API key.
    gemini_backend: Literal["developer_api", "enterprise", "vertex_api_key"] = "developer_api"
    gemini_location: str = "global"
    gemini_api_key: str | None = None
    gemini_model_flash_lite: str = "gemini-3.5-flash-lite"
    gemini_model_flash: str = "gemini-3.8-flash"
    gemini_model_pro: str = "gemini-3.1-pro-preview"

    groq_api_key: str | None = None
    groq_asr_model: str = "whisper-large-v3-turbo"

    # ---------------- Observability ----------------
    sentry_dsn: str | None = None
    sentry_release: str | None = Field(
        default=None,
        description=(
            "Release tag for Sentry (e.g. git SHA or mobile-v1.0.0). "
            "Falls back to GIT_SHA at init time; unset means unversioned."
        ),
    )

    # ---------------- Cost guards (per TRD §14.3) ----------------
    per_user_daily_gemini_budget_usd: float = 0.50
    per_user_daily_gemini_hard_cap_usd: float = 1.00

    # ---------------- Rate limiting (slowapi) ----------------
    # slowapi limit strings, e.g. "60/minute". Read per-request (via a
    # callable in app/ratelimit.py) so tests can tighten them with env
    # overrides without rebuilding the app.
    rate_limit_default: str = "60/minute"
    rate_limit_saves: str = "10/minute"

    # ---------------- CORS ----------------
    # Comma-separated origins for staging/prod, e.g.
    # "https://app.spillthereel.app,https://admin.spillthereel.app".
    # Dev keeps "*" (see main.py). Required outside dev — boot fails
    # fast if unset.
    cors_allowed_origins: str = ""

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
    return Settings()  # env vars populate required fields
