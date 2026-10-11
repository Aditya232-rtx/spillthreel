"""OUTBOUND_PROXY_URL passthrough tests (yt-dlp + gallery-dl).

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
from typing import Any

import pytest

from app.services.extractors import gallerydl as gallerydl_mod
from app.services.extractors import ytdlp as ytdlp_mod
from app.services.extractors.gallerydl import GalleryDlExtractor

_PROXY = "http://proxy.internal:8080"


class TestOutboundProxyHelpers:
    def test_ytdlp_no_proxy_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTBOUND_PROXY_URL", raising=False)
        assert ytdlp_mod._outbound_proxy() is None

    def test_ytdlp_env_proxy_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTBOUND_PROXY_URL", _PROXY)
        assert ytdlp_mod._outbound_proxy() == _PROXY

    def test_gallerydl_no_proxy_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OUTBOUND_PROXY_URL", raising=False)
        assert gallerydl_mod._outbound_proxy() is None
        assert gallerydl_mod._proxy_args() == []

    def test_gallerydl_env_proxy_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OUTBOUND_PROXY_URL", _PROXY)
        assert gallerydl_mod._outbound_proxy() == _PROXY
        assert gallerydl_mod._proxy_args() == ["--proxy", _PROXY]


async def test_gallerydl_argv_carries_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With OUTBOUND_PROXY_URL set, both gallery-dl phases get --proxy."""
    monkeypatch.setenv("OUTBOUND_PROXY_URL", _PROXY)
    calls: list[list[str]] = []

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
            assert "--proxy" in argv
            assert argv[argv.index("--proxy") + 1] == _PROXY
            # URL stays last; --proxy never leaks into error/log surface here.
            assert argv[-1] == "https://instagram.com/p/abc123"
    finally:
        import shutil

        tmp = next(
            p for p in result.image_paths[0].parents if p.name.startswith("spillthereel-gallerydl-")
        )
        shutil.rmtree(tmp, ignore_errors=True)


async def test_gallerydl_argv_without_proxy_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without proxy config the argv shape is exactly the legacy one."""
    monkeypatch.delenv("OUTBOUND_PROXY_URL", raising=False)
    calls: list[list[str]] = []

    class _FakeProcess:
        def __init__(self, dest: Path | None) -> None:
            self._dest = dest
            self.returncode: int | None = 0

        async def communicate(self, *_args: Any) -> tuple[bytes, bytes]:
            if self._dest is not None:
                target = self._dest / "a.jpg"
                target.write_bytes(b"\xff\xd8fake")
            return json.dumps([{"description": "cap"}]).encode(), b""

        def kill(self) -> None:
            pass  # pragma: no cover

        async def wait(self) -> int | None:
            return self.returncode

    async def _fake_create(*args: str, **kwargs: Any) -> _FakeProcess:
        arglist = list(args)
        calls.append(arglist)
        dest: Path | None = None
        if "--destination" in arglist:
            dest = Path(arglist[arglist.index("--destination") + 1])
        return _FakeProcess(dest)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)

    result = await GalleryDlExtractor("x").extract("https://x.com/u/status/1")
    try:
        assert calls[0][:2] == ["gallery-dl", "--dump-json"]
        assert all("--proxy" not in argv for argv in calls)
    finally:
        import shutil

        tmp = next(
            p for p in result.image_paths[0].parents if p.name.startswith("spillthereel-gallerydl-")
        )
        shutil.rmtree(tmp, ignore_errors=True)
