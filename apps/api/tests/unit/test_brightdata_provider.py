"""Bright Data provider tests (Part D).

No real network: httpx MockTransport routes trigger → progress →
snapshot with SANITIZED fixtures under fixtures/scraper/brightdata/.
Covers video / image / carousel / dead_page / quota / 5xx / 429 /
poll-budget timeout / provider selection, plus the error taxonomy
(SourceGone vs ProviderQuota vs transient).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.extractors.base import ExtractorError
from app.services.extractors.scraper import brightdata as bd_mod
from app.services.extractors.scraper import scraper_extractor as se_mod
from app.services.extractors.scraper.base import ProviderQuotaError, SourceGoneError
from app.services.extractors.scraper.brightdata import BrightDataProvider
from app.services.extractors.scraper.scraper_extractor import ScraperApiExtractor

_FIX = Path(__file__).parent / "fixtures" / "scraper" / "brightdata"

_TEST_POST_DATASET = "gd_test_ig_post"
_TEST_REEL_DATASET = "gd_test_ig_reel"
_TEST_TIKTOK_DATASET = "gd_test_tiktok"


def _load(name: str) -> Any:
    return json.loads((_FIX / name).read_text())


def _mock_client(monkeypatch: pytest.MonkeyPatch, module: Any, handler: Any) -> None:
    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(module, "_build_client", _factory)


def _resp(status: int, payload: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    if isinstance(payload, str):
        return httpx.Response(status, text=payload, headers=headers or {})
    return httpx.Response(status, json=payload, headers=headers or {})


def _route(
    *,
    trigger_status: int = 200,
    trigger_payload: Any | None = None,
    progress_status: int = 200,
    progress_payload: Any | None = None,
    snapshot_status: int = 200,
    snapshot_payload: Any | None = None,
    seen: list[httpx.Request] | None = None,
) -> Any:
    trigger_payload = {"snapshot_id": "s_test123"} if trigger_payload is None else trigger_payload
    progress_payload = (
        {"snapshot_id": "s_test123", "status": "ready"}
        if progress_payload is None
        else progress_payload
    )
    if snapshot_payload is None:
        snapshot_payload = []

    def _handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        path = req.url.path
        if path.endswith("/datasets/v3/trigger"):
            return _resp(trigger_status, trigger_payload)
        if "/datasets/v3/progress/" in path:
            return _resp(progress_status, progress_payload)
        if "/datasets/v3/snapshot/" in path:
            return _resp(snapshot_status, snapshot_payload)
        return _resp(404, {"error": "unexpected path in test"})

    return _handler


def _set_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "test-token")
    monkeypatch.setenv("BRIGHTDATA_IG_POST_DATASET", _TEST_POST_DATASET)
    monkeypatch.setenv("BRIGHTDATA_IG_REEL_DATASET", _TEST_REEL_DATASET)
    monkeypatch.setenv("BRIGHTDATA_TIKTOK_DATASET", _TEST_TIKTOK_DATASET)


def _raise_settings_unavailable() -> Any:
    """Stub for get_settings: simulate 'nothing configured via settings'."""
    raise ValueError("settings unavailable in this test")


class TestBrightDataInstagram:
    async def test_reel_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _load("ig_reel_video.json")
        seen: list[httpx.Request] = []
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(snapshot_payload=records, seen=seen),
        )
        _set_env(monkeypatch)
        post = await BrightDataProvider().fetch(
            "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
        )
        assert post.media_type == "video"
        assert post.video_url == "https://cdn.example.com/brightdata/ig_video123.mp4"
        assert post.caption == "Test reel caption #reel #test"
        assert post.owner_username == "testcreator"
        assert post.duration_seconds == 12.5
        # Reel URLs route to the reels collector, not the posts collector.
        trigger_reqs = [r for r in seen if r.url.path.endswith("/datasets/v3/trigger")]
        assert len(trigger_reqs) == 1
        assert trigger_reqs[0].url.params["dataset_id"] == _TEST_REEL_DATASET

    async def test_post_image(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _load("ig_post_image.json")
        seen: list[httpx.Request] = []
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(snapshot_payload=records, seen=seen),
        )
        _set_env(monkeypatch)
        post = await BrightDataProvider().fetch(
            "instagram", "https://www.instagram.com/p/PhOTo123/"
        )
        assert post.media_type == "image"
        assert post.image_urls == ["https://cdn.example.com/brightdata/ig_photo123.jpg"]
        trigger_reqs = [r for r in seen if r.url.path.endswith("/datasets/v3/trigger")]
        assert trigger_reqs[0].url.params["dataset_id"] == _TEST_POST_DATASET

    async def test_post_carousel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _load("ig_post_carousel.json")
        _mock_client(monkeypatch, bd_mod, _route(snapshot_payload=records))
        _set_env(monkeypatch)
        post = await BrightDataProvider().fetch("instagram", "https://www.instagram.com/p/CaR456/")
        assert post.media_type == "carousel"
        assert len(post.image_urls) == 3
        assert "https://cdn.example.com/brightdata/ig_car1.jpg" in post.image_urls

    async def test_deleted_dead_page_is_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _load("deleted_dead_page.json")
        _mock_client(monkeypatch, bd_mod, _route(snapshot_payload=records))
        _set_env(monkeypatch)
        with pytest.raises(SourceGoneError):
            await BrightDataProvider().fetch("instagram", "https://www.instagram.com/p/gone123/")

    async def test_empty_snapshot_is_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_client(monkeypatch, bd_mod, _route(snapshot_payload=[]))
        _set_env(monkeypatch)
        with pytest.raises(SourceGoneError):
            await BrightDataProvider().fetch("instagram", "https://www.instagram.com/p/gone123/")

    @pytest.mark.parametrize("status", [401, 402, 403])
    async def test_quota_status_is_provider_quota(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(trigger_status=status, trigger_payload={"error": "Invalid credentials"}),
        )
        _set_env(monkeypatch)
        with pytest.raises(ProviderQuotaError) as exc:
            await BrightDataProvider().fetch(
                "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
            )
        assert not isinstance(exc.value, SourceGoneError)

    async def test_trigger_5xx_retries_once_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        records = _load("ig_reel_video.json")
        calls = {"n": 0}

        def _handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if path.endswith("/datasets/v3/trigger"):
                calls["n"] += 1
                if calls["n"] == 1:
                    return _resp(500, {"error": "boom"})
                return _resp(200, {"snapshot_id": "s_test123"})
            if "/datasets/v3/progress/" in path:
                return _resp(200, {"snapshot_id": "s_test123", "status": "ready"})
            if "/datasets/v3/snapshot/" in path:
                return _resp(200, records)
            return _resp(404, {"error": "unexpected"})

        _mock_client(monkeypatch, bd_mod, _handler)
        _set_env(monkeypatch)
        post = await BrightDataProvider().fetch(
            "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
        )
        assert post.media_type == "video"
        assert calls["n"] == 2

    async def test_snapshot_5xx_twice_is_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_client(monkeypatch, bd_mod, _route(snapshot_status=500, snapshot_payload={"e": 1}))
        _set_env(monkeypatch)
        with pytest.raises(ExtractorError) as exc:
            await BrightDataProvider().fetch(
                "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
            )
        assert not isinstance(exc.value, (SourceGoneError, ProviderQuotaError))

    async def test_429_honors_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _load("ig_reel_video.json")
        seen: list[str] = []

        def _handler(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/datasets/v3/trigger"):
                if not seen:
                    seen.append("first")
                    return _resp(429, {"error": "slow"}, {"retry-after": "0"})
                return _resp(200, {"snapshot_id": "s_test123"})
            if "/datasets/v3/progress/" in req.url.path:
                return _resp(200, {"snapshot_id": "s_test123", "status": "ready"})
            return _resp(200, records)

        _mock_client(monkeypatch, bd_mod, _handler)
        _set_env(monkeypatch)
        post = await BrightDataProvider().fetch(
            "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
        )
        assert post.media_type == "video"
        assert seen == ["first"]

    async def test_poll_timeout_is_transient_not_gone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(progress_payload={"snapshot_id": "s_test123", "status": "running"}),
        )
        _set_env(monkeypatch)
        monkeypatch.setattr(bd_mod, "_POLL_BUDGET_S", 0.05)
        monkeypatch.setattr(bd_mod, "_POLL_INTERVAL_S", 0.01)
        with pytest.raises(ExtractorError) as exc:
            await BrightDataProvider().fetch(
                "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
            )
        assert not isinstance(exc.value, (SourceGoneError, ProviderQuotaError))

    async def test_poll_failed_is_transient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(progress_payload={"snapshot_id": "s_test123", "status": "failed"}),
        )
        _set_env(monkeypatch)
        with pytest.raises(ExtractorError) as exc:
            await BrightDataProvider().fetch(
                "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
            )
        assert not isinstance(exc.value, (SourceGoneError, ProviderQuotaError))

    async def test_trigger_404_is_transient_not_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Vendor 404 on trigger = bad dataset_id (config), NOT a deleted post.
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(trigger_status=404, trigger_payload="dataset does not exist"),
        )
        _set_env(monkeypatch)
        with pytest.raises(ExtractorError) as exc:
            await BrightDataProvider().fetch(
                "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
            )
        assert not isinstance(exc.value, (SourceGoneError, ProviderQuotaError))

    async def test_missing_token_is_transient_not_quota(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BRIGHTDATA_API_TOKEN", raising=False)
        import app.settings as settings_mod

        monkeypatch.setattr(settings_mod, "get_settings", _raise_settings_unavailable)
        with pytest.raises(ExtractorError) as exc:
            await BrightDataProvider().fetch(
                "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
            )
        assert not isinstance(exc.value, (SourceGoneError, ProviderQuotaError))

    async def test_token_never_in_error_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_client(monkeypatch, bd_mod, _route(snapshot_status=500, snapshot_payload={"e": 1}))
        monkeypatch.setenv("BRIGHTDATA_API_TOKEN", "super-secret-token")
        with pytest.raises(ExtractorError) as exc:
            await BrightDataProvider().fetch(
                "instagram", "https://www.instagram.com/reel/AbC123XyZ/"
            )
        assert "super-secret-token" not in str(exc.value)


class TestBrightDataTikTok:
    async def test_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _load("tiktok_video.json")
        seen: list[httpx.Request] = []
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(snapshot_payload=records, seen=seen),
        )
        _set_env(monkeypatch)
        post = await BrightDataProvider().fetch(
            "tiktok", "https://www.tiktok.com/@testcreator/video/1234567890123456789"
        )
        assert post.media_type == "video"
        assert post.video_url == "https://cdn.example.com/brightdata/tt_video123.mp4"
        assert post.caption == "Test tiktok caption #fyp"
        assert post.owner_username == "testcreator"
        assert post.duration_seconds == 15
        trigger_reqs = [r for r in seen if r.url.path.endswith("/datasets/v3/trigger")]
        assert trigger_reqs[0].url.params["dataset_id"] == _TEST_TIKTOK_DATASET

    async def test_deleted_is_source_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records = _load("deleted_dead_page.json")
        _mock_client(monkeypatch, bd_mod, _route(snapshot_payload=records))
        _set_env(monkeypatch)
        with pytest.raises(SourceGoneError):
            await BrightDataProvider().fetch(
                "tiktok", "https://www.tiktok.com/@testcreator/video/999"
            )

    async def test_quota_401_is_provider_quota(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _mock_client(
            monkeypatch,
            bd_mod,
            _route(trigger_status=401, trigger_payload="Invalid credentials"),
        )
        _set_env(monkeypatch)
        with pytest.raises(ProviderQuotaError):
            await BrightDataProvider().fetch(
                "tiktok", "https://www.tiktok.com/@testcreator/video/1234567890123456789"
            )


class TestBrightDataSelection:
    async def test_brightdata_can_handle(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCRAPER_PROVIDER", "brightdata")
        assert await ScraperApiExtractor("instagram").can_handle("https://instagram.com/p/a")
        assert await ScraperApiExtractor("tiktok").can_handle("https://tiktok.com/@u/video/1")

    async def test_brightdata_rejects_other_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCRAPER_PROVIDER", "brightdata")
        assert (
            await ScraperApiExtractor("x").can_handle("https://x.com/u/status/1")  # type: ignore[arg-type]
            is False
        )

    async def test_none_still_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCRAPER_PROVIDER", "none")
        assert (
            await ScraperApiExtractor("instagram").can_handle("https://instagram.com/p/a") is False
        )
        assert (
            await ScraperApiExtractor("tiktok").can_handle("https://tiktok.com/@u/video/1") is False
        )

    async def test_unknown_provider_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCRAPER_PROVIDER", "bogus")
        assert (
            await ScraperApiExtractor("instagram").can_handle("https://instagram.com/p/a") is False
        )
        with pytest.raises(ExtractorError):
            await ScraperApiExtractor("instagram").extract("https://instagram.com/p/a")

    async def test_extract_routes_to_brightdata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pathlib import Path as _Path
        from typing import Any as _Any

        monkeypatch.setenv("SCRAPER_PROVIDER", "brightdata")
        seen: list[tuple[str, str]] = []

        async def _fake_download(url: str, dest_dir: _Any, *, kind: str = "video") -> _Path:
            seen.append((url, kind))
            dest = _Path(dest_dir)
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / "fake-bd.mp4"
            path.write_bytes(b"fake-bytes")
            return path

        monkeypatch.setattr(se_mod, "_resolve_download_media", lambda: _fake_download)

        async def _fake_fetch(self: _Any, platform: _Any, url: str) -> _Any:
            from app.services.extractors.scraper.base import ScrapedPost

            return ScrapedPost(
                media_type="video",
                video_url="https://cdn.example.com/brightdata/routed.mp4",
                caption="routed",
            )

        monkeypatch.setattr(BrightDataProvider, "fetch", _fake_fetch)
        result = await ScraperApiExtractor("instagram").extract("https://instagram.com/reel/a")
        try:
            assert result.media_type == "video"
            assert result.video_path is not None and result.video_path.is_file()
            assert result.original_caption == "routed"
            assert seen == [("https://cdn.example.com/brightdata/routed.mp4", "video")]
        finally:
            if result.video_path is not None:
                import shutil

                tmp = next(
                    p
                    for p in result.video_path.parents
                    if p.name.startswith("spillthereel-scraper-")
                )
                shutil.rmtree(tmp, ignore_errors=True)
