"""Unit tests for the pipeline image branch (photo/carousel posts).

No real network, no DB, no creds: the extractor registry, the Gemini
model, ffmpeg, and the storage/memory tail are all faked. Mocking style
mirrors test_youtube_native.py (plain fakes + monkeypatch).
"""

from __future__ import annotations

import types as stdlib_types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.services.ingest.pipeline as pipeline_mod
from app.db.models import Item
from app.services.extractors.base import ExtractResult, Owner
from app.services.llm.base import StructuredSummary

_TEST_USER_ID = "user-img-1"
_SOURCE_URL = "https://www.instagram.com/p/abc123/"


def _make_item() -> Item:
    return Item(
        id="img-item-1",
        user_id=_TEST_USER_ID,
        source_url=_SOURCE_URL,
        source_url_norm=_SOURCE_URL,
        platform="instagram",
        state="downloading",
        saved_at=datetime.now(UTC),
        source_type="share_extension",
    )


def _make_summary() -> StructuredSummary:
    return StructuredSummary(
        title="Photo title here",
        summary="Two sentence summary of the photo.",
        transcript="",
        on_screen_text="hello sign",
        objects=["person", "dog"],
        scenes=["person holding a dog"],
        topics=["pets"],
        sentiment="informative",  # type: ignore[arg-type]
        primary_language="en",
        duration_seconds=0.0,
    )


class _FakeSummaryModel:
    """Stands in for GeminiSummaryModel; records the image-branch call."""

    def __init__(self, summary: StructuredSummary) -> None:
        self._summary = summary
        self.calls: list[dict[str, Any]] = []

    async def summarize(self, frames: Any, audio: Any, transcript: Any) -> StructuredSummary:
        self.calls.append({"frames": frames, "audio": audio, "transcript": transcript})
        return self._summary

    async def summarize_youtube(self, *args: Any, **kwargs: Any) -> StructuredSummary:
        raise AssertionError("youtube native path must not run for image items")


def _patch_world(
    monkeypatch: pytest.MonkeyPatch, model: _FakeSummaryModel, result: ExtractResult
) -> dict[str, Any]:
    state: dict[str, Any] = {"finalized": [], "gone": []}
    state["emit"] = AsyncMock()
    state["extract"] = AsyncMock(return_value=result)

    async def _finalized(ctx: Any, **kwargs: Any) -> None:
        state["finalized"].append({"ctx": ctx, **kwargs})

    async def _gone(item_id: str, reason: str) -> None:
        state["gone"].append((item_id, reason))

    monkeypatch.setattr(pipeline_mod, "_emit_event", state["emit"])
    monkeypatch.setattr(pipeline_mod, "extractor_extract", state["extract"])
    monkeypatch.setattr(pipeline_mod, "_finish_indexed", _finalized)
    monkeypatch.setattr(pipeline_mod, "_mark_source_gone", _gone)
    monkeypatch.setattr(pipeline_mod, "GeminiSummaryModel", lambda: model)
    # ffmpeg must not run on the image branch — nothing to probe/sample.
    fake_ffmpeg = stdlib_types.SimpleNamespace(
        probe=AsyncMock(side_effect=AssertionError("ffmpeg probe must not run")),
        sample_frames=AsyncMock(side_effect=AssertionError("ffmpeg frames must not run")),
        extract_audio=AsyncMock(side_effect=AssertionError("ffmpeg audio must not run")),
        make_thumbnail=AsyncMock(side_effect=AssertionError("ffmpeg thumb must not run")),
        DEFAULT_FRAME_INTERVAL_SECONDS=2.0,
    )
    monkeypatch.setattr(pipeline_mod, "ffmpeg", fake_ffmpeg)
    return state


def _image_result(image_paths: list[Path]) -> ExtractResult:
    return ExtractResult(
        media_type="image",
        video_path=None,
        audio_path=None,
        image_paths=image_paths,
        original_caption="carousel caption",
        original_owner=Owner(
            url="https://instagram.com/someuser", name="Some User", username="someuser"
        ),
        duration_seconds=None,
        resolved_at=datetime.now(UTC),
    )


def _write_images(tmp_path: Path, names: list[str]) -> list[Path]:
    paths: list[Path] = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"\xff\xd8fake-image-bytes")
        paths.append(path)
    return paths


class TestImageBranch:
    async def test_carousel_images_become_frames(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        images = _write_images(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
        model = _FakeSummaryModel(_make_summary())
        state = _patch_world(monkeypatch, model, _image_result(images))

        item = _make_item()
        ctx = pipeline_mod._RunContext(item_id=item.id, user_id=item.user_id, workdir=tmp_path)
        await pipeline_mod._run(ctx, item)

        state["extract"].assert_awaited_once_with(_SOURCE_URL)
        # Gemini got 3 frames, no audio, no transcript.
        assert len(model.calls) == 1
        call = model.calls[0]
        assert len(call["frames"]) == 3
        assert [f.path for f in call["frames"]] == images
        assert call["audio"] is None
        assert call["transcript"] is None
        # Shared tail ran with the first image as thumbnail.
        assert len(state["finalized"]) == 1
        fin = state["finalized"][0]
        assert fin["thumb_path"] == images[0]
        assert fin["caption"] == "carousel caption"
        assert fin["owner_username"] == "someuser"
        assert fin["transcript_source"] == "gemini"
        assert fin["model_frames"] == 3
        assert state["gone"] == []

    async def test_single_photo(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        images = _write_images(tmp_path, ["only.png"])
        model = _FakeSummaryModel(_make_summary())
        state = _patch_world(monkeypatch, model, _image_result(images))

        item = _make_item()
        ctx = pipeline_mod._RunContext(item_id=item.id, user_id=item.user_id, workdir=tmp_path)
        await pipeline_mod._run(ctx, item)

        assert len(model.calls) == 1
        assert len(model.calls[0]["frames"]) == 1
        assert len(state["finalized"]) == 1
        assert state["finalized"][0]["thumb_path"] == images[0]
        assert state["finalized"][0]["model_frames"] == 1
        assert state["gone"] == []

    async def test_carousel_capped_at_ten(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        images = _write_images(tmp_path, [f"{i:02d}.jpg" for i in range(12)])
        model = _FakeSummaryModel(_make_summary())
        state = _patch_world(monkeypatch, model, _image_result(images))

        item = _make_item()
        ctx = pipeline_mod._RunContext(item_id=item.id, user_id=item.user_id, workdir=tmp_path)
        await pipeline_mod._run(ctx, item)

        assert len(model.calls[0]["frames"]) == 10
        assert state["finalized"][0]["model_frames"] == 10

    async def test_no_media_at_all_still_fails_loud(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        empty = ExtractResult(
            media_type="image",
            video_path=None,
            audio_path=None,
            image_paths=[],
            original_caption=None,
            original_owner=None,
            duration_seconds=None,
            resolved_at=datetime.now(UTC),
        )
        model = _FakeSummaryModel(_make_summary())
        state = _patch_world(monkeypatch, model, empty)

        item = _make_item()
        ctx = pipeline_mod._RunContext(item_id=item.id, user_id=item.user_id, workdir=tmp_path)
        with pytest.raises(pipeline_mod.PipelineError, match="no video path"):
            await pipeline_mod._run(ctx, item)

        assert model.calls == []
        assert state["finalized"] == []
