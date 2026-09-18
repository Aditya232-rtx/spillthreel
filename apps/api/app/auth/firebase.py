"""Firebase Admin SDK bootstrap + ID-token verification.

TRD §6.2 + §15.2: every /v1/ request carries `Authorization: Bearer <id token>`
and this module is where that token is cryptographically verified against
Firebase's public JWKs (cached by the SDK).
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_app() -> firebase_admin.App:
    """Initialize Firebase Admin exactly once per process."""
    settings = get_settings()

    if settings.firebase_credentials_json:
        cred_dict = json.loads(settings.firebase_credentials_json)
        cred = credentials.Certificate(cred_dict)
    else:
        # Falls back to GOOGLE_APPLICATION_CREDENTIALS or Cloud Run's default SA
        cred = credentials.ApplicationDefault()

    app = firebase_admin.initialize_app(
        cred,
        options={"projectId": settings.firebase_project_id},
    )
    _logger.info("firebase_admin.initialized", project=settings.firebase_project_id)
    return app


class FirebaseAuthError(Exception):
    """Raised when a token fails to verify. Middleware maps this to 401."""


async def verify_id_token(token: str) -> dict[str, Any]:
    """Verify a Firebase ID token → decoded claims dict.

    The Firebase Admin SDK caches JWK certificates so verifies after
    warmup are ~1ms. Any decoding / signature / expiry error raises
    FirebaseAuthError so the middleware can produce a 401 response.
    """
    _get_app()  # ensure init
    try:
        return firebase_auth.verify_id_token(token, check_revoked=False)
    except firebase_auth.RevokedIdTokenError as e:
        raise FirebaseAuthError("token revoked") from e
    except firebase_auth.ExpiredIdTokenError as e:
        raise FirebaseAuthError("token expired") from e
    except firebase_auth.InvalidIdTokenError as e:
        raise FirebaseAuthError(f"invalid token: {e}") from e
    except Exception as e:
        raise FirebaseAuthError(f"unknown verify error: {e}") from e
