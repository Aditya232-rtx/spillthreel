"""Unit tests for Settings normalization (app/settings.py).

Regression (local-loop finding): `.env.example` ships `WORKER_BASE_URL=`
(empty string). Pydantic kept it as `""`, so `enqueue_task`'s dev
local-dispatch shortcut (`worker_base_url is None`) missed and dev fell
through to the Cloud Tasks path (awaited inline fallback instead of the
fire-and-forget background task). Empty/blank now normalizes to None at
the settings boundary, so "" behaves as unset everywhere.
"""

from __future__ import annotations

from typing import Any

from app.settings import Settings

_BASE: dict[str, Any] = {
    "supabase_url": "https://test.supabase.co",
    "supabase_publishable_key": "sb_publishable_test",
    "supabase_secret_key": "sb_secret_test",
    "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
}


def _settings(**overrides: Any) -> Settings:
    base = dict(_BASE)
    base.update(overrides)
    return Settings(**base)


class TestWorkerBaseUrlEmptyString:
    def test_empty_string_is_none(self) -> None:
        assert _settings(worker_base_url="").worker_base_url is None

    def test_blank_whitespace_is_none(self) -> None:
        assert _settings(worker_base_url="   ").worker_base_url is None

    def test_unset_is_none(self) -> None:
        assert _settings().worker_base_url is None

    def test_explicit_none_is_none(self) -> None:
        assert _settings(worker_base_url=None).worker_base_url is None

    def test_real_url_preserved(self) -> None:
        url = "https://worker-abc123-uc.a.run.app"
        assert _settings(worker_base_url=url).worker_base_url == url


def test_extractor_egress_proxy_is_separate_from_provider_proxy() -> None:
    assert _settings().extractor_egress_proxy_url is None
    assert (
        _settings(extractor_egress_proxy_url="http://egress-proxy.internal:8080")
        .extractor_egress_proxy_url
        == "http://egress-proxy.internal:8080"
    )
    assert _settings(outbound_proxy_url="http://provider-proxy.internal:8080").extractor_egress_proxy_url is None


def test_supabase_service_key_is_optional_for_database_only_runtimes() -> None:
    assert _settings().supabase_secret_key == "sb_secret_test"
    assert _settings(supabase_secret_key=None).supabase_secret_key is None
