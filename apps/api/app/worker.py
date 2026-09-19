"""Worker ASGI app — the entrypoint the Cloud Run worker service uses.

In dev this is imported into `main.py` so `uvicorn app.main:app` serves
both the api and the worker on port 8000. In prod they run as separate
Cloud Run services with different container commands:

  api:    uvicorn app.main:app --port $PORT
  worker: uvicorn app.worker:app --port $PORT
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api import health
from app.observability.logging import configure_logging
from app.tasks import worker_entry


def create_worker_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="SpillTheReel Worker", version="0.1.0")
    app.include_router(health.router)
    app.include_router(worker_entry.router)
    return app


app = create_worker_app()
