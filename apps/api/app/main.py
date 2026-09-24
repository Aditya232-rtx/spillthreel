"""FastAPI ASGI app factory — the entrypoint uvicorn calls.

Assembles:
  * Structured logging + Sentry
  * Firebase Admin (via first token verify — lazy-init)
  * v1 routers (saves, items, categories, users)
  * Health probe

Run locally:
    uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded

from app.api import health
from app.api.v1 import categories, items, saves, users
from app.observability.logging import configure_logging, get_logger
from app.ratelimit import (
    DefaultRateLimitMiddleware,
    limiter,
    rate_limit_exceeded_handler,
)
from app.settings import get_settings
from app.tasks import worker_entry


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=0.1 if settings.environment == "prod" else 0.0,
            send_default_pii=False,
        )
    get_logger(__name__).info("api.startup", environment=settings.environment)
    yield
    get_logger(__name__).info("api.shutdown")


def create_app() -> FastAPI:
    app = FastAPI(
        title="SpillTheReel API",
        version="0.1.0",
        lifespan=_lifespan,
    )

    settings = get_settings()

    # CORS: permissive only in dev. Outside dev the allowed origins must
    # be explicit via CORS_ALLOWED_ORIGINS — fail fast if unset, so a
    # misconfigured staging/prod deploy never boots wide open.
    if settings.environment == "dev":
        allow_origins = ["*"]
    else:
        allow_origins = [
            origin.strip()
            for origin in settings.cors_allowed_origins.split(",")
            if origin.strip()
        ]
        if not allow_origins:
            raise RuntimeError(
                "CORS_ALLOWED_ORIGINS must be set when ENVIRONMENT is "
                f"{settings.environment!r}"
            )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting: default limit on every route via the middleware,
    # stricter per-route decorators where set (e.g. POST /v1/saves).
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(DefaultRateLimitMiddleware)

    app.include_router(health.router)
    app.include_router(saves.router, prefix="/v1", tags=["saves"])
    app.include_router(items.router, prefix="/v1", tags=["items"])
    app.include_router(categories.router, prefix="/v1", tags=["categories"])
    app.include_router(users.router, prefix="/v1", tags=["users"])

    # In dev, the worker routes share the api process so a single
    # docker-compose service exercises the full pipeline. In prod they
    # run as a separate Cloud Run service via app/worker.py.
    if settings.environment == "dev":
        app.include_router(worker_entry.router)

    return app


app = create_app()
