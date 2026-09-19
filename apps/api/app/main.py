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

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.v1 import categories, items, saves, users
from app.observability.logging import configure_logging, get_logger
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

    # CORS is intentionally permissive during dev — Cloud Load Balancer
    # will front prod and only surface the mobile bundle's origin.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(saves.router, prefix="/v1", tags=["saves"])
    app.include_router(items.router, prefix="/v1", tags=["items"])
    app.include_router(categories.router, prefix="/v1", tags=["categories"])
    app.include_router(users.router, prefix="/v1", tags=["users"])

    # In dev, the worker routes share the api process so a single
    # docker-compose service exercises the full pipeline. In prod they
    # run as a separate Cloud Run service via app/worker.py.
    settings = get_settings()
    if settings.environment == "dev":
        app.include_router(worker_entry.router)

    return app


app = create_app()
