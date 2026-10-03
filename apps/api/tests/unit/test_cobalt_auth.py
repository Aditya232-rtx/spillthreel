"""Cobalt auth-header contract (app/services/extractors/cobalt.py).

Two independent layers that must never share one header:
- `Authorization: Api-Key` — Cobalt's own key check (all environments).
- `X-Serverless-Authorization: Bearer` — Cloud Run IAM, stripped by the
  proxy before reaching the container (non-dev only).

Token fetching is stubbed at `_fetch_id_token_sync` so no Google
credentials are needed; failure paths raise instead of sending an
implicitly-unauthenticated request.
"""

from __future__ import annotations

import pytest

from app.services.extractors import cobalt as cobalt_mod
from app.services.extractors.base import ExtractorError
from app.settings import get_settings


@pytest.fixture()
def dev_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("COBALT_BASE_URL", "http://cobalt:9000")
    monkeypatch.setenv("COBALT_API_KEY", "dev-key")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture()
def staging_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("COBALT_BASE_URL", "https://cobalt-staging.example.run.app")
    monkeypatch.setenv("COBALT_API_KEY", "staging-key")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://staging.example.com")
    get_settings.cache_clear()
    # Token cache is module-global: clear both sides so tests can't leak
    # cached tokens into each other.
    cobalt_mod._id_token_cache.clear()
    yield get_settings()
    get_settings.cache_clear()
    cobalt_mod._id_token_cache.clear()


async def test_dev_sends_only_api_key(dev_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(audience: str) -> str:
        raise AssertionError("token fetch must not run in dev")

    monkeypatch.setattr(cobalt_mod, "_fetch_id_token_sync", _boom)
    headers = await cobalt_mod._auth_headers(dev_settings)
    assert headers["Authorization"] == "Api-Key dev-key"
    assert "X-Serverless-Authorization" not in headers


async def test_non_dev_sends_both_headers(
    staging_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cobalt_mod, "_fetch_id_token_sync", lambda audience: f"token-for-{audience}"
    )
    headers = await cobalt_mod._auth_headers(staging_settings)
    assert headers["Authorization"] == "Api-Key staging-key"
    assert (
        headers["X-Serverless-Authorization"]
        == "Bearer token-for-https://cobalt-staging.example.run.app"
    )


async def test_non_dev_token_failure_raises(
    staging_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(audience: str) -> str:
        raise RuntimeError("metadata server unreachable")

    monkeypatch.setattr(cobalt_mod, "_fetch_id_token_sync", _fail)
    with pytest.raises(ExtractorError, match="could not fetch GCP ID token"):
        await cobalt_mod._auth_headers(staging_settings)


async def test_token_cached_per_audience(staging_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _counting_fetch(audience: str) -> str:
        calls.append(audience)
        return "cached-token"

    monkeypatch.setattr(cobalt_mod, "_fetch_id_token_sync", _counting_fetch)
    first = await cobalt_mod._auth_headers(staging_settings)
    second = await cobalt_mod._auth_headers(staging_settings)
    assert calls == ["https://cobalt-staging.example.run.app"]
    assert (
        first["X-Serverless-Authorization"]
        == second["X-Serverless-Authorization"]
        == "Bearer cached-token"
    )
