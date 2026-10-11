"""ScraperApiExtractor tests (Part D).

download_media (Part C) is faked — no network, no temp-file leaks.
Covers provider selection, the video-wins carousel rule, up-to-N image
cap, and loud failure when the Part C downloader is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services.extractors.base import ExtractorError
from app.services.extractors.scraper import scraper_extractor as se_mod
from app.services.extractors.scraper.base import ScrapedPost
from app.services.extractors.scraper.scraper_extractor import ScraperApiExtractor


def _fake_download_factory(seen: list[tuple[str, str]]) -> Any:
    async def _fake(url: str, dest_dir: Any, *, kind: str = "video") -> Path:
        seen.append((url, kind))
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        suffix = ".mp4" if kind == "video" else ".jpg"
        path = dest / f"fake-{len(seen)}{suffix}"
        path.write_bytes(b"fake-bytes")
        return path

    return _fake


async def test_none_provider_can_handle_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPER_PROVIDER", "none")
    assert await ScraperApiExtractor("instagram").can_handle("https://instagram.com/p/a") is False
    assert await ScraperApiExtractor("tiktok").can_handle("https://tiktok.com/@u/video/1") is False


async def test_unsupported_platform_can_handle_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPER_PROVIDER", "scrapecreators")
    assert await ScraperApiExtractor("x").can_handle("https://x.com/u/status/1") is False  # type: ignore[arg-type]


async def test_video_downloads_video_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPER_PROVIDER", "scrapecreators")
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(se_mod, "_resolve_download_media", lambda: _fake_download_factory(seen))

    async def _fake_fetch(self: Any, platform: Any, url: str) -> ScrapedPost:
        return ScrapedPost(
            media_type="video",
            video_url="https://cdn.example.com/v.mp4",
            caption="cap",
            owner_username="u",
            owner_name="N",
            duration_seconds=9.0,
        )

    monkeypatch.setattr(se_mod.ScrapeCreatorsProvider, "fetch", _fake_fetch)
    result = await ScraperApiExtractor("instagram").extract("https://instagram.com/reel/a")
    try:
        assert result.media_type == "video"
        assert result.video_path is not None and result.video_path.is_file()
        assert result.original_caption == "cap"
        assert result.original_owner is not None and result.original_owner.username == "u"
        assert result.duration_seconds == 9.0
        assert seen == [("https://cdn.example.com/v.mp4", "video")]
    finally:
        if result.video_path is not None:
            import shutil

            tmp = next(
                p for p in result.video_path.parents if p.name.startswith("spillthereel-scraper-")
            )
            shutil.rmtree(tmp, ignore_errors=True)


async def test_mixed_carousel_video_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Video-wins rule: mixed carousel downloads ONLY the video."""
    monkeypatch.setenv("SCRAPER_PROVIDER", "apify")
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(se_mod, "_resolve_download_media", lambda: _fake_download_factory(seen))

    async def _fake_fetch(self: Any, platform: Any, url: str) -> ScrapedPost:
        return ScrapedPost(
            media_type="carousel",
            video_url="https://cdn.example.com/car.mp4",
            image_urls=[
                "https://cdn.example.com/car1.jpg",
                "https://cdn.example.com/car2.jpg",
            ],
        )

    monkeypatch.setattr(se_mod.ApifyProvider, "fetch", _fake_fetch)
    result = await ScraperApiExtractor("instagram").extract("https://instagram.com/p/a")
    try:
        assert result.media_type == "video"
        assert result.video_path is not None
        assert result.image_paths == []
        assert seen == [("https://cdn.example.com/car.mp4", "video")]
    finally:
        if result.video_path is not None:
            import shutil

            tmp = next(
                p for p in result.video_path.parents if p.name.startswith("spillthereel-scraper-")
            )
            shutil.rmtree(tmp, ignore_errors=True)


async def test_image_carousel_caps_at_n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPER_PROVIDER", "scrapecreators")
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr(se_mod, "_resolve_download_media", lambda: _fake_download_factory(seen))

    async def _fake_fetch(self: Any, platform: Any, url: str) -> ScrapedPost:
        return ScrapedPost(
            media_type="carousel",
            image_urls=[f"https://cdn.example.com/i{i}.jpg" for i in range(25)],
        )

    monkeypatch.setattr(se_mod.ScrapeCreatorsProvider, "fetch", _fake_fetch)
    result = await ScraperApiExtractor("instagram").extract("https://instagram.com/p/a")
    try:
        assert result.media_type == "image"
        assert len(result.image_paths) == se_mod._MAX_IMAGES
        assert all(kind == "image" for _, kind in seen)
        assert len(seen) == se_mod._MAX_IMAGES
    finally:
        if result.image_paths:
            import shutil

            tmp = next(
                p
                for p in result.image_paths[0].parents
                if p.name.startswith("spillthereel-scraper-")
            )
            shutil.rmtree(tmp, ignore_errors=True)


async def test_missing_downloader_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRAPER_PROVIDER", "scrapecreators")

    async def _fake_fetch(self: Any, platform: Any, url: str) -> ScrapedPost:
        return ScrapedPost(media_type="video", video_url="https://cdn.example.com/v.mp4")

    monkeypatch.setattr(se_mod.ScrapeCreatorsProvider, "fetch", _fake_fetch)

    def _boom() -> Any:
        raise RuntimeError("download_media missing")

    monkeypatch.setattr(se_mod, "_resolve_download_media", _boom)
    with pytest.raises((RuntimeError, ExtractorError)):
        await ScraperApiExtractor("instagram").extract("https://instagram.com/reel/a")
