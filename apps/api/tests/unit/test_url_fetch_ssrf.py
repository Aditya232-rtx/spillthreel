"""Regressions for the accepted save URL and server-side fetch boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import AnyUrl

from app.api.v1.saves import _validate_save_url
from app.services.ingest import pipeline


def test_manual_save_requires_https_and_supported_platform() -> None:
    assert _validate_save_url(AnyUrl("https://www.instagram.com/reel/abc/")) == "instagram"

    with pytest.raises(pipeline.UnsupportedPlatformError, match="HTTPS"):
        _validate_save_url(AnyUrl("http://www.instagram.com/reel/abc/"))
    with pytest.raises(pipeline.UnsupportedPlatformError):
        _validate_save_url(AnyUrl("https://127.0.0.1/admin/"))


@pytest.mark.parametrize(
    ("url", "platform_name", "message"),
    [
        ("http://www.youtube.com/watch?v=abcdefghijk", "youtube", "HTTPS"),
        ("https://127.0.0.1/admin", "instagram", "allowed social platform"),
        ("https://www.instagram.com/p/abc/", "youtube", "platform does not match"),
    ],
)
def test_worker_revalidates_saved_fetch_target(
    url: str, platform_name: str, message: str
) -> None:
    item = SimpleNamespace(source_url=url, platform=platform_name)
    with pytest.raises(pipeline.PipelineError, match=message):
        pipeline._validate_source_url(item)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("fetch", ["oembed", "video_details"])
async def test_fixed_youtube_metadata_fetch_does_not_follow_redirects_or_use_env_proxy(
    monkeypatch: pytest.MonkeyPatch, fetch: str
) -> None:
    requested: list[str] = []
    client_options: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if len(requested) == 1:
            # A redirect to a loopback endpoint must remain a response from
            # the fixed public metadata host, never a second server request.
            return httpx.Response(
                302,
                headers={"location": "http://127.0.0.1:8080/latest/meta-data"},
                json={"title": "redirect response", "items": []},
            )
        return httpx.Response(200, json={"title": "loopback reached"})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def client_factory(**kwargs: Any) -> httpx.AsyncClient:
        client_options.append(dict(kwargs))
        return real_async_client(transport=transport, **kwargs)

    monkeypatch.setattr(pipeline.httpx, "AsyncClient", client_factory)
    if fetch == "oembed":
        result = await pipeline._fetch_oembed("https://www.youtube.com/watch?v=abcdefghijk")
        assert result.title == "redirect response"
    else:
        with pytest.raises(pipeline._YouTubeSourceGoneError):
            await pipeline._fetch_video_details("abcdefghijk", "test-key")

    assert len(requested) == 1
    assert "127.0.0.1" not in requested[0]
    assert len(client_options) == 1
    assert client_options[0]["follow_redirects"] is False
    assert client_options[0]["trust_env"] is False
