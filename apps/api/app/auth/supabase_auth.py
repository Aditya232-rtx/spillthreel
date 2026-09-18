"""Supabase JWT verification.

Verifies Supabase Auth session JWTs offline:
  * ES256 / RS256 (new asymmetric default): fetches signing key from the
    project's public JWKS endpoint, matches by `kid`, caches for ~10 min
    (matching Supabase's edge TTL).
  * HS256 (legacy): falls back to `SUPABASE_LEGACY_JWT_SECRET` if
    configured. Only present on older projects or during a rotation
    window; new projects can leave it unset.

Branch on the token's `alg` header — Supabase's docs recommend this exact
pattern for smooth cutover from legacy secrets to asymmetric keys.
"""

from __future__ import annotations

from typing import Any

import jwt
from jwt import PyJWKClient

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

# Module-level JWKS client — its internal cache handles the ~10 min TTL
# and refetches on unknown `kid` (transparent signing-key rotation).
# Lazy-init so importing this module doesn't hit the network at boot.
_jwks_client: PyJWKClient | None = None

# Audience claim Supabase sets on every session token.
_EXPECTED_AUDIENCE = "authenticated"


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        _jwks_client = PyJWKClient(
            settings.supabase_jwks_url,
            cache_keys=True,
            lifespan=600,  # 10 min — matches Supabase's edge cache TTL
        )
    return _jwks_client


class SupabaseAuthError(Exception):
    """Raised when a token fails to verify. Middleware maps this to 401."""


def verify_access_token(token: str) -> dict[str, Any]:
    """Verify a Supabase Auth access token → decoded claims dict.

    Pure offline verification after JWKS warmup — no per-request network
    hop. Any decoding / signature / expiry error raises SupabaseAuthError.
    """
    settings = get_settings()

    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as e:
        raise SupabaseAuthError(f"malformed token: {e}") from e

    alg = header.get("alg")
    if alg is None:
        raise SupabaseAuthError("missing alg in token header")

    key: str | Any
    algorithms: list[str]

    if alg in ("ES256", "RS256", "EdDSA"):
        try:
            signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        except Exception as e:  # noqa: BLE001 — PyJWKClient can raise many types
            raise SupabaseAuthError(f"jwks lookup failed: {e}") from e
        key = signing_key.key
        algorithms = [alg]
    elif alg == "HS256":
        if not settings.supabase_legacy_jwt_secret:
            raise SupabaseAuthError(
                "token is HS256 but SUPABASE_LEGACY_JWT_SECRET is not set"
            )
        key = settings.supabase_legacy_jwt_secret
        algorithms = ["HS256"]
    else:
        raise SupabaseAuthError(f"unsupported alg: {alg}")

    try:
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=_EXPECTED_AUDIENCE,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise SupabaseAuthError("token expired") from e
    except jwt.InvalidAudienceError as e:
        raise SupabaseAuthError("wrong audience") from e
    except jwt.InvalidTokenError as e:
        raise SupabaseAuthError(f"invalid token: {e}") from e
