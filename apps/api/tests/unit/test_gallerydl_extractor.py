"""gallery-dl extractor unit tests (app/services/extractors/gallerydl.py).

Subprocess is faked at `asyncio.create_subprocess_exec` — no network, no
`gallery-dl` binary, no creds. The fake optionally materializes image
files into the `--destination` dir so the download phase's file scan
runs for real. Returned tmpdirs are removed by each test (in prod the
pipeline owns result-path deletion per TRD §15.7).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.services.extractors import gallerydl as gallerydl_mod
from app.services.extractors.base import ExtractorError
from app.services.extractors.gallerydl import GalleryDlExtractor


@dataclass
class _FakeSpec:
    returncode: int = 0
    stdout: bytes = b""
    stderr: bytes = b""
    files: list[str] = field(default_factory=list)
    delay_seconds: float = 0.0


class _FakeProcess:
    def __init__(self, spec: _FakeSpec, dest: Path | None) -> None:
        self._spec = spec
        self._dest = dest
        self.returncode: int | None = spec.returncode
        self.killed = False

    async def communicate(self, *_args: Any) -> tuple[bytes, bytes]:
        if self._spec.delay_seconds:
            await asyncio.sleep(self._spec.delay_seconds)
        if self._dest is not None:
            for name in self._spec.files:
                path = self._dest / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"\xff\xd8fake-image-bytes")
        return self._spec.stdout, self._spec.stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int | None:
        return self.returncode


def _install_fake(monkeypatch: pytest.MonkeyPatch, specs: list[_FakeSpec]) -> list[list[str]]:
    """Patch subprocess creation; return the captured argv lists."""
    calls: list[list[str]] = []
    pending = list(specs)

    async def _fake_create(*args: str, **kwargs: Any) -> _FakeProcess:
        arglist = list(args)
        calls.append(arglist)
        spec = pending.pop(0) if pending else _FakeSpec()
        dest: Path | None = None
        if "--destination" in arglist:
            dest = Path(arglist[arglist.index("--destination") + 1])
        return _FakeProcess(spec, dest)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    return calls


def _metadata_stdout() -> bytes:
    return json.dumps(
        [
            {
                "description": "carousel caption here",
                "username": "someuser",
                "fullname": "Some User",
                "uploader_url": "https://instagram.com/someuser",
            }
        ]
    ).encode()


async def test_happy_path_returns_images_caption_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake(
        monkeypatch,
        [
            _FakeSpec(stdout=_metadata_stdout()),
            _FakeSpec(files=["instagram/someuser/a.jpg", "b.png", "c.webp"]),
        ],
    )
    result = await GalleryDlExtractor("instagram").extract("https://instagram.com/p/abc123")
    try:
        assert result.media_type == "image"
        assert result.video_path is None
        assert len(result.image_paths) == 3
        assert all(p.is_file() for p in result.image_paths)
        assert result.original_caption == "carousel caption here"
        assert result.original_owner is not None
        assert result.original_owner.username == "someuser"
        assert result.original_owner.name == "Some User"
        assert result.resolved_at is not None
        # Two phases: metadata dump first, download second.
        assert calls[0][:2] == ["gallery-dl", "--dump-json"]
        assert "--destination" in calls[1]
    finally:
        tmp = next(
            p for p in result.image_paths[0].parents if p.name.startswith("spillthereel-gallerydl-")
        )
        shutil.rmtree(tmp, ignore_errors=True)


async def test_timeout_raises_extractor_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gallerydl_mod, "_TIMEOUT_SECONDS", 0.05)
    _install_fake(monkeypatch, [_FakeSpec(delay_seconds=5.0)])
    with pytest.raises(ExtractorError, match="timed out"):
        await GalleryDlExtractor("instagram").extract("https://instagram.com/p/abc123")


async def test_nonzero_exit_raises_with_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake(
        monkeypatch,
        [_FakeSpec(returncode=1, stderr=b"error: 401 Unauthorized for gallery")],
    )
    with pytest.raises(ExtractorError, match=r"exit 1.*401 Unauthorized"):
        await GalleryDlExtractor("x").extract("https://x.com/user/status/123")


async def test_error_messages_redact_query_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake(
        monkeypatch,
        [_FakeSpec(returncode=1, stderr=b"error: login required")],
    )
    with pytest.raises(ExtractorError) as exc_info:
        await GalleryDlExtractor("instagram").extract(
            "https://instagram.com/p/abc123?auth_token=supersecret&x=1"
        )
    assert "supersecret" not in str(exc_info.value)
    assert "/p/abc123" in str(exc_info.value)


async def test_video_only_download_raises_not_mislabeled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake(
        monkeypatch,
        [
            _FakeSpec(stdout=_metadata_stdout()),
            _FakeSpec(files=["clip.mp4"]),
        ],
    )
    with pytest.raises(ExtractorError, match="video-only"):
        await GalleryDlExtractor("instagram").extract("https://instagram.com/p/abc123")
    assert len(calls) == 2


async def test_can_handle_scoped_to_image_platforms() -> None:
    assert await GalleryDlExtractor("instagram").can_handle("https://instagram.com/p/a")
    assert await GalleryDlExtractor("x").can_handle("https://x.com/u/status/1")
    assert not await GalleryDlExtractor("youtube").can_handle("https://youtu.be/a")
    assert not await GalleryDlExtractor("tiktok").can_handle("https://tiktok.com/@u/video/1")
