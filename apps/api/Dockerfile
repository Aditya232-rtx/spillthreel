# SpillTheReel API — multi-stage: builder resolves deps strictly from
# uv.lock, runtime is slim and carries only the resolved .venv + source.
# The served app module comes from APP_MODULE (default app.main:app; the
# worker service sets APP_MODULE=app.worker:app). The port comes from
# Cloud Run's PORT env (default 8000 for local docker). Build context is
# apps/api (see cloudbuild.staging.yaml). See apps/workers/Dockerfile
# for the worker image (same lockfile + `worker` extra, repo-root context).

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Pinned uv release (never :latest — reproducible builds).
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

# Deps first for layer caching. --frozen fails rather than re-resolving,
# so the image always matches uv.lock. --no-install-project: app source
# is copied in the runtime stage, and uv would otherwise fail resolving
# the project without it present.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev --extra worker

# ------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_MODULE=app.main:app \
    PORT=8000

# ffmpeg/ffprobe: dev api runs the ingest pipeline in-process via the
# local dispatcher (enqueue shortcut when WORKER_BASE_URL is unset —
# compose api has none), which shells out via services/media/ffmpeg.py.
# Staging/prod api never touches it (worker service does), but the dev
# loop needs it. System layer before app layers for cache-friendliness.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Non-root: TRD §15.6 sandboxing.
RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

COPY --chown=app:app app ./app
COPY --chown=app:app alembic.ini ./

USER app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Shell form with exec: expands APP_MODULE/PORT at container start while
# keeping uvicorn as PID 1 for clean SIGTERM handling on Cloud Run.
CMD ["sh", "-c", "exec uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
