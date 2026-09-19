# SpillTheReel — Build Updates & Punch List

**Last updated:** 2026-09-19
**Repo:** https://github.com/Aditya232-rtx/spillthreel
**Latest commit:** _(pending)_ — feat(backend): Phase 1 ingest pipeline scaffolding + Storage buckets

Living document. Keep this in the repo root, update on every session close.
Structure per section: **✅ Done · 🟡 Needs improvement · 🔴 Missing · ⏳ Pending**.

Companion docs (contract-level, don't drift): [prd.md](./prd.md) · [trd.md](./trd.md) · [architecture.md](./architecture.md) · [buildphase.md](./buildphase.md) · [design-system.md](./design-system.md) · [claude-design-handoff.md](./claude-design-handoff.md)

---

## 1. Product Docs

- ✅ **PRD** written (v1.1) — 8 features (F1–F8), personas, tiered IG import strategy, deferred monetization, success metrics, risks.
- ✅ **TRD** written (v1.0) with post-hoc Supabase rewrite — full stack, `Extractor`/`MemoryStore`/`SummaryModel`/`TranscriptionModel` protocols, Postgres schema with RLS, IG parser algorithm, cost model (~$3/user/mo).
- ✅ **Architecture.md** written — system diagram, monorepo tree, 5 data-flow walkthroughs, deployment topology.
- ✅ **BuildPhase.md** written — 8 phases (0–7) with deliverables, exit criteria, timeline.
- ✅ **Design System** (design-system.md) + **Design Handoff** (claude-design-handoff.md).
- ✅ **Supabase migration** documented across all four docs (Firebase Auth + Cloud SQL + GCS → Supabase Auth + Postgres + Storage).
- ✅ **Supabase new-format API keys + asymmetric ES256/JWKS JWT** reflected everywhere.
- 🟡 The `docs/design/` diagrams promised in `architecture.md` §16 (mermaid state diagrams, ER diagram, sequence diagrams) — not yet produced; will come during Phase 1 design.

---

## 2. Mobile App (`apps/mobile/`)

### ✅ Done

- Expo SDK 57 scaffold, TypeScript strict, `expo-router` file-based navigation, path aliases (`@/*` → `src/*`).
- **Design tokens** (`src/theme/tokens.ts`) — full palette, spacing, radii, hard-shadow rule.
- **Real Oswald** static weights (Bold, SemiBold, Medium, Regular) + MyLove script for the wordmark.
- **Screens built** (in `app/`):
  - `(auth)/welcome`, `(auth)/login`, `(auth)/signup`
  - `(import)/import1`, `(import)/import2`, `(import)/import3` (with `expo-document-picker` upload)
  - `(app)/home` (stat cards, chart, thumbs, collection rails)
  - `(app)/categories` (with the infinite rotating dial — see below)
  - `(app)/chat` (dark theme, message bubbles, rich cards, suggestion chips, input pill)
  - `(app)/profile` (avatar with hard shadow, stats row, connected sources)
  - `(app)/settings` (grouped rows, toggles, delete-account modal)
- **Primitives**: `PillButton` (with press-into-shadow animation), `Shadowed` (cross-platform hard-offset shadow via stacked backplate — not native shadow props), `Badge`, `TextField`, `TabBar` (floating glass pill), `SettingsRowView`, `ProgressDots`, `AmbientDot`, `LogoBurst`.
- **Categories dial** — full port of the reference's virtual-circle physics: 30 fixed slots, `translateY(radius·sin θ) rotate(θ) scale()` per card, `transform-origin: left`, hard 5px shadow via stacked backplate, cream fade masks matching page bg. Drag + `withDecay` momentum. Tap-vs-drag split via `activeOffsetY([-8, 8])`.
- **Add-category flow** — floating "+" button on Categories header (coral shadow), `AddCategoryModal` with name field + 16-emoji picker + palette rotation, `useCategories()` hook backed by AsyncStorage (v1 shim; swap-in ready for `POST /v1/categories`).
- **Categories store** persists to AsyncStorage; new categories show in dial with emoji, correct color from palette rotation, updated header count.

### 🟡 Needs improvement

- **Welcome-screen animation** — the dot-burst → logo crossfade has an unresolved react-native-web Image stacking-context issue; currently hard-swaps rather than crossfades. Works fine on native devices per the RN docs; only web-preview shows the bug.
- **Card shadow visibility** — on some cards the 5px offset is subtle. Could switch to native `boxShadow` (RN 0.76+) when we've dropped web preview from the dev loop.
- **Font weight variation** — currently loading four static weights. Not exercising all four consistently in screens; audit needed.

### 🔴 Missing

- **Item detail sheet** (PRD F4.5) — not built.
- **Search screen** (PRD F3.3, distinct from the Chat screen) — not built. Filter chips, similar-to-this, query history.
- **Library browse tab** — currently the Home screen doubles as this. Needs full virtualized list per PRD F4.1.
- **Search-permission onboarding walkthrough** (PRD F5.5) — not built.
- **Notification opt-in flow** (PRD F5.8) — not built.
- **Empty states** for library + categories + chat.
- **Item state badge** component (queued / analyzing / fully_indexed / failed).
- **Enhance CTA** on text-indexed items (PRD F4.5, F8.12).
- **Saved Audio tab** (PRD F8.7).
- **Data export UI** (PRD F7.3).
- **Localization scaffolding** — `en` only, but no i18n structure to add languages later.

### ⏳ Pending (blocked on backend or later phases)

- **Supabase JS client integration** on the mobile side — wire `@supabase/supabase-js` for OAuth flows, session storage in `expo-secure-store`.
- **Real API layer** (`src/lib/api.ts`) — TanStack Query + fetch wrapper that attaches the Supabase JWT to every request.
- **Share-sheet / share-intent integration** (PRD F1) — configs in place, native code not yet wired.
- **Expo push notifications** (PRD F6) — deferred to Phase 3.
- **PostHog SDK bootstrap** + event schema.
- **Sentry SDK integration**.

---

## 3. Backend API (`apps/api/`)

### ✅ Done

- **FastAPI scaffold** — `main.py` app factory, structured logging via structlog, Sentry init hook, CORS.
- **Config layer** (`app/settings.py`) — Pydantic Settings with 30+ env vars declared, derived `supabase_jwks_url` property.
- **SQLAlchemy async models** (`app/db/models.py`) — Profile + Item + Category + ItemCategory + Collection + CollectionItem + Import + SavedAudio + IngestionEvent + PushToken.
- **Alembic setup** — env.py, script template, initial migration 0001_initial (mirrors the pure-SQL script).
- **`app/db/engine.py`** — async engine, session factory, `session_scope()` for worker use, `statement_cache_size=0` for asyncpg + Supabase pgbouncer compat.
- **Supabase JWT verifier** (`app/auth/supabase_auth.py`) — verify_access_token() branches on token `alg` header; ES256/RS256/EdDSA via `PyJWKClient` (cached 10 min, auto-refetch on unknown kid); HS256 fallback to `SUPABASE_LEGACY_JWT_SECRET`.
- **Auth middleware** (`app/auth/middleware.py`) — `CurrentUser` dependency, upserts `profiles` row on first login, sets `request.jwt.claim.sub` on the transaction for RLS.
- **Real `/v1/saves` endpoint** — URL normalization, platform detection (regex, 4 platforms), INSERT ON CONFLICT dedup, Cloud Tasks enqueue.
- **Stub `/v1/items` (list + detail + delete)** — with cursor pagination.
- **Stub `/v1/categories` (list + create + delete)** — palette rotation, enqueues category_backfill task on create.
- **Stub `/v1/me` (get + async delete)**.
- **Stub `/v1/health`** — returns `{status: ok, version: 0.1.0}`.
- **Cloud Tasks enqueue helper** (`app/tasks/enqueue.py`) — with in-process dispatcher for dev.
- **Protocol interfaces**: `Extractor`, `MemoryStore`, `SummaryModel`, `TranscriptionModel` — empty ABCs, ready for Phase 1 implementations.
- **Dockerfile** (multi-stage, non-root, distroless-adjacent).
- **docker-compose.yml** — postgres + api + cobalt for local dev.
- **`.env.example`** with inline docs for every var + SECRET vs public labels.
- **`.env`** filled locally with real Supabase creds (gitignored).
- **`pyproject.toml`** — full dep manifest with `worker` and `dev` extras.

### 🟡 Needs improvement

- **Test coverage: ~10%** — 16 unit tests covering platform_detect + extractor registry fallback semantics. Ingestion pipeline itself untested (needs mock fixtures for the four external services).
- **OpenTelemetry tracing** — TRD §17.2 promises spans on every service call; not wired.
- **Custom Cloud Monitoring metrics** — TRD §17.3 promises 8 histograms/gauges; not wired.
- **Rate limiting** — TRD §14.1 promises 100 req/min per user; not wired.
- **CORS** currently `allow_origins=["*"]` — needs env-scoped list before staging.
- The Alembic migration (`20260918_0001_initial.py`) and the pure-SQL script (`apply_initial_schema.sql`) are duplicated by hand — need a linter that alerts on drift.
- **Health endpoint** returns hardcoded version; should read from a build-time env var.

### ✅ Done (Phase 1 core scaffolding — 2026-09-19)

- **`services/extractors/cobalt.py`** — httpx client, streams from Cobalt tunnel URL to tmpfs.
- **`services/extractors/ytdlp.py`** — lazy-imported yt-dlp, offloads to thread, captures uploader metadata.
- **`services/extractors/registry.py`** — priority-ordered per-platform fallback (Cobalt → yt-dlp), distinguishes ExtractorError (walkable) from other exceptions (propagate).
- **`services/media/ffmpeg.py`** — probe, sample_frames (1/2s default, 1/10s long-form branch, scale-cap 720p), extract_audio (16kHz mono WAV for Whisper), make_thumbnail (720w JPEG at duration midpoint).
- **`services/media/supabase_storage.py`** — upload/signed_url/delete via Storage REST API with apikey + service-role auth.
- **`services/llm/gemini.py`** — SummaryModel with structured output (JSON schema), Flash-Lite default, temp 0.3, multimodal (frames + audio) or transcript-only.
- **`services/llm/groq_whisper.py`** — TranscriptionModel for the long-audio branch.
- **`services/memory/cognee_cloud.py`** — full MemoryStore (write, write_batch, delete, search, similar, delete_namespace) via httpx REST.
- **`services/ingest/pipeline.py`** — 11-step orchestrator matching architecture.md §4.1: state transitions with audit rows, short-txn DB helpers so ffmpeg/Gemini don't hold row locks, cleanup of tmpfs in finally.
- **`app/tasks/worker_entry.py`** — /work/ingest live route; import/delete_user/category_backfill stub routes for Phase 2/3/4.
- **`app/worker.py`** — dedicated worker ASGI entrypoint for the prod Cloud Run worker service.
- **Local dispatcher** now actually calls `ingest_item` in dev.
- **Tests**: `tests/unit/test_platform_detect.py` (9 platforms + 7 dedup cases) + `tests/unit/test_extractor_registry.py` (fallback + error-propagation semantics) + `tests/conftest.py` (env-var scaffolding).

### 🔴 Missing (Phase 1 remainder)

- **`services/extractors/gallerydl.py`** — image extractor for IG carousels + X threads.
- **`services/memory/cognee_oss.py`** — escape-hatch stub (only needed if Cognee Cloud outage forces a swap).
- **`services/ingest/taxonomy.py`** — canonical taxonomy + `decide_tier()` + `is_category_clear()` (blocking for Phase 2 IG import).
- **`services/search/retriever.py`** + **`services/search/answerer.py`** — search RAG loop.
- **`/v1/search`** endpoint.
- **`/v1/events`** SSE endpoint for item-state transitions.
- **`/v1/items/{id}/enhance`** endpoint.
- **`/v1/items/{id}/retry`** endpoint.
- **`/v1/notifications/token`** endpoint.
- **`services/imports/ig_parser.py`** — BeautifulSoup HTML parser (F8.3).
- **`services/imports/ig_pipeline.py`** — bulk import orchestrator.
- **`/v1/import/instagram`** upload endpoint.
- **`/v1/import/{id}`** progress endpoint.
- **Cloud Tasks HTTP push** — real GCP integration behind the current local-dispatcher shim.
- **User deletion cascade worker** — task_type=`delete_user` (currently stub).
- **Category backfill classifier worker** — task_type=`category_backfill` (currently stub).
- **Integration test** for the full pipeline against live Gemini + Cognee (rate-limited).

### ⏳ Pending

- Boot the local Python env (`uv sync` inside `apps/api/`) and run `uvicorn app.main:app --reload` for the first time.
- Verify JWT auth end-to-end: mint a real Supabase session from mobile, call `/v1/health`, then `/v1/me` and see the auto-created profile row.
- Wire real Cobalt on GKE (staging) once Phase 0 infra work starts.

---

## 4. Data Layer (Supabase Postgres, live)

### ✅ Done

- Project provisioned: **fcefeiwvnzdfazwxdejy** (ap-south-1, Postgres 17.6).
- **10 domain tables** applied (`profiles`, `items`, `categories`, `item_categories`, `collections`, `collection_items`, `imports`, `saved_audio`, `ingestion_events`, `push_tokens`).
- **14 RLS policies** — verified enforced (anon INSERT rejected with "new row violates row-level security policy").
- **`on_auth_user_created` trigger** — new signups auto-create a `profiles` row.
- **`alembic_version` stamped** to `0001_initial` — future migrations chain from here.
- **JWKS endpoint** returns ES256 signing key (kid `085f4960-ec07-487c-811f-c7568d7633d1`).
- **Storage bucket names configured** in `.env` (`media`, `exports`) — buckets themselves need to be created via dashboard.

### ✅ Done (2026-09-19 via Supabase MCP)

- **Storage buckets** — `media` (private, 10 MB cap, image/jpeg|png|webp) and `exports` (private, 500 MB cap, application/json|zip) created.
- **Storage RLS policies** — 8 policies (SELECT/INSERT/UPDATE/DELETE × 2 buckets) scoped to `(storage.foldername(name))[1] = auth.uid()::text`.
- **`alembic_version` lockdown** — RLS enabled with no policies, so anon/authenticated see nothing (only service-role bypass reads/writes it).
- Migration script committed at `apps/api/scripts/apply_0002_storage_and_lockdown.sql` for reproducibility.

### 🔴 Missing

- **OAuth providers** — Google + Apple + Email not yet configured in Supabase Auth → Providers.
- **Google OAuth 2.0 Client ID + Secret** — need to create in Google Cloud Console, upload to Supabase.
- **Apple Services ID + p8 key** — need to create in Apple Developer portal, upload to Supabase.

### ⏳ Pending

- Consider tightening the `alembic_version` write path so app boots don't require CI to stamp — for now the SQL script does it.
- Backup verification (Supabase Pro gives 7-day PITR — worth doing one test restore before launch).

---

## 5. Infrastructure

### ✅ Done

- **Repo pushed** to https://github.com/Aditya232-rtx/spillthreel (main branch, 8 commits).
- **`.mcp.json`** committed — Supabase MCP available on session restart.
- **`.gitignore`** covers all env files (`!.env.example` allowlist), Python caches, `.expo/`, editor dirs.

### 🔴 Missing (Phase 0 work still owing)

- **Terraform modules** — Cloud Run (api + worker), Cloud Tasks, GKE Autopilot (Cobalt), Secret Manager, VPC, Cloud CDN. Nothing provisioned in GCP yet.
- **GCP project + billing** — need to be set up.
- **GKE Autopilot cluster** for Cobalt.
- **Cobalt self-hosted deployment manifests** (`infra/cobalt/`).
- **CI workflows** (`.github/workflows/`) — mobile-ci, mobile-release, api-ci, api-deploy-staging, api-deploy-prod, infra.
- **EAS Build + Submit configs** — profile production + preview, iOS/Android bundle IDs verified.
- **Apple Developer + Google Play Console** accounts (admin-side setup outside the repo).
- **Domain** (`spillthereel.app` or similar) not yet purchased.
- **Cognee Cloud account** — need to sign up, capture API key.
- **Gemini API key** — need to request from ai.google.dev.
- **Groq API key** — optional for v1 (only long-form audio), can defer.
- **Sentry projects** (mobile + backend).
- **PostHog project**.

---

## 6. Design Assets

### ✅ Done

- **Design system doc** — palette, type scale, radii, spacing, hard-shadow rule, six screen archetypes, 25-screen inventory, JSON tokens block.
- **Logo** — "The Reel Drip" (play triangle melting into droplet + satellite drop). Four SVG variants shipped: primary (coral), chat (peri on ink), monochrome (ink), horizontal lockup with wordmark.
- **Design handoff prompts** — master brand prompt + 11 per-screen prompts formatted for claude.ai design.
- **Reference HTML prototype** dogfooded and ported into RN screens.

### 🟡 Needs improvement

- **Logo** was reactively designed — worth a professional pass before store submission.
- **App icon** (1024×1024 iOS + Android adaptive layers) — not yet produced.
- **Splash screen** — currently Expo default; needs custom.
- **App Store screenshots** (6.7" + 6.5" + 5.5" iOS, various Android densities).

### 🔴 Missing

- **Illustrations for all 12 canonical categories** (design-system.md §5.3 lists them; only 5 have PNG icons via the reference).
- **Empty-state illustrations** for library, categories, chat, import.
- **Success illustration** for import-complete screen.
- **`reel-brain` legacy logo files** — deprecated per design-system.md §16.10, should be removed from `assets/logo/` in a cleanup pass.

---

## 7. Known Issues / Follow-ups

- **Welcome logo animation** — see Mobile §2. Web-only rendering issue.
- **JWT SECRET in .env** — user set `SUPABASE_LEGACY_JWT_SECRET` even though the project is on ES256. Harmless (verifier will simply never hit the HS256 branch on new tokens) but the .env comment says "leave blank" — either update the comment or drop the value once we confirm no legacy tokens circulate.
- **Cost model** in TRD §19 uses Supabase Pro pricing ($25/mo flat + usage). Once we're actually on Pro, verify against real invoices.
- **Region choice** (ap-south-1) is optimized for Indian users but adds ~200ms to every Gemini/Cognee call (both US-hosted). Worth measuring end-to-end save-to-ready latency and re-evaluating if it exceeds PRD's 60s median target.
- **No RLS tests in CI yet** — TRD §18 promises a dedicated pytest suite. Every schema change should re-run this before merging.
- **`alembic_version` table has no RLS** — intentional (Alembic bypasses it via service_role) but worth documenting explicitly to avoid a future audit flag.

---

## 8. Immediate Next Steps (session-boundary handoff)

In priority order. Session should pick up here:

1. **Drop Gemini + Cognee + Groq API keys** into `apps/api/.env` — unblocks live pipeline testing.
2. **`uv sync` in `apps/api/`** — install deps.
3. **Boot the backend locally** — `uv run uvicorn app.main:app --reload`. Curl `/health`.
4. **Manual smoke test** — mint a real Supabase JWT from the mobile app OR via `supabase auth` CLI; POST to `/v1/saves` with a real IG reel URL; watch the ingestion pipeline run end-to-end (Cobalt fetch → ffmpeg → Gemini → Cognee → thumbnail in Supabase Storage → item.state=fully_indexed).
5. **Configure Google + Apple + Email providers** in Supabase Auth → Providers.
6. **Wire Supabase JS on mobile** (`@supabase/supabase-js` + secure-store session adapter).
7. **Build search RAG loop** — `services/search/retriever.py`, `services/search/answerer.py`, `/v1/search` endpoint.
8. **Build IG import pipeline** (Phase 2) — `services/imports/ig_parser.py`, `ig_pipeline.py`, `/v1/import/instagram` endpoint.

---

## 9. Commit History Snapshot

```
8aa3b9d feat(db): apply initial schema to Supabase Postgres
561605d chore: add Supabase MCP server to project config
7d1d10f feat(backend): Supabase new-format API keys + asymmetric JWT (ES256/JWKS)
27c7d60 chore(api): add .env.example template and expand .gitignore
f4986e0 feat(backend): Phase 0 API scaffold + swap to Supabase (Auth+DB+Storage)
25fee91 feat(categories): dial physics polish, add-category flow, real Oswald weights
284e6bc feat: implement the infinite rotating category dial
3d675ee feat: scaffold Expo RN app with kitsch-minimal design system
49fb7b4 docs: initial PRD, TRD, architecture, buildphase, design system, design handoff
```
