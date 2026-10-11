"""Scraper registry chain tests (Part D).

Verifies chain order per platform, SourceGone short-circuit (no
fallthrough past a gone source), and quota fallthrough (never mapped
to gone).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.services.extractors import registry as registry_mod
from app.services.extractors.base import ExtractorError, ExtractResult
from app.services.extractors.gallerydl import GalleryDlExtractor
from app.services.extractors.scraper.base import ProviderQuotaError, SourceGoneError
from app.services.extractors.scraper.scraper_extractor import ScraperApiExtractor
from app.services.extractors.ytdlp import YtDlpExtractor


def _result() -> ExtractResult:
    return ExtractResult(
        media_type="video",
        video_path=None,
        audio_path=None,
        image_paths=[],
        original_caption=None,
        original_owner=None,
        duration_seconds=None,
        resolved_at=datetime.now(UTC),
    )


class _Fake:
    def __init__(
        self,
        exc: Exception | None = None,
        can_handle_answer: bool = True,
    ) -> None:
        self._exc = exc
        self._can = can_handle_answer
        self.calls = 0

    async def can_handle(self, url: str) -> bool:
        return self._can

    async def extract(self, url: str) -> ExtractResult:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return _result()


def _chain_names(platform: str) -> list[str]:
    return [type(e).__name__ for e in registry_mod._build_registry()[platform]]  # type: ignore[index]


class TestChainOrder:
    def test_instagram_chain(self) -> None:
        assert _chain_names("instagram") == [
            "ScraperApiExtractor",
            "YtDlpExtractor",
            "GalleryDlExtractor",
        ]

    def test_tiktok_chain(self) -> None:
        assert _chain_names("tiktok") == ["ScraperApiExtractor", "YtDlpExtractor"]

    def test_x_chain_has_no_scraper(self) -> None:
        assert _chain_names("x") == ["YtDlpExtractor", "GalleryDlExtractor"]

    def test_youtube_chain_has_no_scraper(self) -> None:
        # Part E owns YouTube — registry must not route it via Scraper.
        names = _chain_names("youtube")
        assert "ScraperApiExtractor" not in names
        assert names == ["YtDlpExtractor"]

    def test_built_registry_types(self) -> None:
        reg = registry_mod._build_registry()
        assert isinstance(reg["instagram"][0], ScraperApiExtractor)
        assert isinstance(reg["instagram"][1], YtDlpExtractor)
        assert isinstance(reg["instagram"][2], GalleryDlExtractor)


class TestSourceGoneShortCircuit:
    async def test_source_gone_does_not_fall_through(self) -> None:
        first = _Fake(exc=SourceGoneError("gone"))
        second = _Fake()
        with (
            patch.object(registry_mod, "_registry", {"instagram": [first, second]}),
            pytest.raises(SourceGoneError),
        ):
            await registry_mod.extract("https://instagram.com/p/abc")
        assert first.calls == 1
        assert second.calls == 0

    async def test_quota_falls_through_and_is_not_gone(self) -> None:
        first = _Fake(exc=ProviderQuotaError("quota"))
        second = _Fake()
        with patch.object(registry_mod, "_registry", {"instagram": [first, second]}):
            result = await registry_mod.extract("https://instagram.com/p/abc")
        assert first.calls == 1
        assert second.calls == 1
        assert result.media_type == "video"

    async def test_transient_falls_through(self) -> None:
        first = _Fake(exc=ExtractorError("transient"))
        second = _Fake()
        with patch.object(registry_mod, "_registry", {"instagram": [first, second]}):
            await registry_mod.extract("https://instagram.com/p/abc")
        assert second.calls == 1
