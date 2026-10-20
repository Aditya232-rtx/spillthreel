# HANDOFF — SpillTheReel session state (2026-09-30)

Read this file first. It is the full working state for continuing in a
fresh session with MCPs + subagents active. Repo root:
`/Users/adityajadhav/projects/spillthereelproduct`.

## 1. Goal for this session

Finish **Phase 0** (staging deploy on GCP, exit checks green) and then
**Phase 1** (search RAG loop + remaining endpoints; chat UI already
expects them). Backend-only except where noted; do not touch
`apps/mobile` unless the task says so.

## 2. Tooling available to you (verify on start)

- **Supabase MCP**: configured in `~/.config/opencode/opencode.jsonc`,
  OAuth-authenticated, `opencode mcp list` shows `supabase ✓ connected`.
  Use it for DB/Auth/storage/branches instead of hand-rolled curl.
- **gcloud MCP**: same config file, `command: ["npx", "-y",
  "@google-cloud/gcloud-mcp"]`. Runs as the local gcloud identity.
- **Skills** (auto-loaded from `~/.agents/skills/`): `supabase`,
  `supabase-postgres-best-practices` — load the second before ANY
  Postgres/schema/RLS work.
- **Subagents** (project-scoped, `.opencode/agents/`, need no extra
  wiring): `fastapi-developer`, `expo-react-native-expert`,
  `postgres-pro`, `terraform-engineer`, `test-automator`, `debugger`,
  `llm-architect`, `prompt-engineer`, `cloud-architect`,
  `code-reviewer` + `security-auditor` (read-only: edit/bash denied).

## 3. Git state (do NOT push without being asked)

- HEAD: `1afe070` — "fix(infra): mount Cobalt key file, split auth
  headers, set COBALT_BASE_URL". All local, **unpushed** (human pushes).
- Uncommitted changes (both intentional, commit when convenient):
  - `infra/modules/artifact_registry/outputs.tf` — module output was the
    `projects/...` resource name (not a valid image ref); now returns the
    `region-docker.pkg.dev/...` pull path. Required — without it every
    service image URL is malformed.
  - `.opencode/` — the 11 subagents above.
- Prior session uploaded a huge amount of work; full log: `git log
  --oneline` (pgvector swap `94fe4de`, Cobalt lockdown `0349f49`, etc.).

## 4. What is LIVE right now (verified, not assumed)

- **GCP project `spillthereel`** (asia-south1): Artifact Registry repo
  `spillthereel` exists; GCS state bucket
  `spillthereel-tfstate-spillthereel01` (versioned); Terraform backend
  initialized against it.
- **Images in AR**: `api:latest` ✓ pushed, `worker:latest` ✓ pushed.
  **Cobalt NOT mirrored** — 775MB image kept timing out on big-layer
  PUTs (see §7). Terraform references
  `.../spillthereel/cobalt:10`, which does not exist yet.
- **Terraform applied so far**: ONLY `module.images`. No services,
  queues, secrets, or WIF exist yet. No `tfplan` file on disk.
- **Supabase**: prod project + staging project
  (`txwueqvzftiftmmfyeqt`) both live; Google + Email providers on;
  **"Confirm email" is ON** (new signups get no session until the link
  is tapped); redirect URLs include `spillthereel://`, `exp://`, and
  localhost variants for both `/auth/callback` and `/auth/reset-password`.
- **Local processes verified just now**: API :8000 healthy (`/health`
  ok), Expo web :8081 serving (200). Expo Android/Go runner was up;
  re-check with `pgrep`. **Docker daemon is DOWN again** (socket gone)
  — restart Docker Desktop before any compose/build/push work, and
  expect the §7 flakiness.

## 5. Secrets & env (read, never print, never commit)

- `apps/api/.env` = prod values; `apps/api/.env.staging` = staging
  (has PROJECT_ID/STATE_BUCKET/GITHUB_REPO/CORS/non-secret URLs plus
  real secret values — gitignored, mode should be 600). Source per
  shell invocation: `set -a; . apps/api/.env.staging; set +a`.
- `infra/envs/staging/terraform.tfvars` exists (gitignored);
  `supabase_publishable_key` is intentionally NOT in it — export
  `TF_VAR_supabase_publishable_key` on every terraform call.
- Mobile `.env` files hold the Sentry DSNs + PostHog key (public client
  keys, still kept out of git).

## 6. Architecture you must not re-break

- Memory = **pgvector + Gemini embeddings** (`item_embeddings`,
  `vector(768)`, HNSW cosine). Cognee is fully removed (only historical
  mentions remain). `MemoryStore` interface in
  `apps/api/app/services/memory/base.py`.
- Auth = Supabase ES256/JWKS, `user_metadata.{full_name,custom_name,
  avatar}`. Display names follow last-explicit-choice-wins with a
  server-side `custom_name` anchor + device override + sign-in re-apply
  (see `useAuth`, `display-name.ts`). Duplicate-email signup signs the
  user in, it doesn't error.
- Cobalt auth is TWO headers: `Authorization: Api-Key` (Cobalt layer,
  from mounted `keys.json` via `API_KEY_URL=file:///...`) +
  `X-Serverless-Authorization: Bearer` (Cloud Run IAM, 50-min cached ID
  token, raises outside dev). Never merge them into one header.
- Dockerfiles serve `${APP_MODULE}` on `${PORT}` (exec-form keeps PID 1);
  worker env sets `APP_MODULE=app.worker:app`; `COBALT_BASE_URL` is set
  only on the worker (cycle avoidance).
- Python env rule learned the hard way: plain `uv sync` **drops**
  pytest/ruff/mypy (dev tools live in the `dev` optional extra) — always
  `uv sync --frozen --extra dev` (plus `--extra worker` when needed).

## 7. Known environment hazards

- **Big uploads to AR time out** (`net/http: timeout awaiting response
  headers`, only on large layers; 0.2s RTT otherwise). Resume-friendly
  (server keeps completed layers) — just re-run pushes. If Cobalt still
  won't finish from the laptop, mirror from Cloud Shell/GCE instead.
- **Docker Desktop is unstable** (corrupted VM disk once; force-restarted
  to recover). If builds fail with filesystem errors, restart it before
  debugging code.
- **Expo Go no longer loads the app** (`expo-dev-client` installed) —
  use `npx expo run:android` dev builds or web preview.
- Screenshots pasted in chat DO render. Past reference images sometimes
  arrived blank — if one does, ask for a repo file path instead.

## 8. Suggested order of work

1. Commit the two pending files (`outputs.tf` fix + `.opencode/`).
2. Resume/finish the Cobalt mirror push (or mirror from Cloud Shell).
3. B7: plan+apply `module.secrets`, add the 7 secret versions (generate
   the Cobalt UUID + `COBALT_KEYS_JSON` yourself, temp file in /tmp,
   delete after), verify all 7 have enabled versions.
4. B8: full `terraform plan -out=tfplan`, read it (expect api/worker/
   cobalt + queues + WIF, nothing destroyed), apply saved plan.
5. B9: smoke tests (API 200, Cobalt 403 anon / 200 with
   `X-Serverless-Authorization`, worker 403 anon).
6. B10: `alembic upgrade head` (env.py prefers direct URL), confirm head
   `20260930_0001_pgvector_memory` + `vector` extension + table.
7. B11: `gh` variables (`GCP_WIF_PROVIDER`, `GCP_TF_SERVICE_ACCOUNT`,
   `GCP_PROJECT_ID`, `EXPO_TOKEN` secret), `GCP_ENABLED=true` LAST.
8. Phase 1: `POST /v1/search` (retriever + answerer), `/v1/events` SSE,
   `/enhance`, `/retry`, `/notifications/token`, `gallerydl.py`,
   `ig_pipeline.py` + import endpoints. Chat UI already sends/renders
   against these; stub replies today.
