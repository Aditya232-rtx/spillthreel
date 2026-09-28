# SpillTheReel

**Turn doomscrolling into instant recall.** A mobile "second brain" for saved
short-form content: share any Reel / Short / TikTok / X post into the app and
find it later in natural language ("the ramen recipe with miso"). Bulk-import
your entire Instagram save history from Meta's data export in one step.

## Monorepo map

```
spillthereel/
  apps/mobile/        Expo SDK 57 + React Native app (Android first; no iOS yet)
  apps/api/           FastAPI backend (uv, ruff, mypy, pytest)
  infra/              Terraform — staging only, NEVER applied yet
  docs/               PRD, TRD, architecture, build phases, oauth-setup
  scripts/            local-dev.sh — one-command local stack
  docker-compose.yml  Local Postgres + API + Cobalt
  .github/workflows/  api-ci, mobile-ci, terraform-plan, eas-preview (manual)
```

Product and technical contracts live in [`docs/`](docs/): `prd.md`, `trd.md`,
`architecture.md`, `buildphase.md`, `design-system.md`, plus
[`docs/oauth-setup.md`](docs/oauth-setup.md) for the manual Auth steps.

## Prerequisites

- Docker (Desktop or daemon running)
- Node 22 + npm
- `uv` (Python package manager)
- Android Studio (emulator) — iOS is out of scope until an Apple Developer
  account exists

## Quickstart (local dev)

```bash
./scripts/local-dev.sh                        # prod values from apps/api/.env
./scripts/local-dev.sh --env apps/api/.env.staging   # staging values
```

The script checks Docker, bootstraps the env file, rebuilds + starts the
stack, waits for the API health probe, and prints URLs:

| Service | URL |
|---|---|
| API | http://localhost:8000 (docs: `/docs`, health: `/health`) |
| Cobalt | http://localhost:9000 |
| Postgres | localhost:5432 (user `spill`, db `spillthereel`) |

Mobile (separate terminal):

```bash
cd apps/mobile
cp .env.example .env        # first time only, then fill values
npx expo start --android
```

> **Expo Go no longer loads this app.** `expo-dev-client` is installed, so
> Android runs as a dev build: use `npx expo run:android` (or an EAS
> development build). Web (`npx expo start --web`) is unaffected.

## Environment variables

**Backend** — `apps/api/.env` is prod, `apps/api/.env.staging` is staging
(both gitignored, never commit). Full commented list in
[`apps/api/.env.example`](apps/api/.env.example). Key rules:

- Missing required vars fail the boot fast — check the error, don't guess.
- `CORS_ALLOWED_ORIGINS` is required outside `dev` (empty string refuses boot).
- `SUPABASE_SECRET_KEY`, DB passwords, LLM keys: backend only, never clients.
- Local `docker compose` overrides `DATABASE_URL` to the container Postgres;
  Auth/Storage always point at Supabase Cloud (no local emulator exists).

**Mobile** — `apps/mobile/.env` is prod, `.env.staging` is staging
(gitignored). Template: [`apps/mobile/.env.example`](apps/mobile/.env.example).

| Variable | Required | Notes |
|---|---|---|
| `EXPO_PUBLIC_SUPABASE_URL` | yes | App throws at boot if missing |
| `EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | yes | Public by design; RLS enforces |
| `EXPO_PUBLIC_API_URL` | no | Defaults: localhost:8000 (dev), Cloud Run URL (release) |
| `EXPO_PUBLIC_SENTRY_DSN` | no | Unset = Sentry silently disabled |
| `EXPO_PUBLIC_SENTRY_TRACES_SAMPLE_RATE` | no | Default `0.0` |
| `EXPO_PUBLIC_POSTHOG_KEY` / `_HOST` | no | Unset = analytics silently disabled |

To run the app against staging locally: `cp apps/mobile/.env.staging apps/mobile/.env`.

> Never put the Supabase **secret** key or any server key in `EXPO_PUBLIC_*`
> — those values are embedded in the app bundle.

## Tests & linters

```bash
cd apps/api && uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy app/ && uv run pytest

cd apps/mobile && npm ci && npx tsc --noEmit
```

## CI (`.github/workflows/`)

| Workflow | Trigger | What |
|---|---|---|
| `api-ci` | `apps/api/**` | uv sync, ruff check + format, mypy, pytest |
| `mobile-ci` | `apps/mobile/**` | npm ci, tsc, expo-doctor (non-blocking) |
| `terraform-plan` | `infra/**` | fmt + init/validate always; real plan only if repo variable `GCP_ENABLED == 'true'` (WIF, no keys) |
| `eas-preview` | manual dispatch only | Android APK on the preview profile (free-plan quota) |

Required repo configuration (Settings → Secrets and variables → Actions):

- Secret `EXPO_TOKEN` — Expo access token for EAS builds.
- Variable `GCP_ENABLED` — leave unset/`false` until GCP is provisioned.
- Variables `GCP_WIF_PROVIDER`, `GCP_TF_SERVICE_ACCOUNT` — needed only when
  `GCP_ENABLED` becomes `true` (outputs of the Terraform WIF module).

All jobs use least-privilege `permissions`, path filters, `concurrency` groups
that cancel superseded runs, cached deps, and actions pinned to major versions.

## EAS builds (Android)

First-time setup (human, once) — do **not** hand-write these values:

```bash
cd apps/mobile
eas init        # writes extra.eas.projectId + owner into app.json
```

Then create per-environment secrets (values never enter the repo):

```bash
# development
eas env:create --environment development --name EXPO_PUBLIC_SUPABASE_URL --value "https://txwueqvzftiftmmfyeqt.supabase.co" --type public
eas env:create --environment development --name EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY --value "sb_publishable_..." --type public
eas env:create --environment development --name EXPO_PUBLIC_API_URL --value "http://10.0.2.2:8000" --type public
# preview / production: same three names, per-environment values,
# plus EXPO_PUBLIC_SENTRY_DSN / EXPO_PUBLIC_POSTHOG_KEY / _HOST as needed
```

Build: `eas build --platform android --profile preview` (or `development` /
`production`). Profiles live in [`apps/mobile/eas.json`](apps/mobile/eas.json):
APK for development/preview, AAB + autoIncrement for production. EAS free-plan
build minutes are scarce — preview builds run via manual dispatch only, and
`SENTRY_AUTH_TOKEN` in the build env is what turns on Sentry source-map upload
(otherwise skipped silently).

## Terraform (`infra/`)

Layout: `modules/` (`artifact_registry`, `cloud_run_service`, `secret_manager`,
`cloud_tasks`, `workload_identity_github`) + `envs/staging/`
(`main.tf`, `variables.tf`, `outputs.tf`, `backend.tf`,
`terraform.tfvars.example`). Dropped after the Supabase migration: Cloud SQL,
Cloud Storage buckets, VPC connector, Cloud CDN (all Supabase-managed now).
Cobalt runs on Cloud Run (scale-to-zero) instead of GKE Autopilot for staging —
see the GKE-switch note in `envs/staging/main.tf`.

Validate (no credentials needed, nothing applied):

```bash
terraform fmt -check -recursive infra/
terraform -chdir=infra/envs/staging init -backend=false
terraform -chdir=infra/envs/staging validate
```

**Nothing here has ever been applied.** First apply is a human job:
1. Create the GCP project + billing, and a GCS bucket for state.
2. `terraform init -backend-config="bucket=<state-bucket>"` in
   `infra/envs/staging`, copy `terraform.tfvars.example` → `terraform.tfvars`,
   fill real values.
3. `terraform apply` (creates WIF pool first — the chicken-and-egg module).
4. Add each secret VALUE by hand: `gcloud secrets versions add <ID> --data-file=-`.
5. Set repo variables `GCP_ENABLED=true`, `GCP_WIF_PROVIDER`,
   `GCP_TF_SERVICE_ACCOUNT` from the apply outputs.

## Observability

- **Sentry**: backend inits in `app/main.py` (PII off, header/body scrubbers,
  release from `SENTRY_RELEASE`/`GIT_SHA`, no-op without DSN). Mobile inits in
  `app/_layout.tsx` (PII off, URL/token scrubbers, no-op without DSN).
- **PostHog**: mobile only, `PostHogProvider` with autocapture + replay off;
  typed `track()` in `src/lib/analytics.ts` (event schema TBD), identify by
  Supabase id, reset on sign-out. No emails, URLs, or tokens in props.
