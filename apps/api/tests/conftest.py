"""Shared pytest fixtures + test settings scaffolding.

Sets required env vars BEFORE `app.settings` is first imported so
Pydantic Settings doesn't raise on missing required fields.
"""

from __future__ import annotations

import os

import pytest

# Populate the minimum-required env vars so Settings() can instantiate.
# Real values live in per-test fixtures when they matter.
os.environ.setdefault("ENVIRONMENT", "dev")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_test")
os.environ.setdefault("SUPABASE_SECRET_KEY", "sb_secret_test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres",
)


@pytest.fixture(autouse=True)
def _fresh_settings() -> object:
    """Rebuild the cached Settings around every test.

    Pydantic populates fields from env at construction, so a test that
    mutates env (setenv/delenv) must not see a singleton built under a
    different test's env. Clearing before + after keeps both directions
    hermetic — including against the developer's local .env file, which
    Settings also reads.
    """
    from app.settings import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
