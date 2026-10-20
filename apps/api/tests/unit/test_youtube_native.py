"""Unit tests for the YouTube native ingest path (Part E).

No real network, no creds, no DB: HTTP (oEmbed + Data API v3) is faked at
the httpx boundary, the Gemini client is a fake SummaryModel, the CTO
thumbnail downloader is injected via sys.modules, and the storage/memory
tail is mocked. Mocking style mirrors tests/unit (plain fakes +
monkeypatch, no live fixtures).
"""

from __future__ import annotations

import sys
import types as stdlib_types
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.services.ingest.pipeline as pipeline_mod
from app.db.models import Item
from app.services.extractors.base import ExtractResult, Owner
from app.services.ingest.pipeline import PipelineError, _RunContext
from app.services.llm.base import StructuredSummary
from app.services.media.ffmpeg import MediaProbe
from app.tasks.worker_claims import WorkerIdentity, WorkerLease

_TEST_USER_ID = "user-yt-1"
_VIDEO_ID = "dQw4w9WgXcQ"
_WATCH_URL = f"https://www.youtube.com/watch?v={_VIDEO_ID}"
_SHORTS_URL = f"https://www.youtube.com/shorts/{_VIDEO_ID}"


def _make_item(source_url: str, item_id: str = "yt-item-1") -> Item:
    return Item(
        id=item_id,
        user_id=_TEST_USER_ID,
        source_url=source_url,
        source_url_norm=source_url,
        platform="youtube",
        state="downloading",
        saved_at=datetime.now(UTC),
        source_type="share_extension",
    )


def _make_ctx(item: Item, tmp_path: Path) -> _RunContext:
    return _RunContext(item_id=item.id, user_id=item.user_id, workdir=tmp_path)


def _lease(item: Item) -> WorkerLease:
    return WorkerLease(
        WorkerIdentity("job-yt-test", "ingest", "item", item.id, 1),
        item.id,
        item.user_id,
        "synthetic-worker-token",
    )


def _make_summary(duration: float = 270.0) -> StructuredSummary:
    return StructuredSummary(
        title="YT title here",
        summary="Two sentence summary of the video.",
        transcript="hello world",
        on_screen_text="",
        objects=["person"],
        scenes=["opening shot"],
        topics=["music"],
        sentiment="informative",  # type: ignore[arg-type]
        primary_language="en",
        duration_seconds=duration,
    )


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload

    @property
    def text(self) -> str:
        return str(self._payload)[:200]


def _fake_httpx(
    *,
    oembed: Any = None,
    oembed_status: int = 200,
    videos: Any = None,
    videos_status: int = 200,
) -> tuple[Any, list[dict[str, Any]]]:
    """Fake httpx namespace routing oEmbed vs Data API calls."""
    calls: list[dict[str, Any]] = []

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def get(self, url: str, params: Any = None, **kwargs: Any) -> _FakeResponse:
            calls.append({"url": url, "params": params})
            if "youtube.com/oembed" in url:
                return _FakeResponse(oembed_status, oembed)
            if "googleapis.com/youtube" in url:
                return _FakeResponse(videos_status, videos)
            raise AssertionError(f"unexpected URL in test: {url}")

    return stdlib_types.SimpleNamespace(AsyncClient=_FakeClient), calls


_OEMBED_OK = {
    "title": "oEmbed title",
    "author_name": "oEmbed Channel",
    "author_url": "https://www.youtube.com/@oembedchannel",
    "thumbnail_url": f"https://i.ytimg.com/vi/{_VIDEO_ID}/hqdefault.jpg",
}


def _videos_ok(
    duration: str = "PT4M30S",
    privacy: str = "public",
    upload: str = "processed",
    rating: dict[str, Any] | None = None,
) -> dict[str, Any]:
    content: dict[str, Any] = {"duration": duration}
    if rating is not None:
        content["contentRating"] = rating
    return {
        "items": [
            {
                "id": _VIDEO_ID,
                "snippet": {
                    "title": "API title",
                    "description": "API description",
                    "channelTitle": "API Channel",
                    "channelId": "UC1234567890",
                },
                "contentDetails": content,
                "status": {
                    "privacyStatus": privacy,
                    "uploadStatus": upload,
                    "embeddable": True,
                },
            }
        ]
    }


class _FakeSummaryModel:
    """Stands in for GeminiSummaryModel; records which entrypoint ran."""

    def __init__(self, summary: StructuredSummary) -> None:
        self._summary = summary
        self.youtube_calls: list[dict[str, Any]] = []
        self.legacy_calls: list[dict[str, Any]] = []

    async def summarize_youtube(
        self,
        url: str,
        *,
        title: str | None = None,
        channel: str | None = None,
        description: str | None = None,
        duration_seconds: float | None = None,
    ) -> StructuredSummary:
        self.youtube_calls.append(
            {
                "url": url,
                "title": title,
                "channel": channel,
                "description": description,
                "duration_seconds": duration_seconds,
            }
        )
        return self._summary

    async def summarize(self, frames: Any, audio: Any, transcript: Any) -> StructuredSummary:
        self.legacy_calls.append({"frames": frames, "audio": audio, "transcript": transcript})
        return self._summary


def _install_fake_downloader(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    async def _fake_download(
        url: str,
        dest_dir: Path,
        *,
        kind: str = "video",
        max_mb: Any = None,
        timeout_s: float = 60.0,
    ) -> Path:
        calls.append({"url": url, "kind": kind, "max_mb": max_mb, "timeout_s": timeout_s})
        dest = Path(dest_dir) / "thumb.jpg"
        dest.write_bytes(b"fake-thumb")
        return dest

    mod = stdlib_types.ModuleType("app.services.media.download")
    mod.download_media = _fake_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.services.media.download", mod)


def _patch_tail(monkeypatch: pytest.MonkeyPatch, model: _FakeSummaryModel) -> dict[str, Any]:
    """Mock everything from the thumbnail upload onwards + event emission."""
    state: dict[str, Any] = {
        "finalized": [],
        "gone": [],
        "writes": [],
    }

    async def _finalized(**kwargs: Any) -> None:
        state["finalized"].append(kwargs)

    async def _gone(item_id: str, reason: str, lease: Any = None) -> None:
        state["gone"].append((item_id, reason))

    class _FakeStore:
        def __init__(self, pool: Any) -> None:
            pass

        async def write(self, user_id: str, item: Any, **kwargs: Any) -> str:
            state["writes"].append((user_id, item))
            return item.item_id

    state["emit"] = AsyncMock()
    state["storage_upload"] = AsyncMock(return_value="media/u")
    state["storage_signed_url"] = AsyncMock(return_value="https://signed/thumb.jpg")
    monkeypatch.setattr(pipeline_mod, "_emit_event", state["emit"])
    monkeypatch.setattr(pipeline_mod, "storage_upload", state["storage_upload"])
    monkeypatch.setattr(pipeline_mod, "storage_signed_url", state["storage_signed_url"])
    monkeypatch.setattr(pipeline_mod, "PgVectorStore", _FakeStore)
    monkeypatch.setattr(pipeline_mod, "get_pg_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(pipeline_mod, "_mark_fully_indexed", _finalized)
    monkeypatch.setattr(pipeline_mod, "_mark_source_gone", _gone)
    monkeypatch.setattr(pipeline_mod, "GeminiSummaryModel", lambda: model)
    monkeypatch.setattr(pipeline_mod, "renew_worker_lease", AsyncMock(return_value=True))
    return state


def _guard_legacy_chain(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fail loudly if the download/ffmpeg/registry path runs (native tests)."""
    guards = {
        "extract": AsyncMock(side_effect=AssertionError("registry must not run on native path")),
        "probe": AsyncMock(side_effect=AssertionError("ffmpeg must not run on native path")),
    }
    monkeypatch.setattr(pipeline_mod, "extractor_extract", guards["extract"])
    fake_ffmpeg = stdlib_types.SimpleNamespace(
        probe=guards["probe"],
        sample_frames=AsyncMock(side_effect=AssertionError("ffmpeg frames")),
        extract_audio=AsyncMock(side_effect=AssertionError("ffmpeg audio")),
        make_thumbnail=AsyncMock(side_effect=AssertionError("ffmpeg thumb")),
        DEFAULT_FRAME_INTERVAL_SECONDS=2.0,
    )
    monkeypatch.setattr(pipeline_mod, "ffmpeg", fake_ffmpeg)
    return guards


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestYouTubeNative:
    async def test_happy_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("YOUTUBE_NATIVE_ENABLED", "true")
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
        monkeypatch.delenv("YOUTUBE_MAX_DURATION_SECONDS", raising=False)
        fake_http, http_calls = _fake_httpx(oembed=_OEMBED_OK, videos=_videos_ok())
        monkeypatch.setattr(pipeline_mod, "httpx", fake_http)
        model = _FakeSummaryModel(_make_summary())
        state = _patch_tail(monkeypatch, model)
        guards = _guard_legacy_chain(monkeypatch)
        dl_calls: list[dict[str, Any]] = []
        _install_fake_downloader(monkeypatch, dl_calls)

        item = _make_item(_WATCH_URL)
        await pipeline_mod._run(_make_ctx(item, tmp_path), item, _lease(item))

        state["emit"].assert_any_await(
            item.id,
            "analyzing",
            "indexing",
            detail={"stage": "summary_generated"},
            lease=_lease(item),
        )

        # Gemini got the canonical public watch URL + Data API hints.
        assert len(model.youtube_calls) == 1
        yt_call = model.youtube_calls[0]
        assert yt_call["url"] == _WATCH_URL
        assert yt_call["title"] == "API title"
        assert yt_call["channel"] == "API Channel"
        assert yt_call["description"] == "API description"
        assert yt_call["duration_seconds"] == pytest.approx(270.0)

        # Thumbnail via the CTO-decreed downloader shape (kind="image":
        # the downloader gates content-type by kind and thumbs are JPEG).
        assert len(dl_calls) == 1
        assert dl_calls[0]["url"] == f"https://i.ytimg.com/vi/{_VIDEO_ID}/hqdefault.jpg"
        assert dl_calls[0]["kind"] == "image"
        assert dl_calls[0]["max_mb"] is None
        assert dl_calls[0]["timeout_s"] == pytest.approx(60.0)

        # Shared tail ran: upload + embedding + finalize, no source_gone.
        state["storage_upload"].assert_awaited_once()
        assert len(state["writes"]) == 1
        user_id, indexed = state["writes"][0]
        assert user_id == _TEST_USER_ID
        assert "YT title here" in indexed.text_corpus
        assert indexed.metadata["platform"] == "youtube"
        assert indexed.metadata["duration_seconds"] == pytest.approx(270.0)
        assert len(state["finalized"]) == 1
        fin = state["finalized"][0]
        assert fin["owner_name"] == "API Channel"
        assert fin["caption"] == "API description"
        assert fin["owner_url"] == "https://www.youtube.com/channel/UC1234567890"
        assert state["gone"] == []
        guards["extract"].assert_not_awaited()
        guards["probe"].assert_not_awaited()
        # Both metadata sources were consulted.
        assert any("oembed" in c["url"] for c in http_calls)
        assert any("googleapis" in c["url"] for c in http_calls)

    async def test_not_public_goes_source_gone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("YOUTUBE_NATIVE_ENABLED", "true")
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
        fake_http, _ = _fake_httpx(oembed=_OEMBED_OK, videos=_videos_ok(privacy="private"))
        monkeypatch.setattr(pipeline_mod, "httpx", fake_http)
        model = _FakeSummaryModel(_make_summary())
        state = _patch_tail(monkeypatch, model)
        _guard_legacy_chain(monkeypatch)
        dl_calls: list[dict[str, Any]] = []
        _install_fake_downloader(monkeypatch, dl_calls)

        item = _make_item(_WATCH_URL)
        await pipeline_mod._run(_make_ctx(item, tmp_path), item, _lease(item))

        assert state["gone"] == [("yt-item-1", "youtube_not_public")]
        assert state["finalized"] == []
        assert state["writes"] == []
        assert model.youtube_calls == []
        assert dl_calls == []

    async def test_too_long_is_pipeline_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("YOUTUBE_NATIVE_ENABLED", "true")
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")
        monkeypatch.setenv("YOUTUBE_MAX_DURATION_SECONDS", "600")
        fake_http, _ = _fake_httpx(oembed=_OEMBED_OK, videos=_videos_ok(duration="PT20M"))
        monkeypatch.setattr(pipeline_mod, "httpx", fake_http)
        model = _FakeSummaryModel(_make_summary())
        state = _patch_tail(monkeypatch, model)
        _guard_legacy_chain(monkeypatch)
        dl_calls: list[dict[str, Any]] = []
        _install_fake_downloader(monkeypatch, dl_calls)

        item = _make_item(_WATCH_URL)
        with pytest.raises(PipelineError, match="youtube_too_long"):
            await pipeline_mod._run(_make_ctx(item, tmp_path), item, _lease(item))

        assert state["finalized"] == []
        assert state["gone"] == []
        assert model.youtube_calls == []

    async def test_unknown_duration_watch_url_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # No Data API key + non-/shorts/ URL → cannot meter cost → fail loud.
        monkeypatch.setenv("YOUTUBE_NATIVE_ENABLED", "true")
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        fake_http, _ = _fake_httpx(oembed=_OEMBED_OK)
        monkeypatch.setattr(pipeline_mod, "httpx", fake_http)
        model = _FakeSummaryModel(_make_summary())
        state = _patch_tail(monkeypatch, model)
        _guard_legacy_chain(monkeypatch)
        dl_calls: list[dict[str, Any]] = []
        _install_fake_downloader(monkeypatch, dl_calls)

        item = _make_item(_WATCH_URL)
        with pytest.raises(PipelineError, match="youtube_duration_unknown"):
            await pipeline_mod._run(_make_ctx(item, tmp_path), item, _lease(item))

        assert state["finalized"] == []
        assert model.youtube_calls == []
        assert dl_calls == []

    async def test_flag_off_falls_back_to_legacy_chain(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("YOUTUBE_NATIVE_ENABLED", "false")
        monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

        async def _boom_download(*args: Any, **kwargs: Any) -> Path:
            raise AssertionError("native thumbnail path must not run when flag is off")

        mod = stdlib_types.ModuleType("app.services.media.download")
        mod.download_media = _boom_download  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "app.services.media.download", mod)

        video_path = tmp_path / "source.mp4"
        video_path.write_bytes(b"fake-video")
        frame_path = tmp_path / "frame_0001.jpg"
        frame_path.write_bytes(b"fake-frame")
        thumb_path = tmp_path / "thumb.jpg"
        thumb_path.write_bytes(b"fake-thumb")
        extract_mock = AsyncMock(
            return_value=ExtractResult(
                media_type="video",
                video_path=video_path,
                audio_path=None,
                image_paths=[],
                original_caption="legacy caption",
                original_owner=Owner(
                    url="https://youtube.com/@x", name="Legacy", username="legacy"
                ),
                duration_seconds=45.0,
                resolved_at=datetime.now(UTC),
            )
        )
        monkeypatch.setattr(pipeline_mod, "extractor_extract", extract_mock)
        fake_ffmpeg = stdlib_types.SimpleNamespace(
            probe=AsyncMock(
                return_value=MediaProbe(
                    duration_seconds=45.0, width=720, height=1280, has_video=True, has_audio=True
                )
            ),
            sample_frames=AsyncMock(return_value=[frame_path]),
            extract_audio=AsyncMock(return_value=tmp_path / "audio.wav"),
            make_thumbnail=AsyncMock(return_value=thumb_path),
            DEFAULT_FRAME_INTERVAL_SECONDS=2.0,
        )
        monkeypatch.setattr(pipeline_mod, "ffmpeg", fake_ffmpeg)

        model = _FakeSummaryModel(_make_summary(duration=45.0))

        async def _no_native(url: str, **kwargs: Any) -> StructuredSummary:
            raise AssertionError("summarize_youtube must not run when flag is off")

        model.summarize_youtube = _no_native  # type: ignore[method-assign]
        state = _patch_tail(monkeypatch, model)

        item = _make_item(_WATCH_URL)
        await pipeline_mod._run(_make_ctx(item, tmp_path), item, _lease(item))

        extract_mock.assert_awaited_once_with(_WATCH_URL)
        assert len(model.legacy_calls) == 1
        assert len(state["finalized"]) == 1
        assert state["gone"] == []
