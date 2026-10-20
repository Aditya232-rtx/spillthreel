"""Unit tests for worker OIDC audience check (app/tasks/worker_entry.py).

Regression: Cloud Tasks mints audience `https://<worker>/work/<route>`
but behind Cloud Run the ASGI scope scheme is `http`, so a strict
`audience == str(request.url)` compare 401s with `bad_audience`.
The check must compare (host, path) ignoring scheme; wrong host or
wrong path must still fail closed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request

from app.settings import Settings
from app.tasks.worker_entry import _oidc_audience_ok

_HOST = "worker-abc123-uc.a.run.app"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "supabase_url": "https://test.supabase.co",
        "supabase_publishable_key": "sb_publishable_test",
        "supabase_secret_key": "sb_secret_test",
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/db",
        "environment": "staging",
    }
    base.update(overrides)
    return Settings(**base)


def _req(url: str, forwarded_proto: str | None = None) -> Request:
    parts = urlsplit(url)
    host = parts.hostname or _HOST
    raw: list[tuple[bytes, bytes]] = [(b"host", host.encode("latin-1"))]
    if forwarded_proto is not None:
        raw.append((b"x-forwarded-proto", forwarded_proto.encode("latin-1")))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "scheme": parts.scheme or "http",
        "server": (host, 443 if parts.scheme == "https" else 80),
        "path": parts.path or "/",
        "query_string": parts.query.encode("latin-1"),
        "headers": raw,
        "client": ("testclient", 50000),
    }
    return Request(scope)


class TestOidcAudienceOk:
    def test_exact_https_match(self) -> None:
        url = f"https://{_HOST}/work/ingest"
        assert _oidc_audience_ok(url, _req(url), _settings()) is True

    def test_http_vs_https_mismatch_passes(self) -> None:
        # The prod incident: token aud is https, request.url is http.
        audience = f"https://{_HOST}/work/ingest"
        assert _oidc_audience_ok(audience, _req(f"http://{_HOST}/work/ingest"), _settings()) is True

    def test_forwarded_proto_reconstruction_passes(self) -> None:
        audience = f"https://{_HOST}/work/ingest"
        req = _req(f"http://{_HOST}/work/ingest", forwarded_proto="https")
        assert _oidc_audience_ok(audience, req, _settings()) is True

    def test_wrong_host_fails(self) -> None:
        audience = f"https://{_HOST}/work/ingest"
        assert (
            _oidc_audience_ok(audience, _req("https://evil.run.app/work/ingest"), _settings())
            is False
        )

    def test_wrong_host_fails_despite_scheme_drift(self) -> None:
        audience = f"https://{_HOST}/work/ingest"
        assert (
            _oidc_audience_ok(audience, _req("http://evil.run.app/work/ingest"), _settings())
            is False
        )

    def test_wrong_path_fails(self) -> None:
        audience = f"https://{_HOST}/work/ingest"
        assert (
            _oidc_audience_ok(audience, _req(f"https://{_HOST}/work/import"), _settings()) is False
        )

    def test_path_prefix_trick_fails(self) -> None:
        # /work/ingest-evil must not match /work/ingest.
        audience = f"https://{_HOST}/work/ingest-evil"
        assert (
            _oidc_audience_ok(audience, _req(f"https://{_HOST}/work/ingest"), _settings()) is False
        )

    def test_trailing_slash_route_drift_fails_closed(self) -> None:
        audience = f"https://{_HOST}/work/ingest"
        assert (
            _oidc_audience_ok(audience, _req(f"https://{_HOST}/work/ingest/"), _settings()) is False
        )

    def test_malformed_audience_fails_closed(self) -> None:
        assert (
            _oidc_audience_ok("not-a-url", _req(f"https://{_HOST}/work/ingest"), _settings())
            is False
        )

    def test_base_url_prefix_still_accepted(self) -> None:
        settings = _settings(worker_base_url=f"https://{_HOST}")
        url = f"https://{_HOST}/work/ingest"
        assert _oidc_audience_ok(url, _req(url), settings) is True

    def test_base_url_scheme_drift_accepted(self) -> None:
        settings = _settings(worker_base_url=f"https://{_HOST}")
        audience = f"https://{_HOST}/work/ingest"
        assert _oidc_audience_ok(audience, _req(f"http://{_HOST}/work/ingest"), settings) is True
