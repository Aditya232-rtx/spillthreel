"""Per-user rate limiting via slowapi.

Wiring (see app/main.py):
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_middleware(DefaultRateLimitMiddleware)

`default_limits` apply to every route through DefaultRateLimitMiddleware,
including undecorated ones. Expensive endpoints add a stricter per-route
decorator, e.g. `@limiter.limit(saves_limit)` on POST /v1/saves.

Bucket key: the JWT `sub` claim when a bearer token is present, else the
client IP (preferring X-Forwarded-For, since prod sits behind a load
balancer). The sub is read WITHOUT verifying the signature
(`jwt.get_unverified_claims`) — it is only a bucket label, never an
auth decision. Auth is still enforced separately by
app/auth/middleware.py on every /v1/ route, so forging a bucket key
buys an attacker nothing but a different (still limited) bucket.
"""

from __future__ import annotations

import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from app.settings import get_settings


def rate_limit_key(request: Request) -> str:
    """Bucket key for slowapi: verified-later user id, else client IP."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            try:
                claims = jwt.get_unverified_claims(token)
            except Exception:  # any parse failure falls through to IP
                claims = {}
            sub = claims.get("sub") if isinstance(claims, dict) else None
            if sub:
                return f"user:{sub}"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


def default_limit() -> str:
    """Global default limit, resolved per-request from settings."""
    return get_settings().rate_limit_default


def saves_limit() -> str:
    """Stricter limit for POST /v1/saves (triggers paid LLM work)."""
    return get_settings().rate_limit_saves


limiter = Limiter(
    key_func=rate_limit_key,
    default_limits=[default_limit],
    # NOTE: response-header injection stays OFF. With it on, slowapi's
    # route decorator calls _inject_headers on the endpoint's return value,
    # and standard FastAPI handlers return dicts/models (not Response) —
    # which raises instead of injecting. Retry-After is set explicitly
    # in the handler below, so nothing is lost.
    headers_enabled=False,
)


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 with a guaranteed Retry-After header.

    Set explicitly rather than relying on slowapi's header injection
    (see note above). The per-minute windows used here always reset
    within 60 seconds.
    """
    return JSONResponse(
        {"detail": f"rate limit exceeded: {exc.detail}"},
        status_code=429,
        headers={"Retry-After": "60"},
    )


class DefaultRateLimitMiddleware(BaseHTTPMiddleware):
    """Enforce the global default limit on every request.

    slowapi's own SlowAPIMiddleware first resolves the route to an
    endpoint function and skips anything it can't resolve. With current
    Starlette, included routers appear as _IncludedRouter (FULL match,
    no .endpoint attribute), so the stock middleware silently exempted
    every router-mounted route. Calling _check_request_limit directly
    with no endpoint applies the default limits keyed by request path
    instead — no route resolution involved.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            limiter._check_request_limit(request, None, True)
        except RateLimitExceeded as exc:
            return await rate_limit_exceeded_handler(request, exc)
        return await call_next(request)
