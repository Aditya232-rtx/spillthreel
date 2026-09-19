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
        assert normalize_url("  https://instagram.com/reel/abc/  ") == "https://instagram.com/reel/abc"

    def test_dedup_key_stable(self) -> None:
        """Same reel from different share paths should collapse to one key."""
        a = normalize_url("https://www.instagram.com/reel/abc?igsh=xyz")
        b = normalize_url("https://www.instagram.com/reel/abc/")
        c = normalize_url("https://www.instagram.com/reel/abc?utm_source=share")
        assert a == b == c
