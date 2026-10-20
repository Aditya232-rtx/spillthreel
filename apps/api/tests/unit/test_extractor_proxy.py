"""EXTRACTOR_EGRESS_PROXY_URL passthrough tests (yt-dlp + gallery-dl).

No real network, no binaries: yt-dlp option wiring is verified at the
pure helper level (yt_dlp itself lives in the worker extra and is not
imported here), and gallery-dl argv is captured by faking
`asyncio.create_subprocess_exec`. The proxy URL is never asserted in
logs — only its presence/position in the outgoing invocation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.extractors import gallerydl as gallerydl_mod
from app.services.extractors import ytdlp as ytdlp_mod
from app.services.extractors.base import ExtractorError
from app.services.extractors.gallerydl import GalleryDlExtractor
from app.services.extractors.ytdlp import YtDlpExtractor

_PROXY = "http://proxy.internal:8080"


class TestExtractorProxyHelpers:
    def test_ytdlp_no_proxy_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXTRACTOR_EGRESS_PROXY_URL", raising=False)
        assert ytdlp_mod._extractor_proxy() is None

    def test_ytdlp_env_proxy_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTRACTOR_EGRESS_PROXY_URL", _PROXY)
        assert ytdlp_mod._extractor_proxy() == _PROXY

    def test_gallerydl_no_proxy_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EXTRACTOR_EGRESS_PROXY_URL", raising=False)
        assert gallerydl_mod._extractor_proxy() is None

    def test_gallerydl_env_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTRACTOR_EGRESS_PROXY_URL", _PROXY)
        assert gallerydl_mod._extractor_proxy() == _PROXY

    @pytest.mark.parametrize("module", [ytdlp_mod, gallerydl_mod])
    def test_paid_provider_proxy_does_not_enable_extractors(
        self, monkeypatch: pytest.MonkeyPatch, module: Any
    ) -> None:
        monkeypatch.delenv("EXTRACTOR_EGRESS_PROXY_URL", raising=False)
        monkeypatch.setenv("OUTBOUND_PROXY_URL", _PROXY)
        monkeypatch.setattr(
            "app.settings.get_settings",
            lambda: SimpleNamespace(extractor_egress_proxy_url=None),
        )
        assert module._extractor_proxy() is None


async def test_gallerydl_proxy_config_keeps_credentials_out_of_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Both phases use a private config file; proxy credentials never enter argv."""
    proxy = "http://proxy-user:proxy-password@proxy.internal:8080"
    monkeypatch.setenv("EXTRACTOR_EGRESS_PROXY_URL", proxy)
    monkeypatch.setenv("GEMINI_API_KEY", "model-secret")
    monkeypatch.setenv("OUTBOUND_PROXY_URL", "http://paid-user:paid-secret@paid-proxy")
    calls: list[list[str]] = []
    config_paths: list[Path] = []
    child_envs: list[dict[str, str]] = []

    class _FakeProcess:
        def __init__(self, dest: Path | None) -> None:
            self._dest = dest
            self.returncode: int | None = 0

        async def communicate(self, *_args: Any) -> tuple[bytes, bytes]:
            if self._dest is not None:
                target = self._dest / "instagram" / "u" / "a.jpg"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"\xff\xd8fake")
            meta = json.dumps([{"description": "cap"}]).encode()
            return meta, b""

        def kill(self) -> None:
            pass  # pragma: no cover

        async def wait(self) -> int | None:
            return self.returncode

    async def _fake_create(*args: str, **kwargs: Any) -> _FakeProcess:
        arglist = list(args)
        calls.append(arglist)
        child_envs.append(kwargs["env"])
        config_path = Path(arglist[arglist.index("--config-json") + 1])
        config_paths.append(config_path)
        assert "proxy-password" not in " ".join(arglist)
        assert config_path.stat().st_mode & 0o077 == 0
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert config["extractor"]["proxy"] == proxy
        assert config["downloader"]["proxy"] == proxy
        dest: Path | None = None
        if "--destination" in arglist:
            dest = Path(arglist[arglist.index("--destination") + 1])
        return _FakeProcess(dest)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)

    result = await GalleryDlExtractor("instagram").extract("https://instagram.com/p/abc123")
    try:
        assert result.media_type == "image"
        assert len(calls) == 2
        for argv in calls:
            assert argv[0] == "gallery-dl"
            assert "--proxy" not in argv
            assert "--config-ignore" in argv
            assert argv[-1] == "https://instagram.com/p/abc123"
        for child_env in child_envs:
            assert "GEMINI_API_KEY" not in child_env
            assert "OUTBOUND_PROXY_URL" not in child_env
            assert "EXTRACTOR_EGRESS_PROXY_URL" not in child_env
        assert len(set(config_paths)) == 1
        assert not config_paths[0].exists()
    finally:
        import shutil

        tmp = next(
            p for p in result.image_paths[0].parents if p.name.startswith("spillthereel-gallerydl-")
        )
        shutil.rmtree(tmp, ignore_errors=True)


async def test_gallerydl_without_proxy_fails_before_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconfigured gallery-dl must not make direct network requests."""
    monkeypatch.delenv("EXTRACTOR_EGRESS_PROXY_URL", raising=False)
    calls: list[list[str]] = []

    async def _unexpected_subprocess(*args: str, **kwargs: Any) -> Any:
        calls.append(list(args))
        raise AssertionError("gallery-dl must not run without the proxy")

    monkeypatch.setattr(gallerydl_mod, "_extractor_proxy", lambda: None)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _unexpected_subprocess)
    with pytest.raises(ExtractorError, match="EXTRACTOR_EGRESS_PROXY_URL"):
        await GalleryDlExtractor("x").extract("https://x.com/u/status/1?token=secret")
    assert calls == []


async def test_ytdlp_without_proxy_fails_before_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unconfigured yt-dlp must not import/call its network-capable API."""
    monkeypatch.delenv("EXTRACTOR_EGRESS_PROXY_URL", raising=False)
    calls: list[str] = []

    def _unexpected_import() -> Any:
        calls.append("import")
        raise AssertionError("yt-dlp must not load without the proxy")

    monkeypatch.setattr(ytdlp_mod, "_extractor_proxy", lambda: None)
    monkeypatch.setattr(ytdlp_mod, "_lazy_import_ytdlp", _unexpected_import)
    with pytest.raises(ExtractorError, match="EXTRACTOR_EGRESS_PROXY_URL"):
        await YtDlpExtractor("youtube").extract(
            "https://youtube.com/watch?v=abc&signature=signed-secret"
        )
    assert calls == []


async def test_ytdlp_extract_passes_configured_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The configured proxy reaches the yt-dlp request options."""
    monkeypatch.setenv("EXTRACTOR_EGRESS_PROXY_URL", _PROXY)
    sentinel = object()
    monkeypatch.setattr(ytdlp_mod, "_lazy_import_ytdlp", lambda: sentinel)
    captured: dict[str, Any] = {}

    async def _fake_to_thread(func: Any, yt_dlp: Any, opts: dict[str, Any], url: str) -> Any:
        captured.update(yt_dlp=yt_dlp, opts=opts, url=url)
        return {"duration": 1}, tmp_path / "video.mp4"

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)
    result = await YtDlpExtractor("youtube").extract("https://youtube.com/watch?v=abc")
    assert result.media_type == "video"
    assert captured["yt_dlp"] is sentinel
    assert captured["opts"]["proxy"] == _PROXY
