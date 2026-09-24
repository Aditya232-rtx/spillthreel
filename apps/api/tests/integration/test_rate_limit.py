"""Rate-limit tests (slowapi wiring in app/main.py + app/ratelimit.py).

Limits resolve from settings per-request, so tests tighten them with env
overrides + a settings cache clear — no app rebuild needed. Distinct
X-Forwarded-For values isolate storage buckets between tests.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi.errors import RateLimitExceeded

from app.ratelimit import (
    DefaultRateLimitMiddleware,
    limiter,
    rate_limit_exceeded_handler,
    saves_limit,
)
from app.settings import get_settings


@pytest.fixture()
def tight_default_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "3/minute")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_limit_returns_429_with_retry_after(
    tight_default_limit: None,
) -> None:
    from app.main import create_app

    client = TestClient(create_app())
    headers = {"X-Forwarded-For": "rate-test-default"}

    for _ in range(3):
        assert client.get("/health", headers=headers).status_code == 200

    response = client.get("/health", headers=headers)
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "60"


def test_per_route_callable_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercises the @limiter.limit(<callable>) path used by POST /v1/saves
    # without needing a DB or auth token.
    monkeypatch.setenv("RATE_LIMIT_SAVES", "2/minute")
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "1000/minute")
    get_settings.cache_clear()
    try:
        app = FastAPI()
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
        app.add_middleware(DefaultRateLimitMiddleware)

        @app.get("/dummy")
        @limiter.limit(saves_limit)
        async def dummy(request: Request) -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        headers = {"X-Forwarded-For": "rate-test-per-route"}
        assert client.get("/dummy", headers=headers).status_code == 200
        assert client.get("/dummy", headers=headers).status_code == 200

        response = client.get("/dummy", headers=headers)
        assert response.status_code == 429
        assert response.headers.get("Retry-After") == "60"
    finally:
        get_settings.cache_clear()
