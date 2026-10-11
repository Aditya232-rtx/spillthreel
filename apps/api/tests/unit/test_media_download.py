"""SSRF-hardened media downloader tests (app/services/media/download.py).

Zero network: DNS (`socket.getaddrinfo`) and HTTP (httpx.MockTransport)
are both stubbed. If either stub is missing the test fails instead of
hitting the real network.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.services.extractors.base import ExtractorError
from app.services.media import download as download_mod

_PUBLIC_IP = "93.184.216.34"
_PUBLIC_IP_2 = "142.250.72.14"
_PRIVATE_IP = "10.0.0.5"
_LOOPBACK_IP = "127.0.0.1"
_METADATA_IP = "169.254.169.254"
_V6_LOOPBACK = "::1"
_V6_LINKLOCAL = "fe80::1"

_SIGNED_URL = "https://allowed.test/video?token=super-secret-signed-value"
_HOST = "allowed.test"


def _addrinfo(ip_str: str, port: int = 443) -> list[Any]:
    if ":" in ip_str:
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (ip_str, port, 0, 0),
            )
        ]
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip_str, port))]


def _dns_single(ip_str: str) -> Any:
    def _fake(
        host: str | None,
        port: str | int | None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        resolved_port = int(port) if port is not None else 443
        return _addrinfo(ip_str, resolved_port)

    return _fake


def _dns_router(mapping: dict[str, str]) -> Any:
    def _fake(
        host: str | None,
        port: str | int | None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        assert host is not None
        key = host.lower().rstrip(".")
        ip_str = mapping.get(key, _PUBLIC_IP)
        resolved_port = int(port) if port is not None else 443
        return _addrinfo(ip_str, resolved_port)

    return _fake


def _client_with(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def _factory(timeout: httpx.Timeout) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=timeout,
            follow_redirects=False,
        )

    monkeypatch.setattr(download_mod, "_build_client", _factory)


@pytest.fixture()
def allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDIA_HOST_ALLOWLIST", "allowed.test,cdn.example.com,evil-meta.test")


def _ok_video(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"x" * 1024)


async def test_happy_path_video(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))
    _client_with(monkeypatch, _ok_video)
    out = await download_mod.download_media(_SIGNED_URL, tmp_path, kind="video")
    assert out.parent == tmp_path
    assert out.is_file()
    assert out.read_bytes() == b"x" * 1024
    assert out.suffix == ".mp4"
    # No temp orphans left behind.
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


async def test_rejects_non_https(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))
    _client_with(monkeypatch, _ok_video)
    with pytest.raises(ExtractorError) as exc:
        await download_mod.download_media("http://allowed.test/video", tmp_path)
    assert _HOST in str(exc.value)
    assert "http://allowed.test/video" not in str(exc.value)
    assert list(tmp_path.iterdir()) == []


async def test_rejects_disallowed_host(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))
    _client_with(monkeypatch, _ok_video)
    secret_url = "https://evil.example.com/payload?token=abc123"
    with pytest.raises(ExtractorError) as exc:
        await download_mod.download_media(secret_url, tmp_path)
    assert "evil.example.com" in str(exc.value)
    assert secret_url not in str(exc.value)
    assert "abc123" not in str(exc.value)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad_ip", [_PRIVATE_IP, _LOOPBACK_IP, _V6_LOOPBACK, _V6_LINKLOCAL])
async def test_rejects_non_global_ips(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_ip: str
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(bad_ip))
    _client_with(monkeypatch, _ok_video)
    with pytest.raises(ExtractorError) as exc:
        await download_mod.download_media(_SIGNED_URL, tmp_path)
    assert _HOST in str(exc.value)
    assert "super-secret-signed-value" not in str(exc.value)
    assert list(tmp_path.iterdir()) == []


async def test_rejects_metadata_ip_redirect(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _dns_router({_HOST: _PUBLIC_IP, "evil-meta.test": _METADATA_IP}),
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "allowed.test":
            return httpx.Response(
                302,
                headers={"location": "https://evil-meta.test/latest/meta-data?token=zzz"},
            )
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"x")

    _client_with(monkeypatch, _handler)
    with pytest.raises(ExtractorError) as exc:
        await download_mod.download_media(_SIGNED_URL, tmp_path)
    assert "evil-meta.test" in str(exc.value)
    assert "zzz" not in str(exc.value)
    assert "super-secret-signed-value" not in str(exc.value)
    assert list(tmp_path.iterdir()) == []


async def test_rejects_redirect_chain_over_three(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://allowed.test/next-hop"})

    _client_with(monkeypatch, _handler)
    with pytest.raises(ExtractorError, match="too many redirects"):
        await download_mod.download_media(_SIGNED_URL, tmp_path)
    assert list(tmp_path.iterdir()) == []


async def test_rejects_oversize_streamed_body(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"y" * 4096)

    _client_with(monkeypatch, _handler)
    with pytest.raises(ExtractorError, match="too large"):
        await download_mod.download_media(_SIGNED_URL, tmp_path, kind="video", max_mb=0.001)
    assert list(tmp_path.iterdir()) == []


async def test_rejects_oversize_content_length(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4", "content-length": str(200 * 1024 * 1024)},
            content=b"small",
        )

    _client_with(monkeypatch, _handler)
    with pytest.raises(ExtractorError, match="too large"):
        await download_mod.download_media(_SIGNED_URL, tmp_path, kind="video", max_mb=1.0)
    assert list(tmp_path.iterdir()) == []


async def test_rejects_wrong_content_type(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")

    _client_with(monkeypatch, _handler)
    with pytest.raises(ExtractorError, match="content-type"):
        await download_mod.download_media(_SIGNED_URL, tmp_path, kind="video")
    assert list(tmp_path.iterdir()) == []


async def test_temp_cleanup_on_error_covers_all_paths(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every error path must leave zero files behind in dest_dir."""
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP))

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _client_with(monkeypatch, _boom)
    with pytest.raises(ExtractorError):
        await download_mod.download_media(_SIGNED_URL, tmp_path)
    leftovers = list(tmp_path.iterdir())
    assert leftovers == [], f"orphan temp files: {leftovers}"


async def test_error_never_echoes_signed_url(
    allowlisted: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _dns_single(_PUBLIC_IP_2))

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"oops")

    _client_with(monkeypatch, _handler)
    with pytest.raises(ExtractorError) as exc:
        await download_mod.download_media(_SIGNED_URL, tmp_path)
    message = str(exc.value)
    assert "super-secret-signed-value" not in message
    assert _SIGNED_URL not in message
    assert _HOST in message
