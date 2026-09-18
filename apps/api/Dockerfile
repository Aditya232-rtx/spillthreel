# SpillTheReel API — multi-stage: builder installs deps, runtime is slim.
# The worker image extends this with `--extra worker` deps (ffmpeg, yt-dlp,
# cognee, google-genai). See apps/workers/Dockerfile.

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

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
