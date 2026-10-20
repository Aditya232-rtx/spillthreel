"""Adversarial, synthetic ZIP cases for the import parser safety boundary."""

from __future__ import annotations

import io
import struct
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.imports import ig_parser
from app.services.imports.ig_parser import ParserError, parse_zip


def _post_html(count: int = 1) -> bytes:
    entries = []
    for index in range(count):
        entries.append(
            "<div class='pam uiBoxWhite noborder'><table>"
            "<tr><td>URL</td><td><a href='https://www.instagram.com/p/abc{index}/'>link</a></td></tr>"
            "</table></div>".format(index=index)
        )
    return ("<html><body>" + "".join(entries) + "</body></html>").encode()


def _zip_bytes(entries: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
    return output.getvalue()


def _parse_bytes(tmp_path: Path, data: bytes):
    archive_path = tmp_path / "hostile.zip"
    archive_path.write_bytes(data)
    return parse_zip(archive_path)


def _limits(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "import_max_archive_members": 10_000,
        "import_max_member_uncompressed_bytes": 64 * 1024 * 1024,
        "import_max_uncompressed_bytes": 250 * 1024 * 1024,
        "import_max_compression_ratio": 200.0,
        "import_max_parsed_entries": 10_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def small_limits(monkeypatch: pytest.MonkeyPatch):
    def set_limits(**overrides: object) -> None:
        monkeypatch.setattr(ig_parser, "get_settings", lambda: _limits(**overrides))

    set_limits()
    return set_limits


@pytest.mark.parametrize("member_name", ["../saved_posts.html", "/saved_posts.html", "C:/saved_posts.html"])
def test_rejects_traversal_or_absolute_member_paths(tmp_path: Path, member_name: str) -> None:
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, _zip_bytes([(member_name, _post_html())]))


def test_rejects_encrypted_entries(tmp_path: Path) -> None:
    archive = bytearray(_zip_bytes([("saved/saved_posts.html", _post_html())]))
    # Set general-purpose bit 0 in both the local and central directory headers.
    offset = 0
    while offset < len(archive) - 4:
        signature = struct.unpack_from("<I", archive, offset)[0]
        if signature == 0x04034B50:
            flags_offset = offset + 6
            flags = struct.unpack_from("<H", archive, flags_offset)[0]
            struct.pack_into("<H", archive, flags_offset, flags | 0x1)
        elif signature == 0x02014B50:
            flags_offset = offset + 8
            flags = struct.unpack_from("<H", archive, flags_offset)[0]
            struct.pack_into("<H", archive, flags_offset, flags | 0x1)
        offset += 1

    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, bytes(archive))


def test_rejects_duplicate_target_html_entries(tmp_path: Path) -> None:
    payload = _post_html()
    with pytest.raises(ParserError):
        _parse_bytes(
            tmp_path,
            _zip_bytes([("saved/saved_posts.html", payload), ("saved_posts.html", payload)]),
        )


def test_rejects_nested_zip_member(tmp_path: Path) -> None:
    nested = _zip_bytes([("saved_posts.html", _post_html())])
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, _zip_bytes([("nested.zip", nested)]))


def test_rejects_truncated_or_malformed_archive(tmp_path: Path) -> None:
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, b"PK\x03\x04truncated central directory")


def test_enforces_archive_member_count(tmp_path: Path, small_limits) -> None:
    small_limits(import_max_archive_members=2)
    archive = _zip_bytes(
        [("saved/saved_posts.html", _post_html()), ("readme.txt", b"a"), ("notes.txt", b"b")]
    )
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, archive)


def test_enforces_single_member_expansion_ceiling(tmp_path: Path, small_limits) -> None:
    small_limits(import_max_member_uncompressed_bytes=128)
    archive = _zip_bytes([("saved/saved_posts.html", b"A" * 129)])
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, archive)


def test_enforces_total_expansion_ceiling(tmp_path: Path, small_limits) -> None:
    small_limits(import_max_member_uncompressed_bytes=128, import_max_uncompressed_bytes=180)
    archive = _zip_bytes([("one.bin", b"A" * 100), ("two.bin", b"B" * 100)])
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, archive)


def test_enforces_compression_ratio_ceiling(tmp_path: Path, small_limits) -> None:
    small_limits(import_max_compression_ratio=5.0)
    archive = _zip_bytes([("saved/saved_posts.html", b"Z" * 20_000)])
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, archive)


def test_enforces_parsed_entry_count(tmp_path: Path, small_limits, monkeypatch: pytest.MonkeyPatch) -> None:
    small_limits(import_max_parsed_entries=1)
    monkeypatch.setattr(
        ig_parser,
        "_iter_saved_posts",
        lambda _html, _labels: iter(
            [
                ig_parser.SavedPost("https://www.instagram.com/p/one/", None),
                ig_parser.SavedPost("https://www.instagram.com/p/two/", None),
            ]
        ),
    )
    archive = _zip_bytes([("saved/saved_posts.html", _post_html(count=2))])
    with pytest.raises(ParserError):
        _parse_bytes(tmp_path, archive)

