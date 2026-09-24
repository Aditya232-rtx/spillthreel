"""Security tests for Supabase JWT verification (app/auth/supabase_auth.py).

All cases use HS256 with a throwaway test secret so no JWKS network hop
is needed — the asymmetric path shares the same decode/audience/expiry
checks downstream of key resolution, which is what these tests pin.

Settings are env-driven and cached: each test (re)sets env via
monkeypatch and clears the get_settings cache before AND after so no
test leaks config into another.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator

import jwt
import pytest

from app.auth.supabase_auth import SupabaseAuthError, verify_access_token
from app.settings import get_settings

_TEST_LEGACY_SECRET = "test-legacy-secret-for-pytest-only"


@pytest.fixture()
def legacy_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("SUPABASE_LEGACY_JWT_SECRET", _TEST_LEGACY_SECRET)
    get_settings.cache_clear()
    yield _TEST_LEGACY_SECRET
    get_settings.cache_clear()


@pytest.fixture()
def no_legacy_secret(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Blank, not deleted: Settings also reads the local .env FILE, so
    # delenv alone would still pick up a developer's real secret from disk.
    # Empty string is falsy, which is exactly the "not configured" branch.
    monkeypatch.setenv("SUPABASE_LEGACY_JWT_SECRET", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _hs256(payload: dict, secret: str = _TEST_LEGACY_SECRET) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


def _b64url(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


class TestVerifyAccessToken:
    def test_valid_hs256_returns_claims(self, legacy_secret: str) -> None:
        token = _hs256(
            {"sub": "user-123", "aud": "authenticated", "exp": int(time.time()) + 3600},
            legacy_secret,
        )
        claims = verify_access_token(token)
        assert claims["sub"] == "user-123"

    def test_expired_jwt_rejected(self, legacy_secret: str) -> None:
        token = _hs256(
            {"sub": "user-123", "aud": "authenticated", "exp": int(time.time()) - 10},
            legacy_secret,
        )
        with pytest.raises(SupabaseAuthError, match="expired"):
            verify_access_token(token)

    def test_malformed_jwt_rejected(self, legacy_secret: str) -> None:
        with pytest.raises(SupabaseAuthError):
            verify_access_token("definitely-not-a-jwt")

    def test_wrong_audience_rejected(self, legacy_secret: str) -> None:
        token = _hs256(
            {"sub": "user-123", "aud": "wrong-audience", "exp": int(time.time()) + 3600},
            legacy_secret,
        )
        with pytest.raises(SupabaseAuthError, match="audience"):
            verify_access_token(token)

    def test_hs256_without_secret_rejected(self, no_legacy_secret: None) -> None:
        # Signed with SOME secret, but the backend has none configured —
        # must refuse rather than verify against an empty key.
        token = _hs256(
            {"sub": "user-123", "aud": "authenticated", "exp": int(time.time()) + 3600},
            "some-other-secret",
        )
        with pytest.raises(SupabaseAuthError, match="HS256"):
            verify_access_token(token)

    def test_unsupported_alg_rejected(self, legacy_secret: str) -> None:
        header = _b64url({"alg": "none", "typ": "JWT"})
        payload = _b64url({"sub": "user-123"})
        with pytest.raises(SupabaseAuthError, match="unsupported alg"):
            verify_access_token(f"{header}.{payload}.")
