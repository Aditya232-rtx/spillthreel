"""Scraper provider parsing tests (Part D).

No real network: httpx MockTransport serves SANITIZED fixture JSON.
Covers video / single-image / carousel / deleted / quota per
provider x platform, plus retry + Retry-After + error taxonomy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.extractors.base import ExtractorError
from app.services.extractors.scraper import apify as apify_mod
from app.services.extractors.scraper import scrapecreators as sc_mod
from app.services.extractors.scraper.base import ProviderQuotaError, SourceGoneError

_FIX = Path(__file__).parent / "fixtures" / "scraper"


def _load(name: str) -> Any:
    return json.loads((_FIX / name).read_text())


def _mock_client(monkeypatch: pytest.MonkeyPatch, module: Any, handler: Any) -> None:
    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module, "_build_client", _factory)


def _json_response(
    status: int, payload: Any, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers or {})


def _raise_settings_unavailable() -> Any:
    """Stub for get_settings: simulate 'nothing configured via settings'.

    Consumers catch Exception and fall back to env-only, so absence tests
    stay hermetic against canonical settings defaults and the developer's
    local .env file (house pattern from test_egress_check.py).
    """
    raise ValueError("settings unavailable in this test")


class TestScrapeCreatorsInstagram:
    async def test_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_instagram_video.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        post = await sc_mod.ScrapeCreatorsProvider().fetch(
            "instagram", "https://www.instagram.com/reel/abc/"
        )
        assert post.media_type == "video"
        assert post.video_url == "https://cdn.example.com/ig/video123.mp4"
        assert post.caption == "Test reel caption #reel #test"
        assert post.owner_username == "testcreator"
        assert post.owner_name == "Test Creator"
        assert post.duration_seconds == 12.5

    async def test_single_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_instagram_image.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        post = await sc_mod.ScrapeCreatorsProvider().fetch(
            "instagram", "https://www.instagram.com/p/abc/"
        )
        assert post.media_type == "image"
        assert post.image_urls == ["https://cdn.example.com/ig/photo123.jpg"]

    async def test_carousel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_instagram_carousel.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        post = await sc_mod.ScrapeCreatorsProvider().fetch(
            "instagram", "https://www.instagram.com/p/abc/"
        )
        assert post.media_type == "carousel"
        assert "https://cdn.example.com/ig/car1.jpg" in post.image_urls
        # Mixed carousel keeps the child video URL for the video-wins rule.
        assert post.video_url == "https://cdn.example.com/ig/car_video.mp4"

    async def test_deleted_404_is_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_instagram_deleted.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(404, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        with pytest.raises(SourceGoneError):
            await sc_mod.ScrapeCreatorsProvider().fetch(
                "instagram", "https://www.instagram.com/p/gone/"
            )

    async def test_quota_402_is_never_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_instagram_quota.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(402, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        with pytest.raises(ProviderQuotaError):
            await sc_mod.ScrapeCreatorsProvider().fetch(
                "instagram", "https://www.instagram.com/p/abc/"
            )

    async def test_5xx_retries_once_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_instagram_video.json")
        calls = {"n": 0}

        def _handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return _json_response(500, {"error": "boom"})
            return _json_response(200, payload)

        _mock_client(monkeypatch, sc_mod, _handler)
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        post = await sc_mod.ScrapeCreatorsProvider().fetch(
            "instagram", "https://www.instagram.com/reel/abc/"
        )
        assert post.media_type == "video"
        assert calls["n"] == 2

    async def test_429_honors_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_instagram_video.json")
        seen: list[str | None] = []

        def _handler(req: httpx.Request) -> httpx.Response:
            if not seen:
                seen.append("first")
                return _json_response(429, {"error": "slow"}, {"retry-after": "0"})
            return _json_response(200, payload)

        _mock_client(monkeypatch, sc_mod, _handler)
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        post = await sc_mod.ScrapeCreatorsProvider().fetch(
            "instagram", "https://www.instagram.com/reel/abc/"
        )
        assert post.media_type == "video"
        assert seen == ["first"]

    async def test_missing_key_is_transient_not_quota(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SCRAPECREATORS_API_KEY", raising=False)
        # Settings also reads apps/api/.env + ships canonical defaults, so
        # env absence alone doesn't prove "not configured" — stub the
        # settings boundary (house pattern from test_egress_check.py).
        import app.settings as settings_mod

        monkeypatch.setattr(settings_mod, "get_settings", _raise_settings_unavailable)
        with pytest.raises(ExtractorError) as exc:
            await sc_mod.ScrapeCreatorsProvider().fetch(
                "instagram", "https://www.instagram.com/p/abc/"
            )
        assert not isinstance(exc.value, (SourceGoneError, ProviderQuotaError))

    async def test_error_text_never_echoes_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(500, {"e": 1}))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "super-secret-key")
        with pytest.raises(ExtractorError) as exc:
            await sc_mod.ScrapeCreatorsProvider().fetch(
                "instagram", "https://www.instagram.com/p/abc/"
            )
        assert "super-secret-key" not in str(exc.value)


class TestScrapeCreatorsTikTok:
    async def test_video_prefers_no_watermark(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_tiktok_video.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        post = await sc_mod.ScrapeCreatorsProvider().fetch(
            "tiktok", "https://www.tiktok.com/@u/video/123"
        )
        assert post.media_type == "video"
        assert post.video_url == "https://cdn.example.com/tt/no_watermark.mp4"
        assert post.caption == "Test tiktok caption #fyp"
        assert post.owner_username == "testcreator"
        # 15000 (ms-looking) coerces to 15s.
        assert post.duration_seconds == 15.0

    async def test_carousel_slideshow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_tiktok_carousel.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        post = await sc_mod.ScrapeCreatorsProvider().fetch(
            "tiktok", "https://www.tiktok.com/@u/video/123"
        )
        assert post.media_type == "carousel"
        assert len(post.image_urls) == 3

    async def test_deleted_is_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_tiktok_deleted.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(404, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        with pytest.raises(SourceGoneError):
            await sc_mod.ScrapeCreatorsProvider().fetch(
                "tiktok", "https://www.tiktok.com/@u/video/999"
            )

    async def test_quota_401_is_provider_quota(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("scrapecreators_tiktok_quota.json")
        _mock_client(monkeypatch, sc_mod, lambda req: _json_response(401, payload))
        monkeypatch.setenv("SCRAPECREATORS_API_KEY", "test-key")
        with pytest.raises(ProviderQuotaError):
            await sc_mod.ScrapeCreatorsProvider().fetch(
                "tiktok", "https://www.tiktok.com/@u/video/123"
            )


class TestApify:
    async def test_instagram_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_instagram_video.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_IG_ACTOR", "someone~some-actor")
        post = await apify_mod.ApifyProvider().fetch(
            "instagram", "https://www.instagram.com/p/abc/"
        )
        assert post.media_type == "video"
        assert post.video_url == "https://cdn.example.com/apify/ig_video.mp4"

    async def test_instagram_carousel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_instagram_carousel.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_IG_ACTOR", "someone~some-actor")
        post = await apify_mod.ApifyProvider().fetch(
            "instagram", "https://www.instagram.com/p/abc/"
        )
        assert post.media_type == "carousel"
        assert post.video_url == "https://cdn.example.com/apify/car_video.mp4"

    async def test_instagram_single_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_instagram_image.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_IG_ACTOR", "someone~some-actor")
        post = await apify_mod.ApifyProvider().fetch(
            "instagram", "https://www.instagram.com/p/abc/"
        )
        assert post.media_type == "image"

    async def test_instagram_deleted_is_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_instagram_deleted.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_IG_ACTOR", "someone~some-actor")
        with pytest.raises(SourceGoneError):
            await apify_mod.ApifyProvider().fetch("instagram", "https://www.instagram.com/p/gone/")

    async def test_tiktok_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_tiktok_video.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_TIKTOK_ACTOR", "someone~some-actor")
        post = await apify_mod.ApifyProvider().fetch(
            "tiktok", "https://www.tiktok.com/@u/video/123"
        )
        assert post.media_type == "video"
        assert post.video_url == "https://cdn.example.com/apify/tt_video.mp4"

    async def test_tiktok_carousel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_tiktok_carousel.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_TIKTOK_ACTOR", "someone~some-actor")
        post = await apify_mod.ApifyProvider().fetch(
            "tiktok", "https://www.tiktok.com/@u/video/123"
        )
        assert post.media_type == "carousel"
        assert len(post.image_urls) == 2

    async def test_tiktok_deleted_is_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_tiktok_deleted.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(200, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_TIKTOK_ACTOR", "someone~some-actor")
        with pytest.raises(SourceGoneError):
            await apify_mod.ApifyProvider().fetch("tiktok", "https://www.tiktok.com/@u/video/999")

    async def test_quota_402_is_provider_quota(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = _load("apify_quota.json")
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(402, payload))
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.setenv("APIFY_IG_ACTOR", "someone~some-actor")
        with pytest.raises(ProviderQuotaError):
            await apify_mod.ApifyProvider().fetch("instagram", "https://www.instagram.com/p/abc/")

    async def test_missing_actor_is_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APIFY_TOKEN", "test-token")
        monkeypatch.delenv("APIFY_IG_ACTOR", raising=False)
        # settings.py ships canonical actor defaults, so env absence alone
        # doesn't prove "not configured" — stub the settings boundary.
        import app.settings as settings_mod

        monkeypatch.setattr(settings_mod, "get_settings", _raise_settings_unavailable)
        with pytest.raises(ExtractorError) as exc:
            await apify_mod.ApifyProvider().fetch("instagram", "https://www.instagram.com/p/abc/")
        assert not isinstance(exc.value, (SourceGoneError, ProviderQuotaError))

    async def test_token_never_in_error_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_client(monkeypatch, apify_mod, lambda req: _json_response(500, []))
        monkeypatch.setenv("APIFY_TOKEN", "super-secret-token")
        monkeypatch.setenv("APIFY_IG_ACTOR", "someone~some-actor")
        with pytest.raises(ExtractorError) as exc:
            await apify_mod.ApifyProvider().fetch("instagram", "https://www.instagram.com/p/abc/")
        assert "super-secret-token" not in str(exc.value)
