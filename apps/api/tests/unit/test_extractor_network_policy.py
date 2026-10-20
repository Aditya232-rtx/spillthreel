"""Network policy regressions for extractors which fetch URLs themselves."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.extractors import registry as registry_mod
from app.services.extractors.base import ExtractorError, NoExtractorSucceededError
from app.services.extractors.gallerydl import GalleryDlExtractor, _redact_url
from app.services.extractors.scraper.base import SourceGoneError
from app.services.extractors.ytdlp import YtDlpExtractor


async def test_registry_does_not_log_or_raise_signed_source_url() -> None:
    source_url = "https://instagram.com/p/abc?signature=signed-secret"

    class _FailingExtractor:
        platform = "instagram"

        async def can_handle(self, url: str) -> bool:
            return True

        async def extract(self, url: str):
            raise ExtractorError(f"failed for {url}; proxy=http://user:pass@proxy")

    with (
        patch.object(registry_mod, "_registry", {"instagram": [_FailingExtractor()]}),
        patch.object(registry_mod._logger, "warning") as warning,
        pytest.raises(NoExtractorSucceededError) as exc_info,
    ):
        await registry_mod.extract(source_url)

    logged = repr(warning.call_args)
    raised = str(exc_info.value)
    for sensitive in (source_url, "signed-secret", "user:pass", "proxy"):
        assert sensitive not in logged
        assert sensitive not in raised
    assert "instagram" in raised


async def test_registry_sanitizes_source_gone_error() -> None:
    secret_url = "https://instagram.com/p/abc?signature=signed-secret"

    class _GoneExtractor:
        platform = "instagram"

        async def can_handle(self, url: str) -> bool:
            return True

        async def extract(self, url: str):
            raise SourceGoneError(f"gone at {secret_url}")

    with (
        patch.object(registry_mod, "_registry", {"instagram": [_GoneExtractor()]}),
        patch.object(registry_mod._logger, "info") as info,
        pytest.raises(SourceGoneError) as exc_info,
    ):
        await registry_mod.extract(secret_url)

    assert "signed-secret" not in repr(info.call_args)
    assert "signed-secret" not in str(exc_info.value)


def test_gallerydl_redaction_removes_url_userinfo_and_signed_query() -> None:
    redacted = _redact_url(
        "https://private-user:private-password@instagram.com:8443/p/abc?signature=signed-secret"
    )

    assert redacted == "https://instagram.com:8443/p/abc"
    for sensitive in ("private-user", "private-password", "signed-secret"):
        assert sensitive not in redacted


@pytest.mark.parametrize(
    "proxy",
    ["socks5://proxy.internal:1080", "http://proxy:0", "http://proxy/path", "http://proxy/?x=1"],
)
async def test_ytdlp_rejects_non_explicit_proxy_endpoint_before_network(
    monkeypatch: pytest.MonkeyPatch, proxy: str
) -> None:
    monkeypatch.setenv("EXTRACTOR_EGRESS_PROXY_URL", proxy)
    with pytest.raises(ExtractorError, match="proxy configuration is invalid"):
        await YtDlpExtractor("youtube").extract("https://youtube.com/watch?v=abc")


@pytest.mark.parametrize(
    "proxy",
    ["socks5://proxy.internal:1080", "http://proxy:0", "http://proxy/path", "http://proxy/?x=1"],
)
async def test_gallerydl_rejects_non_explicit_proxy_endpoint_before_subprocess(
    monkeypatch: pytest.MonkeyPatch, proxy: str
) -> None:
    monkeypatch.setenv("EXTRACTOR_EGRESS_PROXY_URL", proxy)
    with pytest.raises(ExtractorError, match="proxy configuration is invalid"):
        await GalleryDlExtractor("instagram").extract("https://instagram.com/p/abc")


@pytest.mark.parametrize(
    ("extractor", "url"),
    [
        (YtDlpExtractor("youtube"), "https://127.0.0.1/admin"),
        (GalleryDlExtractor("instagram"), "https://127.0.0.1/admin"),
        (GalleryDlExtractor("x"), "https://instagram.com/p/abc"),
    ],
)
async def test_network_extractors_reject_url_outside_assigned_platform(
    monkeypatch: pytest.MonkeyPatch, extractor: object, url: str
) -> None:
    monkeypatch.setenv("EXTRACTOR_EGRESS_PROXY_URL", "http://proxy.internal:8080")
    with pytest.raises(ExtractorError, match="URL does not match"):
        await extractor.extract(url)  # type: ignore[attr-defined]
