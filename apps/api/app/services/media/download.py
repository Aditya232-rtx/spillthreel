"""SSRF-hardened streaming media downloader.

Security model (read before touching this file):

* HTTPS-only, manual redirect walk (max 3). Every hop re-validates
  scheme, host suffix-allowlist, and DNS — redirects are the classic
  SSRF bypass, so there is no "already checked" shortcut.
* DNS is resolved in-process via ``socket.getaddrinfo`` and every
  returned address must be globally routable (loopback, private,
  link-local incl. 169.254.169.254 / fe80::/10, multicast, reserved,
  unspecified are all rejected for IPv4 and IPv6). A host with ANY bad
  address is rejected outright — mixed good/bad answers are a DNS
  round-robin trick.
* DNS-rebinding defense: after validation we pin resolution for that
  hop by temporarily constraining ``socket.getaddrinfo`` to the
  validated IP set (see ``_pinned_dns``). httpx keeps requesting the
  ORIGINAL hostname URL, so TLS SNI / cert verification / Host header
  are untouched — only the resolved socket address is forced. This
  collapses the validate→connect TOCTOU window to the pinned call.
  Residual risk: a privileged network attacker racing the TTL inside
  the single pinned request (documented in the Part C report).
* Errors name the HOST only — never the full (possibly signed) URL.
  Logs carry host + path length, never query strings or tokens.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from unittest import mock
from urllib.parse import urljoin, urlparse

import httpx

from app.observability.logging import get_logger
from app.services.extractors.base import ExtractorError
from app.settings import get_settings

_logger = get_logger(__name__)

_MAX_REDIRECTS = 3
_CHUNK_SIZE_BYTES = 65536
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# Fallback allowlist (integrator owns the real value in settings.py).
# Covers IG (fbcdn/cdninstagram), TikTok (tiktokcdn/byteoversea/mus*),
# YouTube thumbnails + streams (ytimg/googlevideo), and X (twimg).
_DEFAULT_ALLOWLIST = (
    "cdninstagram.com,fbcdn.net,"
    "tiktokcdn.com,tiktokcdn-us.com,byteoversea.com,muscdn.com,"
    "tiktokv.com,googlevideo.com,ytimg.com,youtube.com,youtu.be,"
    "ggpht.com,twimg.com"
)
_DEFAULT_MAX_MB: dict[str, float] = {"video": 100.0, "image": 20.0, "audio": 20.0}
_DEFAULT_CONNECT_TIMEOUT_S = 10.0

_KIND_PREFIX: dict[str, str] = {"video": "video/", "image": "image/", "audio": "audio/"}
_KIND_FALLBACK_EXT: dict[str, str] = {"video": ".mp4", "image": ".jpg", "audio": ".mp3"}
_MIME_EXT: dict[str, str] = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
}


def _parse_allowlist(raw: str) -> list[str]:
    parts: list[str] = []
    for chunk in raw.split(","):
        item = chunk.strip().lower().lstrip(".")
        if item:
            parts.append(item)
    return parts


def _get_allowlist() -> list[str]:
    raw: str | None = None
    try:
        settings = get_settings()
        value = getattr(settings, "media_host_allowlist", None)
        if isinstance(value, str) and value.strip():
            raw = value
    except Exception:
        raw = None
    if raw is None:
        env_raw = os.environ.get("MEDIA_HOST_ALLOWLIST", "")
        raw = env_raw if env_raw.strip() else _DEFAULT_ALLOWLIST
    return _parse_allowlist(raw)


def _get_max_bytes(kind: str, override_mb: float | None) -> int:
    if override_mb is not None:
        if override_mb <= 0:
            raise ExtractorError("invalid max_mb: must be positive")
        return int(override_mb * 1024 * 1024)
    try:
        settings = get_settings()
        attr = {
            "video": "media_max_video_mb",
            "image": "media_max_image_mb",
            "audio": "media_max_audio_mb",
        }[kind]
        value = getattr(settings, attr, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(float(value) * 1024 * 1024)
    except ExtractorError:
        raise
    except Exception:
        pass
    env_name = {
        "video": "MEDIA_MAX_VIDEO_MB",
        "image": "MEDIA_MAX_IMAGE_MB",
        "audio": "MEDIA_MAX_AUDIO_MB",
    }[kind]
    env_raw = os.environ.get(env_name)
    if env_raw:
        try:
            parsed = float(env_raw)
            if parsed > 0:
                return int(parsed * 1024 * 1024)
        except ValueError:
            pass
    return int(_DEFAULT_MAX_MB[kind] * 1024 * 1024)


def _get_connect_timeout_s() -> float:
    try:
        settings = get_settings()
        value = getattr(settings, "media_connect_timeout_s", None)
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
    except Exception:
        pass
    env_raw = os.environ.get("MEDIA_CONNECT_TIMEOUT_S")
    if env_raw:
        try:
            parsed = float(env_raw)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return _DEFAULT_CONNECT_TIMEOUT_S


def _build_timeout(timeout_s: float) -> httpx.Timeout:
    connect_s = _get_connect_timeout_s()
    return httpx.Timeout(connect=connect_s, read=timeout_s, write=timeout_s, pool=timeout_s)


def _build_client(timeout: httpx.Timeout) -> httpx.AsyncClient:
    """Client factory (separate for tests to inject MockTransport)."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def _check_allowlist(host: str, allowlist: list[str]) -> None:
    for suffix in allowlist:
        if host == suffix or host.endswith("." + suffix):
            return
    raise ExtractorError(f"host not allowlisted: {host}")


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return True
    # Backstop for CGNAT / documentation / benchmark ranges that some
    # Python versions do not flag above: only globally routable passes.
    return not addr.is_global


def _resolve_ips(host: str, port: int) -> list[str]:
    """Resolve host and reject non-global addresses. Returns validated IP strings."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise ExtractorError(f"DNS resolution failed for host {host}") from e
    if not infos:
        raise ExtractorError(f"DNS resolution failed for host {host}")
    ips: list[str] = []
    for _family, _socktype, _proto, _canon, sockaddr in infos:
        ip_str = str(sockaddr[0])
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError as e:
            raise ExtractorError(f"blocked IP for host {host}") from e
        if _is_blocked_ip(addr):
            raise ExtractorError(f"blocked IP for host {host}")
        ips.append(ip_str)
    if not ips:
        raise ExtractorError(f"DNS resolution failed for host {host}")
    return ips


@contextmanager
def _pinned_dns(host: str, ips: list[str], port: int) -> Iterator[None]:
    """Pin DNS for one hop to the validated IP set.

    Mechanism: temporarily replace ``socket.getaddrinfo`` so queries for
    ``host`` return ONLY the pre-validated addresses. httpx/anyio still
    dial by hostname, so SNI, certificate verification against the real
    hostname, and the Host header are unchanged — the socket simply
    cannot go anywhere except a validated IP. Queries for any other
    hostname fall through to the real resolver.
    """
    original = socket.getaddrinfo
    pinned = host.lower().rstrip(".")

    def _fake(*args: Any, **kwargs: Any) -> Any:
        query_host = args[0] if args else kwargs.get("host")
        if query_host is None or str(query_host).lower().rstrip(".") != pinned:
            return original(*args, **kwargs)
        try:
            requested_family: int = int(args[2]) if len(args) > 2 else int(kwargs.get("family", 0))
        except (ValueError, TypeError):
            requested_family = 0
        try:
            requested_port = int(args[1]) if len(args) > 1 and args[1] is not None else port
        except (ValueError, TypeError):
            requested_port = port
        results: list[Any] = []
        for ip_str in ips:
            addr = ipaddress.ip_address(ip_str)
            if isinstance(addr, ipaddress.IPv4Address):
                if requested_family not in (0, int(socket.AF_INET)):
                    continue
                results.append(
                    (
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (ip_str, requested_port),
                    )
                )
            else:
                if requested_family not in (0, int(socket.AF_INET6)):
                    continue
                results.append(
                    (
                        socket.AF_INET6,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        (ip_str, requested_port, 0, 0),
                    )
                )
        return results

    with mock.patch.object(socket, "getaddrinfo", _fake):
        yield


def _extension_for(mime: str, kind: str) -> str:
    ext = _MIME_EXT.get(mime)
    if ext is not None:
        return ext
    return _KIND_FALLBACK_EXT[kind]


async def download_media(
    url: str,
    dest_dir: Path | str,
    *,
    kind: Literal["video", "image", "audio"] = "video",
    max_mb: float | None = None,
    timeout_s: float = 60.0,
) -> Path:
    """Download media to ``dest_dir`` with SSRF + size + type hardening.

    Raises ExtractorError naming only the host (never the full URL).
    Returns the Path of the downloaded file inside ``dest_dir``.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    max_bytes = _get_max_bytes(kind, max_mb)
    timeout = _build_timeout(timeout_s)
    allowlist = _get_allowlist()
    expected_prefix = _KIND_PREFIX[kind]

    fd, tmp_name = tempfile.mkstemp(prefix=".dl-", suffix=".tmp", dir=str(dest))
    os.close(fd)
    tmp_path = Path(tmp_name)
    completed = False
    try:
        current = url
        async with _build_client(timeout) as client:
            for hop in range(_MAX_REDIRECTS + 1):
                parsed = urlparse(current)
                host = (parsed.hostname or "").lower().rstrip(".")
                if parsed.scheme.lower() != "https":
                    raise ExtractorError(
                        f"refusing non-https URL for host {host or 'unknown host'}"
                    )
                if not host:
                    raise ExtractorError("URL has no host")
                try:
                    req_port: int = int(parsed.port) if parsed.port is not None else 443
                except ValueError as e:
                    raise ExtractorError(f"invalid port for host {host}") from e
                _check_allowlist(host, allowlist)
                ips = _resolve_ips(host, req_port)
                path_len = len(parsed.path or "")
                _logger.info(
                    "media.download.request", host=host, path_len=path_len, kind=kind, hop=hop
                )
                try:
                    with _pinned_dns(host, ips, req_port):
                        async with client.stream(
                            "GET",
                            current,
                            headers={
                                "User-Agent": "SpillTheReel-media-downloader/1.0",
                                "Accept": "*/*",
                            },
                        ) as resp:
                            status = resp.status_code
                            if status in _REDIRECT_STATUSES:
                                if hop >= _MAX_REDIRECTS:
                                    raise ExtractorError(f"too many redirects for host {host}")
                                location = resp.headers.get("location")
                                if not location:
                                    raise ExtractorError(
                                        f"redirect without location for host {host}"
                                    )
                                _logger.info(
                                    "media.download.redirect",
                                    host=host,
                                    status=status,
                                    hop=hop,
                                )
                                current = urljoin(current, location)
                                continue
                            if status < 200 or status >= 300:
                                raise ExtractorError(f"unexpected status {status} for host {host}")
                            raw_ct = resp.headers.get("content-type", "")
                            mime = raw_ct.split(";")[0].strip().lower()
                            if not mime.startswith(expected_prefix):
                                raise ExtractorError(
                                    f"unexpected content-type {mime or 'missing'} for host {host}"
                                )
                            declared = resp.headers.get("content-length")
                            if declared is not None:
                                try:
                                    declared_n = int(declared)
                                except ValueError:
                                    declared_n = -1
                                if declared_n > max_bytes:
                                    raise ExtractorError(f"content too large for host {host}")
                            total = 0
                            with tmp_path.open("wb") as fp:
                                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE_BYTES):
                                    if not chunk:
                                        continue
                                    total += len(chunk)
                                    if total > max_bytes:
                                        raise ExtractorError(f"content too large for host {host}")
                                    fp.write(chunk)
                            if total == 0:
                                raise ExtractorError(f"empty body for host {host}")
                            final = dest / f"{uuid.uuid4().hex}{_extension_for(mime, kind)}"
                            tmp_path.rename(final)
                            completed = True
                            _logger.info(
                                "media.download.complete",
                                host=host,
                                bytes=total,
                                kind=kind,
                            )
                            return final
                except ExtractorError:
                    raise
                except httpx.HTTPError as e:
                    raise ExtractorError(
                        f"download failed for host {host}: {type(e).__name__}"
                    ) from e
        raise ExtractorError("too many redirects")
    finally:
        if not completed:
            tmp_path.unlink(missing_ok=True)
