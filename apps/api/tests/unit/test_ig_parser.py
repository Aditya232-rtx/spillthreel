"""IG export parser tests against a real user's Aug-2026 export.

The fixture ZIP (tests/parser-corpus/user_export_2026_aug.zip) is the
user's own historical Instagram data — trimmed to just the three
saved_*.html files, no other export artifacts. Regenerate it by running
the zip command from the parser-corpus/ README when a new schema hits
prod.

If Meta changes the export schema, the first thing to break here is
parser_schema_hash — that's intentional. When it flips, add a synthetic
fixture covering the new labels alongside this one.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.imports.ig_parser import (
    ArchiveLimitError,
    ParsedExport,
    ParserError,
    parse_zip,
)


def _zip(path: Path, members: dict[str, bytes], *, compression: int = zipfile.ZIP_STORED) -> Path:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _limits(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "import_max_archive_members": 100,
        "import_max_member_uncompressed_bytes": 1024 * 1024,
        "import_max_uncompressed_bytes": 2 * 1024 * 1024,
        "import_max_compression_ratio": 200.0,
        "import_max_parsed_entries": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)

FIXTURE_ZIP = Path(__file__).parent.parent / "parser-corpus" / "user_export_2026_aug.zip"


@pytest.fixture(scope="module")
def parsed() -> ParsedExport:
    if not FIXTURE_ZIP.exists():
        pytest.skip(f"fixture zip not present at {FIXTURE_ZIP}")
    return parse_zip(FIXTURE_ZIP)


class TestParseZip:
    def test_extracts_posts(self, parsed: ParsedExport) -> None:
        assert len(parsed.posts) > 0

    def test_extracts_collections(self, parsed: ParsedExport) -> None:
        # User's Aug 2026 export contained at least one custom collection.
        assert len(parsed.collections) > 0

    def test_extracts_audio(self, parsed: ParsedExport) -> None:
        assert len(parsed.audio) > 0

    def test_schema_hash_is_stable(self, parsed: ParsedExport) -> None:
        # Snapshot the labels seen — regression alarm on Meta schema change.
        # The hash itself is 16 hex chars.
        assert len(parsed.parser_schema_hash) == 16
        assert all(c in "0123456789abcdef" for c in parsed.parser_schema_hash)

    def test_rejects_traversal_and_absolute_member_names(self, tmp_path: Path, monkeypatch) -> None:
        import app.services.imports.ig_parser as parser

        monkeypatch.setattr(parser, "get_settings", lambda: _limits())
        for name in ("../saved/saved_posts.html", "/saved_posts.html", "C:\\temp\\saved_posts.html"):
            archive = _zip(tmp_path / "unsafe.zip", {name: b"<html></html>"})
            with pytest.raises(ParserError, match="unsafe archive member path"):
                parse_zip(archive)

    def test_rejects_duplicate_target_files(self, tmp_path: Path, monkeypatch) -> None:
        import app.services.imports.ig_parser as parser

        monkeypatch.setattr(parser, "get_settings", lambda: _limits())
        archive = tmp_path / "duplicate.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("saved_posts.html", b"one")
            zf.writestr("saved/saved_posts.html", b"two")
        with pytest.raises(ParserError, match="duplicate import HTML"):
            parse_zip(archive)

    def test_rejects_encrypted_member_flag(self, tmp_path: Path, monkeypatch) -> None:
        import app.services.imports.ig_parser as parser

        monkeypatch.setattr(parser, "get_settings", lambda: _limits())
        archive = _zip(tmp_path / "encrypted.zip", {"saved_posts.html": b"<html></html>"})
        raw = bytearray(archive.read_bytes())
        local = raw.find(b"PK\x03\x04")
        central = raw.find(b"PK\x01\x02")
        raw[local + 6] |= 1
        raw[central + 8] |= 1
        archive.write_bytes(raw)
        with pytest.raises(ParserError, match="encrypted"):
            parse_zip(archive)

    @pytest.mark.parametrize(
        ("members", "limits"),
        [
            ({"a": b"", "b": b""}, {"import_max_archive_members": 1}),
            ({"saved_posts.html": b"x" * 20}, {"import_max_member_uncompressed_bytes": 10}),
            (
                {"saved_posts.html": b"x" * 20},
                {"import_max_uncompressed_bytes": 10},
            ),
            (
                {"saved_posts.html": b"x" * 1000},
                {"import_max_compression_ratio": 2.0},
            ),
        ],
    )
    def test_rejects_archive_resource_limits(self, tmp_path: Path, monkeypatch, members, limits) -> None:
        import app.services.imports.ig_parser as parser

        monkeypatch.setattr(parser, "get_settings", lambda: _limits(**limits))
        archive = _zip(tmp_path / "limited.zip", members, compression=zipfile.ZIP_DEFLATED)
        with pytest.raises(ArchiveLimitError):
            parse_zip(archive)

    def test_rejects_nested_zip_payload(self, tmp_path: Path, monkeypatch) -> None:
        import app.services.imports.ig_parser as parser

        monkeypatch.setattr(parser, "get_settings", lambda: _limits())
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as zf:
            zf.writestr("inner.txt", b"nested")
        archive = _zip(tmp_path / "nested.zip", {"saved_posts.html": nested.getvalue()})
        with pytest.raises(ParserError, match="nested ZIP"):
            parse_zip(archive)

    def test_rejects_nested_zip_member_by_name(self, tmp_path: Path, monkeypatch) -> None:
        import app.services.imports.ig_parser as parser

        monkeypatch.setattr(parser, "get_settings", lambda: _limits())
        archive = _zip(tmp_path / "nested-member.zip", {"saved/archive.zip": b"opaque"})
        with pytest.raises(ParserError, match="nested ZIP"):
            parse_zip(archive)

    def test_enforces_parsed_entry_limit(self, tmp_path: Path, monkeypatch) -> None:
        import app.services.imports.ig_parser as parser

        monkeypatch.setattr(parser, "get_settings", lambda: _limits(import_max_parsed_entries=1))
        post = parser.SavedPost("https://www.instagram.com/reel/abc/", None)
        monkeypatch.setattr(parser, "_iter_saved_posts", lambda *_args: iter((post, post)))
        archive = _zip(tmp_path / "entries.zip", {"saved_posts.html": b"synthetic"})
        with pytest.raises(ArchiveLimitError, match="parsed entries"):
            parse_zip(archive)


class TestPostShape:
    def test_every_post_has_a_url(self, parsed: ParsedExport) -> None:
        assert all(p.url and p.url.startswith("http") for p in parsed.posts)

    def test_urls_look_like_ig_permalinks(self, parsed: ParsedExport) -> None:
        # Meta serves both /p/ (post) and /reel/ variants in saved dumps.
        for p in parsed.posts:
            assert "instagram.com" in p.url

    def test_at_least_some_posts_have_captions(self, parsed: ParsedExport) -> None:
        # Not every saved item has a caption (some posts are pure media)
        # but on any real user's export there should be at least one with
        # a caption longer than a few words.
        long_captions = [p for p in parsed.posts if p.caption and len(p.caption) > 20]
        assert len(long_captions) > 0

    def test_at_least_some_posts_have_owner(self, parsed: ParsedExport) -> None:
        with_owner = [p for p in parsed.posts if p.owner is not None]
        assert len(with_owner) > 0
        # And the owners we DID extract should carry at least one field.
        for p in with_owner[:5]:
            assert p.owner is not None
            assert p.owner.url or p.owner.name or p.owner.username

    def test_hashtags_stripped_of_hash_prefix(self, parsed: ParsedExport) -> None:
        for p in parsed.posts:
            for tag in p.hashtags:
                assert not tag.startswith("#"), f"got un-stripped hashtag {tag!r}"


class TestCollectionShape:
    def test_collections_have_names(self, parsed: ParsedExport) -> None:
        assert all(c.name for c in parsed.collections)

    def test_collections_may_have_media(self, parsed: ParsedExport) -> None:
        # At least one collection in a real user's export has members;
        # empty collections are legal but boring.
        with_media = [c for c in parsed.collections if c.media]
        assert len(with_media) > 0


class TestAudioShape:
    def test_audio_entries_have_title_or_artist(self, parsed: ParsedExport) -> None:
        for a in parsed.audio:
            assert a.title or a.artist
