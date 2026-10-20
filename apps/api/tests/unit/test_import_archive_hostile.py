"""Hostile synthetic ZIP regressions for the Instagram import parser."""

from __future__ import annotations

import io
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.imports import ig_parser
from app.services.imports.ig_parser import ArchiveLimitError, ParserError, parse_zip


def _archive(path: Path, members: list[tuple[str, bytes]]) -> Path:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, payload in members:
                zf.writestr(name, payload)
    return path


def _settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "import_max_archive_members": 20,
        "import_max_member_uncompressed_bytes": 1024,
        "import_max_uncompressed_bytes": 2048,
        "import_max_compression_ratio": 10_000.0,
        "import_max_parsed_entries": 20,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch):
    def configure(**overrides: object) -> None:
        monkeypatch.setattr(ig_parser, "get_settings", lambda: _settings(**overrides))

    configure()
    return configure


@pytest.mark.parametrize(
    "member_name",
    ["..\\saved_posts.html", "saved\\..\\saved_posts.html", "folder/../saved_posts.html"],
)
def test_rejects_posix_and_windows_traversal_names(
    tmp_path: Path, limits, member_name: str
) -> None:
    archive = _archive(tmp_path / "traversal.zip", [(member_name, b"<html></html>")])

    with pytest.raises(ParserError, match="unsafe archive member path"):
        parse_zip(archive)


def test_rejects_member_count_over_small_configured_limit(tmp_path: Path, limits) -> None:
    limits(import_max_archive_members=2)
    archive = _archive(
        tmp_path / "member-count.zip",
        [("one.txt", b"1"), ("two.txt", b"2"), ("three.txt", b"3")],
    )

    with pytest.raises(ArchiveLimitError, match="too many members"):
        parse_zip(archive)


def test_rejects_high_compression_ratio_without_large_fixture(tmp_path: Path, limits) -> None:
    limits(import_max_compression_ratio=2.0)
    archive = _archive(tmp_path / "ratio.zip", [("saved_posts.html", b"Z" * 256)])

    with pytest.raises(ArchiveLimitError, match="compression ratio"):
        parse_zip(archive)


def test_actual_decompressed_bytes_cannot_exceed_limit_when_declared_size_is_forged(
    tmp_path: Path, limits, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A low declared size must not hide bytes returned by the decompressor."""
    limits(import_max_member_uncompressed_bytes=64, import_max_uncompressed_bytes=128)
    archive = tmp_path / "forged-size.zip"
    archive.touch()
    info = zipfile.ZipInfo("saved_posts.html")
    # Model a central directory claiming one byte while the stream produces
    # more. ZipExtFile normally honors the declared length, so this fake
    # boundary exercises the defensive cap against a decompressor overrun.
    info.file_size = 1
    info.compress_size = 1

    class ForgedArchive:
        def __init__(self, _path: Path) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def infolist(self):
            return [info]

        def open(self, _info):
            return io.BytesIO(b"<html>" + b"A" * 512 + b"</html>")

    monkeypatch.setattr(ig_parser.zipfile, "ZipFile", ForgedArchive)
    with pytest.raises(ArchiveLimitError, match="expanded beyond limit"):
        parse_zip(archive)

