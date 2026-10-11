# Worker Dockerfile — builds from the REPO ROOT (context must be `.`,
# built with `-f apps/workers/Dockerfile`) for correct COPY paths.
# Same lockfile as the API image plus the `worker` extra; serves
# app.worker:app via the shared APP_MODULE convention (Terraform sets it
# explicitly anyway).

ARG BUILD_IMAGE=python:3.12-slim

# ---- Builder ----
FROM ${BUILD_IMAGE} AS builder

WORKDIR /app

# Pinned uv release (never :latest — reproducible builds).
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv

# Copy dependency files first for better cache. --frozen fails rather
# than re-resolving, so the image always matches uv.lock.
COPY apps/api/pyproject.toml apps/api/uv.lock ./

# Install worker dependencies (includes API deps + worker extras).
# --no-install-project: app sources are copied separately below, and uv
# would otherwise fail resolving the project without them present.
RUN uv sync --frozen --no-install-project --no-dev --extra worker

# ---- Runtime ----
FROM ${BUILD_IMAGE}

WORKDIR /app

# ffmpeg/ffprobe: the ingest pipeline shells out to both
# (services/media/ffmpeg.py: probe, sample_frames, extract_audio,
# make_thumbnail). System layer before app layers for cache-friendliness.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code from repo root
COPY apps/api/app ./app

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser \
    && chown -R appuser:appuser /app

USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    APP_MODULE=app.worker:app \
    PORT=8080

# Same uvicorn serving pattern as the API image; APP_MODULE selects the
# worker ASGI app (Cloud Tasks HTTP target). `python -m
# app.tasks.worker_entry` would exit immediately — that module only
# defines route handlers, no server.
CMD ["sh", "-c", "exec uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
