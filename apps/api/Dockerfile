# SpillTheReel API — multi-stage: builder installs deps, runtime is slim.
# The served app module comes from APP_MODULE (default app.main:app; the
# worker service sets APP_MODULE=app.worker:app). The port comes from
# Cloud Run's PORT env (default 8000 for local docker). See
# apps/workers/Dockerfile for the worker image (same deps + entrypoint).

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# uv is the fast resolver; pip is fine here since the image only builds once.
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install --no-cache-dir ".[worker]"

# ------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_MODULE=app.main:app \
    PORT=8000

# Non-root: TRD §15.6 sandboxing.
RUN groupadd -r app && useradd -r -g app -u 10001 app

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY app ./app
COPY alembic.ini ./

USER app

EXPOSE 8000

# Shell form with exec: expands APP_MODULE/PORT at container start while
# keeping uvicorn as PID 1 for clean SIGTERM handling on Cloud Run.
CMD ["sh", "-c", "exec uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
