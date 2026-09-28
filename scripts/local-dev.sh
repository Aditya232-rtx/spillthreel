#!/usr/bin/env bash
# SpillTheReel local dev bootstrap (idempotent — safe to re-run).
#
# Usage:
#   ./scripts/local-dev.sh                      # uses apps/api/.env (prod values)
#   ./scripts/local-dev.sh --env apps/api/.env.staging   # staging values
#
# What it does:
#   1. Checks Docker is running.
#   2. Checks the env file exists (copies apps/api/.env.example -> .env
#      for the DEFAULT file only, then asks you to fill it).
#   3. Exports the env file and runs `docker compose up --build -d`.
#   4. Waits for GET /health on the API and prints the URLs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="apps/api/.env"
if [[ "${1:-}" == "--env" ]]; then
  if [[ $# -lt 2 ]]; then
    echo "usage: $0 [--env path/to/.env]" >&2
    exit 1
  fi
  ENV_FILE="$2"
fi

# 1. Docker must be up.
if ! docker info >/dev/null 2>&1; then
  echo "error: Docker is not running. Start Docker Desktop (or dockerd) and retry." >&2
  exit 1
fi

# 2. Env file must exist. Auto-create ONLY the default from the example.
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ "$ENV_FILE" == "apps/api/.env" && -f "apps/api/.env.example" ]]; then
    cp apps/api/.env.example apps/api/.env
    echo "created apps/api/.env from .env.example — fill in real values, then re-run."
    exit 1
  fi
  echo "error: env file '$ENV_FILE' not found. Create it (see apps/api/.env.example) and re-run." >&2
  exit 1
fi

# 3. Export env file, then (re)build + start. `up` is idempotent.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
echo "using env file: $ENV_FILE"
docker compose up --build -d

# 4. Wait for the API health probe, then print URLs.
echo -n "waiting for API http://localhost:8000/health "
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null http://localhost:8000/health; then
    echo "OK"
    echo ""
    echo "API:      http://localhost:8000  (docs: http://localhost:8000/docs)"
    echo "Health:   http://localhost:8000/health"
    echo "Cobalt:   http://localhost:9000"
    echo "Postgres: localhost:5432 (user spill / db spillthereel)"
    echo "Mobile:   cd apps/mobile && npx expo start"
    exit 0
  fi
  echo -n "."
  sleep 2
done
echo ""
echo "error: API did not become healthy in ~120s. Inspect with:" >&2
echo "  docker compose logs api" >&2
exit 1
