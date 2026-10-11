"""Unit tests for platform_detect + normalize_url.

Kept fast — no fixtures, no I/O. If the classifier ever needs a
platform-specific rule (e.g. tiktok /photo/ URLs behave differently
from /video/), it goes here first.
"""

from __future__ import annotations

import pytest

from app.services.ingest.platform_detect import (
    UnsupportedPlatformError,
    detect_platform,
    is_shorts_url,
    normalize_url,
)


class TestDetectPlatform:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://www.instagram.com/reel/DbM7eWINqAy/", "instagram"),
            ("https://instagram.com/p/CxYz123/", "instagram"),
            ("https://www.tiktok.com/@user/video/12345", "tiktok"),
            ("https://vm.tiktok.com/ZM123/", "tiktok"),
            ("https://www.youtube.com/shorts/abc123", "youtube"),
            ("https://youtu.be/abc123", "youtube"),
            ("https://youtube.com/watch?v=abc123", "youtube"),
            ("https://x.com/user/status/12345", "x"),
            ("https://twitter.com/user/status/12345", "x"),
        ],
    )
    def test_supported_platforms(self, url: str, expected: str) -> None:
        assert detect_platform(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://linkedin.com/posts/user_abc",
            "https://facebook.com/reel/123",
            "https://reddit.com/r/videos/comments/abc",
            "https://vimeo.com/12345",
            "https://google.com",
        ],
    )
    def test_unsupported_platforms_raise(self, url: str) -> None:
        with pytest.raises(UnsupportedPlatformError):
            detect_platform(url)

    def test_missing_host_raises(self) -> None:
        with pytest.raises(UnsupportedPlatformError):
            detect_platform("not-a-url")

    def test_case_insensitive_host(self) -> None:
        assert detect_platform("https://INSTAGRAM.COM/reel/abc") == "instagram"

    def test_strips_userinfo(self) -> None:
        assert detect_platform("https://user:pass@instagram.com/reel/abc") == "instagram"


class TestNormalizeUrl:
    def test_forces_https(self) -> None:
        assert normalize_url("http://instagram.com/reel/abc").startswith("https://")

    def test_lowercases_host(self) -> None:
        assert "instagram.com" in normalize_url("https://INSTAGRAM.COM/reel/abc")

    def test_drops_query_and_fragment(self) -> None:
        raw = "https://instagram.com/reel/abc?igsh=xyz&utm=foo#anchor"
        assert normalize_url(raw) == "https://instagram.com/reel/abc"

    def test_drops_trailing_slash(self) -> None:
        assert normalize_url("https://instagram.com/reel/abc/") == "https://instagram.com/reel/abc"

    def test_preserves_root_slash(self) -> None:
        # A URL with just a host should keep `/` as the path — never empty.
        assert normalize_url("https://instagram.com").endswith("/")

    def test_strips_userinfo(self) -> None:
        assert "user:pass@" not in normalize_url("https://user:pass@instagram.com/reel/abc")

    def test_stripping_whitespace(self) -> None:
        assert (
            normalize_url("  https://instagram.com/reel/abc/  ") == "https://instagram.com/reel/abc"
        )

    def test_dedup_key_stable(self) -> None:
        """Same reel from different share paths should collapse to one key."""
        a = normalize_url("https://www.instagram.com/reel/abc?igsh=xyz")
        b = normalize_url("https://www.instagram.com/reel/abc/")
        c = normalize_url("https://www.instagram.com/reel/abc?utm_source=share")
        assert a == b == c


YT_ID = "dQw4w9WgXcQ"
YT_ID_2 = "9bZkp7q19f0"
YT_CANON = f"https://www.youtube.com/watch?v={YT_ID}"


class TestNormalizeYoutube:
    """Every plain-video YouTube share form → one canonical dedup key."""

    @pytest.mark.parametrize(
        "raw",
        [
            f"https://www.youtube.com/watch?v={YT_ID}",
            f"https://youtube.com/watch?v={YT_ID}",
            f"http://www.youtube.com/watch?v={YT_ID}",
            f"https://WWW.YOUTUBE.COM/watch?v={YT_ID}",
            f"https://www.youtube.com/watch?v={YT_ID}&t=30s",
            f"https://www.youtube.com/watch?t=30s&v={YT_ID}&list=PLxyz",
            f"https://www.youtube.com/watch?v={YT_ID}&list=PLxyz&index=4",
            f"https://youtu.be/{YT_ID}",
            f"https://youtu.be/{YT_ID}?si=abc123&t=30",
            f"https://www.youtube.com/shorts/{YT_ID}",
            f"https://www.youtube.com/shorts/{YT_ID}/",
            f"https://www.youtube.com/shorts/{YT_ID}?feature=share",
            f"https://m.youtube.com/watch?v={YT_ID}",
            f"https://m.youtube.com/shorts/{YT_ID}",
            f"https://music.youtube.com/watch?v={YT_ID}",
        ],
    )
    def test_all_forms_collapse_to_one_key(self, raw: str) -> None:
        assert normalize_url(raw) == YT_CANON

    def test_distinct_ids_stay_distinct(self) -> None:
        assert normalize_url(f"https://youtu.be/{YT_ID}") != normalize_url(
            f"https://youtu.be/{YT_ID_2}"
        )

    def test_shorts_and_watch_of_same_id_share_key(self) -> None:
        assert normalize_url(f"https://www.youtube.com/shorts/{YT_ID}") == normalize_url(
            f"https://www.youtube.com/watch?v={YT_ID}"
        )


class TestNormalizeYoutubeGarbage:
    """Missing/garbage ids fall back to generic normalization — never crash."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("https://www.youtube.com/watch", "https://www.youtube.com/watch"),
            ("https://www.youtube.com/watch?list=PLxyz", "https://www.youtube.com/watch"),
            (f"https://www.youtube.com/watch?v={YT_ID}&v={YT_ID_2}", YT_CANON),  # first v wins
            ("https://www.youtube.com/watch?v=", "https://www.youtube.com/watch"),
            ("https://www.youtube.com/watch?v=short", "https://www.youtube.com/watch"),
            ("https://www.youtube.com/watch?v=!!!not-an-id!!!", "https://www.youtube.com/watch"),
            ("https://www.youtube.com/shorts/", "https://www.youtube.com/shorts"),
            ("https://www.youtube.com/shorts/abc", "https://www.youtube.com/shorts/abc"),
            (
                "https://www.youtube.com/embed/dQw4w9WgXcQ",
                "https://www.youtube.com/embed/dQw4w9WgXcQ",
            ),
            ("https://youtu.be/", "https://youtu.be/"),
            ("https://youtu.be/abc", "https://youtu.be/abc"),
        ],
    )
    def test_fallback_to_generic(self, raw: str, expected: str) -> None:
        assert normalize_url(raw) == expected

    @pytest.mark.parametrize("raw", ["not-a-url", "", "https://", "://bad"])
    def test_garbage_never_crashes(self, raw: str) -> None:
        assert isinstance(normalize_url(raw), str)


class TestIsShortsUrl:
    @pytest.mark.parametrize(
        "raw",
        [
            f"https://www.youtube.com/shorts/{YT_ID}",
            f"https://www.youtube.com/shorts/{YT_ID}/",
            f"https://www.youtube.com/shorts/{YT_ID}?feature=share",
            f"https://m.youtube.com/shorts/{YT_ID}",
            "https://www.youtube.com/shorts/",
        ],
    )
    def test_shorts_forms_are_shorts(self, raw: str) -> None:
        assert is_shorts_url(raw) is True

    @pytest.mark.parametrize(
        "raw",
        [
            f"https://www.youtube.com/watch?v={YT_ID}",
            f"https://youtu.be/{YT_ID}",
            f"https://music.youtube.com/watch?v={YT_ID}",
            "https://www.instagram.com/reel/abc",
            "https://www.youtube.com/watch",
        ],
    )
    def test_non_shorts_forms_are_not_shorts(self, raw: str) -> None:
        assert is_shorts_url(raw) is False

    @pytest.mark.parametrize("raw", ["not-a-url", "", "https://", "://bad"])
    def test_garbage_never_crashes(self, raw: str) -> None:
        assert is_shorts_url(raw) is False


class TestNormalizeInstagram:
    """Tracking params stripped; /reel/ vs /reels/ vs /p/ vs /tv/ preserved."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            (
                "https://instagram.com/reel/abc?igsh=xyz&utm_source=share#anchor",
                "https://instagram.com/reel/abc",
            ),
            (
                "https://www.instagram.com/reels/abc?igsh=xyz",
                "https://www.instagram.com/reels/abc",
            ),
            (
                "https://instagram.com/p/CxYz123/?utm_medium=share",
                "https://instagram.com/p/CxYz123",
            ),
            ("https://instagram.com/tv/AbC123/?igsh=xyz", "https://instagram.com/tv/AbC123"),
        ],
    )
    def test_paths_preserved_params_stripped(self, raw: str, expected: str) -> None:
        assert normalize_url(raw) == expected

    def test_reels_not_folded_to_reel(self) -> None:
        """Codebase treats them as distinct shapes — dedup must too."""
        assert normalize_url("https://instagram.com/reels/abc") != normalize_url(
            "https://instagram.com/reel/abc"
        )

    def test_shortcode_case_preserved(self) -> None:
        assert normalize_url("https://instagram.com/reel/DbM7eWINqAy/").endswith("/DbM7eWINqAy")


class TestNormalizeIdempotent:
    @pytest.mark.parametrize(
        "raw",
        [
            f"https://www.youtube.com/watch?v={YT_ID}&list=PLxyz",
            f"https://youtu.be/{YT_ID}?si=abc",
            f"https://m.youtube.com/shorts/{YT_ID}/",
            f"https://music.youtube.com/watch?v={YT_ID}",
            "https://www.youtube.com/watch?list=PLxyz",
            "https://youtu.be/abc",
            "https://instagram.com/reel/abc?igsh=xyz",
            "https://www.instagram.com/reels/abc?utm_source=share",
            "https://instagram.com/p/CxYz123/",
            "https://instagram.com/tv/AbC123/?igsh=xyz",
            "https://www.tiktok.com/@user/video/12345?foo=bar",
            "https://x.com/user/status/12345?foo=bar",
        ],
    )
    def test_double_normalize_is_stable(self, raw: str) -> None:
        assert normalize_url(normalize_url(raw)) == normalize_url(raw)
