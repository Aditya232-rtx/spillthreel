"""Unit tests for the extractor registry's fallback + error behavior.

Uses fake Extractor Protocol impls so we don't hit Cobalt or spawn
yt-dlp. The key contract we're testing: first extractor that returns
without raising wins; ExtractorError skips to the next; anything else
propagates up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.services.extractors import registry as registry_mod
from app.services.extractors.base import (
    ExtractResult,
    ExtractorError,
    NoExtractorSucceededError,
    Platform,
)


class _FakeExtractor:
    """Test double implementing the Extractor Protocol."""

    def __init__(
        self,
        platform: Platform = "instagram",
        result: ExtractResult | None = None,
        raise_extractor_error: bool = False,
        raise_unexpected: bool = False,
        can_handle_answer: bool = True,
    ) -> None:
        self.platform: Platform = platform
        self._result = result
        self._raise_extractor_error = raise_extractor_error
        self._raise_unexpected = raise_unexpected
        self._can_handle_answer = can_handle_answer
        self.extract_calls = 0

    async def can_handle(self, url: str) -> bool:
        return self._can_handle_answer

    async def extract(self, url: str) -> ExtractResult:
        self.extract_calls += 1
        if self._raise_unexpected:
            raise RuntimeError("boom")
        if self._raise_extractor_error:
            raise ExtractorError("expected fail")
        assert self._result is not None
        return self._result


def _make_result() -> ExtractResult:
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


class TestRegistryFallback:
    async def test_first_success_short_circuits(self) -> None:
        first = _FakeExtractor(result=_make_result())
        second = _FakeExtractor(result=_make_result())
        with patch.object(registry_mod, "_registry", {"instagram": [first, second]}):
            await registry_mod.extract("https://instagram.com/reel/abc")
        assert first.extract_calls == 1
        assert second.extract_calls == 0

    async def test_first_extractor_error_falls_back(self) -> None:
        first = _FakeExtractor(raise_extractor_error=True)
        second = _FakeExtractor(result=_make_result())
        with patch.object(registry_mod, "_registry", {"instagram": [first, second]}):
            result = await registry_mod.extract("https://instagram.com/reel/abc")
        assert first.extract_calls == 1
        assert second.extract_calls == 1
        assert result.media_type == "video"

    async def test_all_fail_raises_no_extractor_succeeded(self) -> None:
        first = _FakeExtractor(raise_extractor_error=True)
        second = _FakeExtractor(raise_extractor_error=True)
        with patch.object(registry_mod, "_registry", {"instagram": [first, second]}):
            with pytest.raises(NoExtractorSucceededError):
                await registry_mod.extract("https://instagram.com/reel/abc")

    async def test_unexpected_exception_propagates(self) -> None:
        """Non-ExtractorError exceptions must NOT be swallowed by fallback —
        the registry only walks past known-failure signals."""
        first = _FakeExtractor(raise_unexpected=True)
        second = _FakeExtractor(result=_make_result())
        with patch.object(registry_mod, "_registry", {"instagram": [first, second]}):
            with pytest.raises(RuntimeError, match="boom"):
                await registry_mod.extract("https://instagram.com/reel/abc")
        # Fallback should NOT have been tried.
        assert second.extract_calls == 0

    async def test_can_handle_false_skips_without_calling_extract(self) -> None:
        first = _FakeExtractor(can_handle_answer=False)
        second = _FakeExtractor(result=_make_result())
        with patch.object(registry_mod, "_registry", {"instagram": [first, second]}):
            await registry_mod.extract("https://instagram.com/reel/abc")
        assert first.extract_calls == 0
        assert second.extract_calls == 1

    async def test_no_extractors_for_platform_raises(self) -> None:
        with patch.object(registry_mod, "_registry", {"instagram": []}):
            with pytest.raises(NoExtractorSucceededError):
                await registry_mod.extract("https://instagram.com/reel/abc")
