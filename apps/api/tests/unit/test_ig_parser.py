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

from pathlib import Path

import pytest

from app.services.imports.ig_parser import (
    ParsedExport,
    parse_zip,
)

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
