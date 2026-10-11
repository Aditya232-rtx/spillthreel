"""Dry-run tests for scripts/egress_check.py — no network.

Every network seam (`_scrape_post`, `_download_media_file`,
`_youtube_metadata`, `_youtube_full`, `_x_probe`) is monkeypatched, so these
tests prove CLI parsing, table output shape, summary counts, and exit codes
only. Secrets appear exclusively as env var NAMES, never values.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "egress_check.py"

_URLS = [
    "https://www.instagram.com/reel/AbCdEfGhIjK1/",
    "https://www.tiktok.com/@someuser/video/1234567890123456789",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://x.com/someuser/status/1234567890123456789",
]


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("egress_check", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["egress_check"] = module
    spec.loader.exec_module(module)
    return module


def _write_urls_file(tmp_path: Path, lines: list[str]) -> str:
    path = tmp_path / "urls.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _patch_all_network(
    monkeypatch: pytest.MonkeyPatch, module: Any, media_bytes: int = 1234
) -> None:
    async def _fake_download(media_url: str, dest_dir: Path, kind: str, timeout_s: float) -> Path:
        _ = (media_url, kind, timeout_s)
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / "fake-media.bin"
        target.write_bytes(b"x" * media_bytes)
        return target

    post = SimpleNamespace(media_type="video", video_url="https://cdn.example/v.mp4", image_urls=[])
    monkeypatch.setattr(module, "_scrape_post", AsyncMock(return_value=post))
    monkeypatch.setattr(module, "_download_media_file", _fake_download)
    monkeypatch.setattr(
        module,
        "_youtube_metadata",
        AsyncMock(
            return_value={
                "title": "T",
                "channel": "C",
                "channel_url": None,
                "description": None,
                "duration_seconds": None,
                "video_id": "dQw4w9WgXcQ",
                "used_data_api": False,
            }
        ),
    )
    monkeypatch.setattr(module, "_youtube_full", AsyncMock(return_value=None))
    monkeypatch.setattr(module, "_x_probe", AsyncMock(return_value={"title": "X"}))


def test_table_shape_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setenv("SCRAPER_PROVIDER", "scrapecreators")
    monkeypatch.setenv("SCRAPECREATORS_API_KEY", "dummy-presence-only")
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    _patch_all_network(monkeypatch, module)
    urls_file = _write_urls_file(tmp_path, ["# comment", "", *_URLS, "   "])

    code = module.main([urls_file, "--timeout", "5"])

    assert code == 0
    out = capsys.readouterr().out
    assert "url" in out.splitlines()[0] and "platform" in out.splitlines()[0]
    assert "extractor" in out.splitlines()[0] and "result" in out.splitlines()[0]
    assert "bytes" in out.splitlines()[0] and "secs" in out.splitlines()[0]
    for platform in ("instagram", "tiktok", "youtube", "x"):
        assert platform in out
    assert out.count("ok") >= 4
    assert "platforms:" in out
    assert "instagram=1" in out and "tiktok=1" in out
    assert "youtube=1" in out and "x=1" in out
    assert "results:" in out and "ok=4" in out


def _no_settings() -> Any:
    """Simulate 'no settings fallback' so key presence depends on env only.

    (The real `get_settings()` also reads apps/api/.env, which would make
    absence tests depend on the developer's local file.)
    """
    raise ValueError("settings unavailable in this test")


def test_missing_key_exit2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.setenv("SCRAPER_PROVIDER", "scrapecreators")
    monkeypatch.delenv("SCRAPECREATORS_API_KEY", raising=False)
    import app.settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_settings", _no_settings)
    urls_file = _write_urls_file(tmp_path, [_URLS[0]])

    code = module.main([urls_file])

    assert code == 2
    err = capsys.readouterr().err
    assert "SCRAPECREATORS_API_KEY" in err


def test_full_requires_gemini_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import app.settings as settings_mod

    monkeypatch.setattr(settings_mod, "get_settings", _no_settings)
    urls_file = _write_urls_file(tmp_path, [_URLS[2]])

    code = module.main([urls_file, "--full"])

    assert code == 2
    assert "GEMINI_API_KEY" in capsys.readouterr().err


def test_unsupported_platform_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load_module()
    urls_file = _write_urls_file(tmp_path, ["https://example.com/not-supported"])

    code = module.main([urls_file])

    assert code == 0
    out = capsys.readouterr().out
    assert "unsupported" in out
    assert "unsupported_platform" in out


def test_missing_file_exit2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_module()

    code = module.main([str(tmp_path / "does-not-exist.txt")])

    assert code == 2
    assert "cannot read" in capsys.readouterr().err
