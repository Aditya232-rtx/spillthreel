# SpillTheReel — Build Updates & Punch List

**Last updated:** 2026-10-14
**Repo:** https://github.com/Aditya232-rtx/spillthreel
**Latest commit:** `f7db275` — chore(infra): unmap versionless scrapcreators secret
**Branch:** `main` — in sync with origin, all 4 CIs green (api-ci, mobile-ci, terraform-plan, build-and-push)

Living document. Keep this in the repo root, update on every session close.
Structure per section: **✅ Done · 🟡 Needs improvement · 🔴 Missing · ⏳ Pending**.

## 0. Current State — Phase Completion (2026-10-14)

| Phase | Progress | Standing |
|---|---|---|
| **Phase 0** — staging deploy (Apple signup excluded) | **~95%** | ✅ 3 Cloud Run services live, queues RUNNING, 7→9 secrets, WIF + CI deploy loop, both DBs at hardened head, B9 6/6 smokes. ⏳ EAS green build + real-device testing (first build failed cloud gradlew — diagnosis open). 🔴 Human account items only: Play Console, domain, Supabase Pro/PITR verify, Gemini quota. |
| **Phase 1** — search RAG + endpoints + ingestion rebuild | **~90%** | ✅ All endpoints built/deployed/smoked (search, retry/enhance, gallerydl, events/notifications, imports, ig_pipeline, item screen, mobile chat). Apify + Bright Data download-verified incl. Google-network runs. 🟡 TikTok proof needs real URLs; ScrapeCreators needs key + test; full Cloud Shell matrix; egress-strategy decision. 🔴 Deferred smalls: Last-Event-ID resume, notifications list, CHECK constraint, `text_indexed` state question, search statement-cache fix, `image_tag` TF var, deployer role scoping, PR-comment step, parser-corpus fixture (12 unit skips), RLS suite in CI, OTel/metrics. |
| **Mobile app overall** | **~80%** | ✅ Chat/search/items wired to backend, detail screen, audit TOP 5 fixed. 🟡 Home/profile still mock data, import UI simulated, share-intent unwired, push sending unsent. 🔴 No device testing yet (blocked on EAS green). |
| **EAS / device testing** | **~20%** | 🟡 First preview build failed at `Run gradlew` (unknown task). Prebuild clean locally, `newArchEnabled` fix committed, Android SDK present on Mac. ⏳ Need failing task name (build page) or local gradle repro, then rebuild. |

### Pending (needs a human — agents cannot)
- Play Console signup; domain purchase/DNS; Supabase Pro + PITR verify (staging+prod); Gemini quota check; app-name reservation.
- `terraform apply` for any future infra wave (all applies to date done; none pending right now).
- EAS build page read (failing gradle task name) if local repro stalls.
- Egress-strategy decision (Cloud NAT static IP recommended) before production ingestion.
- Paste `SCRAPECREATORS_API_KEY` when signed up; re-add worker mapping (3-step recipe in TF commit notes).

### Missing (no owner yet)
- TikTok proof URLs (2–3 real ones) for provider verification.
- `GET /v1/notifications/token?list`, Last-Event-ID resume, notifications push sending.
- RLS pytest suite in CI; OTel spans + Cloud Monitoring metrics; TanStack Query hooks.
- Onboarding walkthrough, notification opt-in, empty states, saved-audio tab, export UI, localization, share-intent JS wiring, `useShareIntent` provider.

### Needs improvement (ranked)
1. Search transient 500 under pooler → `statement_cache_size=0` in engine config.
2. `text_indexed` state unreachable — implement text-first path or drop from constraint.
3. SSE long-lived connections vs pool size (per-poll sessions + idle timeout).
4. Home/profile mock data → real `GET /v1/*` wiring; import UI simulated → real upload.
5. `expo-share-intent` MIME config + preview-build verification.
6. Mobile package/SDK drift (`expo-doctor` 20/21) + add `typecheck` script, make doctor blocking.

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
- **Auth flow hardening** — fixed OAuth loop-back (web `detectSessionInUrl`, native deep-link `app/auth/callback` route, Supabase redirect-URL whitelist), central `AuthGate` + session-aware index (no more welcome-bounce), duplicate-email signup detection (silent sign-in proof + inline "already has an account" note under the email field), pending-confirmation flow (proceeds to import, `emailRedirectTo` returns link taps to the app).
- **Password reset** — `(auth)/forgot-password` (email → `resetPasswordForEmail` with deep-link redirect) + `auth/reset-password` landing screen (link verification, new + confirm fields, expired-link handling, resend). Login link moved below the Log in button as minimal coral text.
- **Password rules** — shared `src/lib/password.ts` (8+ chars, letter + number), confirm fields on signup + reset, client-side pre-checks.
- **Native Sign In with Apple** on iOS (`expo-apple-authentication` + `expo-crypto` nonce, `signInWithIdToken`), web-OAuth fallback elsewhere.
- **Auth hygiene** — `__DEV__`-gated sanitized OAuth logs (tokens redacted), 401 → local `signOut()` via `AuthGate` to welcome, fail-fast on missing `EXPO_PUBLIC_*` env (+ `apps/mobile/.env.example`).
- **Dynamic identity** — `src/lib/display-name.ts` drives home greeting, avatar initial, and profile (name, handle, join date, avatar initial); `(auth)/your-name` step in OAuth-signup; inline profile name editor; per-email last-choice-wins guard re-applied on every `SIGNED_IN` so provider logins can't silently rename.
- **Verify nudges** — `src/lib/verification.ts` (pending flag, dismissal, resend); import guide browsable pre-confirmation; confirm card + resend on import3; coral dot on home avatar + auto-popup anchored at the profile icon.
- **Chat rebuild** — live per-user conversations with typing indicator and persisted history drawer (bottom hamburger, slide-in panel, new/switch/delete); mic/send morph button (coral mic, hold-to-talk, Web Speech on web); suggestion chips send on tap; fixed-height chip row; SVG back chevron (optically centered); frosted-glass back buttons matching the dock; chat empty state ("Ask your second brain anything.").
- **Welcome wordmark** coral (`#E85C3F`); MyLove weight bug fixed (no `fontWeight` on single-weight script — Android fell back to system sans).

### 🟡 Needs improvement

- **Welcome-screen animation** — the dot-burst → logo crossfade has an unresolved react-native-web Image stacking-context issue; currently hard-swaps rather than crossfades. Works fine on native devices per the RN docs; only web-preview shows the bug.
- **Card shadow visibility** — on some cards the 5px offset is subtle. Could switch to native `boxShadow` (RN 0.76+) when we've dropped web preview from the dev loop.
- **Font weight variation** — currently loading four static weights. Not exercising all four consistently in screens; audit needed.

### 🔴 Missing

- **Item detail sheet** (PRD F4.5) — not built.
- **Search screen** (PRD F3.3, distinct from the Chat screen) — not built. Filter chips, similar-to-this, query history. (Chat has live local conversations + stub replies; no backend RAG yet.)
- **Library browse tab** — currently the Home screen doubles as this. Needs full virtualized list per PRD F4.1.
- **Search-permission onboarding walkthrough** (PRD F5.5) — not built.
- **Notification opt-in flow** (PRD F5.8) — not built.
- **Empty states** for library + categories (chat empty state built).
- **Item state badge** component (queued / analyzing / fully_indexed / failed).
- **Enhance CTA** on text-indexed items (PRD F4.5, F8.12).
- **Saved Audio tab** (PRD F8.7).
- **Data export UI** (PRD F7.3).
- **Localization scaffolding** — `en` only, but no i18n structure to add languages later.

### ⏳ Pending (blocked on backend or later phases)

- **TanStack Query hooks** — `src/lib/api.ts` fetch wrapper exists (JWT attach, 401 → signOut); per-screen `useQuery`/`useMutation` hooks not yet written.
- **Share-sheet / share-intent integration** (PRD F1) — configs in place, native code not yet wired.
- **Expo push notifications** (PRD F6) — deferred to Phase 3.
- **PostHog SDK bootstrap** + event schema.
- **Sentry SDK integration** (mobile; backend hook exists).

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
- **docker-compose.yml** — postgres + api for local dev (no extractor container; scraper head is `SCRAPER_PROVIDER=none` no-op locally).
- **`.env.example`** with inline docs for every var + SECRET vs public labels.
- **`.env`** filled locally with real Supabase creds (gitignored).
- **`pyproject.toml`** — full dep manifest with `worker` and `dev` extras.

- **Rate limiting** (`app/ratelimit.py`, `slowapi` dep) — 60 req/min default on every route via `DefaultRateLimitMiddleware` (custom: stock `SlowAPIMiddleware` silently exempts Starlette `_IncludedRouter` routes), keyed by JWT `sub` else IP (XFF-aware); stricter 10/min on `POST /v1/saves`; 429 with `Retry-After: 60`. Limits resolve from settings per-request (`RATE_LIMIT_DEFAULT`, `RATE_LIMIT_SAVES`).
- **CORS lockdown** — `"*"` only in dev; staging/prod require explicit `CORS_ALLOWED_ORIGINS` and refuse to boot without it.
- **Profile display-name sync** — auth middleware updates `profiles.display_name` from JWT claims whenever it changes (was write-once at creation).
- **Tests**: `tests/unit/test_auth_security.py` (expired / malformed / wrong-audience / HS256-without-secret / unsupported-alg JWTs) + `tests/integration/test_rate_limit.py` (429 + `Retry-After`, global and per-route paths). Full suite: 58 passed, 12 skipped.

### 🟡 Needs improvement

- **Test coverage** — auth + rate-limit suites added (58 passed); ingestion pipeline itself still untested (needs mock fixtures for the four external services).
- **OpenTelemetry tracing** — TRD §17.2 promises spans on every service call; not wired.
- **Custom Cloud Monitoring metrics** — TRD §17.3 promises 8 histograms/gauges; not wired.
- The Alembic migration (`20260918_0001_initial.py`) and the pure-SQL script (`apply_initial_schema.sql`) are duplicated by hand — need a linter that alerts on drift.
- **Health endpoint** returns hardcoded version; should read from a build-time env var.
- **Ruff/mypy** — repo baseline has pre-existing violations (25 ruff, ULID `call-arg` mypy); touched files kept clean, full cleanup still owing.

### ✅ Done (Phase 1 core scaffolding — 2026-09-19)

- **`services/extractors/scraper/`** — `ScraperApiExtractor` (ScrapeCreators REST + Apify run-sync actors `apify/instagram-scraper` / `clockworks/tiktok-scraper`, `SCRAPER_PROVIDER` switch); hardened downloader (https-only, DNS-pinned, `MEDIA_HOST_ALLOWLIST`, 100/20/20 MB caps); YouTube-native path (oEmbed + Data API v3 → Gemini FileData URL input, 600s cap, `YOUTUBE_NATIVE_ENABLED` escape hatch); canonical YouTube watch URLs + `is_shorts_url`.
- **`services/extractors/ytdlp.py`** — lazy-imported yt-dlp, offloads to thread, captures uploader metadata.
- **`services/extractors/registry.py`** — priority-ordered per-platform chains (IG [scraper→ytdlp→gallerydl], TikTok [scraper→ytdlp], X [ytdlp→gallerydl]; YouTube native, no registry), typed errors (`SourceGoneError`→`source_gone`, `ProviderQuotaError` never-gone fallthrough).
- **`services/media/ffmpeg.py`** — probe, sample_frames (1/2s default, 1/10s long-form branch, scale-cap 720p), extract_audio (16kHz mono WAV for Whisper), make_thumbnail (720w JPEG at duration midpoint).
- **`services/media/supabase_storage.py`** — upload/signed_url/delete via Storage REST API with apikey + service-role auth.
- **`services/llm/gemini.py`** — SummaryModel with structured output (JSON schema), Flash-Lite default, temp 0.3, multimodal (frames + audio) or transcript-only.
- **`services/llm/groq_whisper.py`** — TranscriptionModel for the long-audio branch.
- **`services/memory/pgvector_store.py`** — full MemoryStore (write, write_batch, delete, search, similar, delete_namespace) on pgvector + Gemini embeddings, user-scoped queries throughout.
- **`services/ingest/pipeline.py`** — 11-step orchestrator matching architecture.md §4.1: state transitions with audit rows, short-txn DB helpers so ffmpeg/Gemini don't hold row locks, cleanup of tmpfs in finally.
- **`app/tasks/worker_entry.py`** — /work/ingest live route; import/delete_user/category_backfill stub routes for Phase 2/3/4.
- **`app/worker.py`** — dedicated worker ASGI entrypoint for the prod Cloud Run worker service.
- **Local dispatcher** now actually calls `ingest_item` in dev.
- **Tests**: `tests/unit/test_platform_detect.py` (9 platforms + 7 dedup cases) + `tests/unit/test_extractor_registry.py` (fallback + error-propagation semantics) + `tests/conftest.py` (env-var scaffolding).

### 🔴 Missing (Phase 1 remainder)

- **`services/extractors/gallerydl.py`** — image extractor for IG carousels + X threads.
- (Cognee OSS escape hatch dropped — nothing external left to escape from.)
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
- **Integration test** for the full pipeline against live Postgres (pgvector) + Gemini (rate-limited).

### ⏳ Pending

- Boot the local Python env (`uv sync` inside `apps/api/`) and run `uvicorn app.main:app --reload` for the first time.
- Verify JWT auth end-to-end: mint a real Supabase session from mobile, call `/v1/health`, then `/v1/me` and see the auto-created profile row.
- Verify extractor chains end-to-end: scraper head with `SCRAPER_PROVIDER=none` falls through cleanly; paid paths verified via `egress_check.py` from Cloud Shell.

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

- **Apple provider** — Services ID + p8 key still need creating in the Apple Developer portal and uploading to Supabase Auth → Providers. (Google + Email configured and verified live: Google round-trip lands in-app, confirmation mail sends; "Confirm email" currently ON.)
- **Leaked-password protection** — dashboard toggle (Auth → Password protection), not yet enabled.

### ⏳ Pending

- Consider tightening the `alembic_version` write path so app boots don't require CI to stamp — for now the SQL script does it.
- Backup verification (Supabase Pro gives 7-day PITR — worth doing one test restore before launch).

---

## 5. Infrastructure

### ✅ Done

- **Repo pushed** to https://github.com/Aditya232-rtx/spillthreel (main branch — see §9 for current history).
- **`.mcp.json`** committed — Supabase MCP available on session restart. OpenCode global config also carries the server (authenticated via `opencode mcp auth`, browser OAuth, verified connected).
- **Agent skills** committed (`.agents/skills/`: `supabase`, `supabase-postgres-best-practices` + `skills-lock.json`), mirrored to `~/.agents/skills` for OpenCode auto-loading.
- **`.gitignore`** covers all env files (`!.env.example` allowlist), Python caches, `.expo/`, editor dirs — plus Terraform state/vars, mobile signing artifacts (`google-services.json`, keystores, p8/p12), real-user export fixtures (`user_export_*.zip`), and `bugreport-*.zip` emulator dumps.

### 🔴 Missing (Phase 0 work still owing)

- **Terraform modules** — Cloud Run (api + worker), Cloud Tasks, Secret Manager (incl. empty shells `SCRAPECREATORS_API_KEY` / `APIFY_API_TOKEN` / `YOUTUBE_API_KEY`), WIF. Nothing provisioned in GCP yet. No extractor service, no NAT/proxy (default egress).
- **Self-hosted extractor dropped** — no GKE cluster, no self-hosted extractor manifests (retired; see TRD §22).
- **GCP project + billing** — need to be set up.
- **CI workflows** (`.github/workflows/`) — mobile-ci, mobile-release, api-ci, api-deploy-staging, api-deploy-prod, infra.
- **EAS Build + Submit configs** — profile production + preview, iOS/Android bundle IDs verified.
- **Apple Developer + Google Play Console** accounts (admin-side setup outside the repo).
- **Domain** (`spillthereel.app` or similar) not yet purchased.
- (Memory-vendor account removed — pgvector needs no signup.)
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
- **Region choice** (ap-south-1) is optimized for Indian users but adds ~200ms to every Gemini call (US-hosted). Worth measuring end-to-end save-to-ready latency and re-evaluating if it exceeds PRD's 60s median target.
- **No RLS tests in CI yet** — TRD §18 promises a dedicated pytest suite. Every schema change should re-run this before merging.
- **`alembic_version` table has no RLS** — intentional (Alembic bypasses it via service_role) but worth documenting explicitly to avoid a future audit flag.
- **Confirmation links are web-origin on web** — `emailRedirectTo` is `window.location.origin` for web signups, so tapping the link on a phone browser pointed at localhost goes nowhere; confirm on the Metro host or sign up from the native app (deep-link return). Revisit with a hosted domain before launch.
- **Voice input is web-only for now** — hold-to-talk UI + timer + release-to-send work everywhere, but transcription uses the browser Speech API; native needs an STT module (dev build only, not Expo Go).
- **Past reference images went missing** — several pasted screenshots arrived blank on the agent side; the pixel-measured chevron + screenshot-verified chat pass replaced that loop. Prefer committing reference assets under `assets/` when pixel-fidelity matters.

---

## 8. Immediate Next Steps (session-boundary handoff)

In priority order. Session should pick up here:

1. **EAS green build** — get failing gradle task (build page or local SDK repro) → fix → `gh workflow run eas-preview` → install APK → real-device pass (share-receive, chat search, item screen, import).
2. **Egress decision + full matrix** — Cloud Shell run (~50 URLs) → pick primary provider → point staging users at new pipeline.
3. **TikTok proof** — 2–3 real URLs through Bright Data/Apify → download bytes.
4. **Small fixes batch** — statement_cache_size, Last-Event-ID, notifications list, CHECK constraint, image_tag var, deployer scoping, parser-corpus fixture.
5. **Observability** — OTel spans, Cloud Monitoring metrics + alerts (TRD §17).
6. **Remaining product UI** — home/profile real data, import UI wiring, share-intent, push sending, onboarding/empty states.
7. **Apple provider** — Services ID + p8 → Supabase dashboard (needs Apple Developer account).

---

## 9. Commit History Snapshot

```
f7db275 chore(infra): unmap versionless scrapcreators secret
d1ef810 fix(mobile): account deletion + oauth + timeouts + history
1e54505 fix(api): categories 409 + items cursor/filters
24de950 feat(api): LLM budget guards
6f6e77b feat(api): category backfill
0f921f7 feat(api): user deletion purge task
6dc5cd5 chore(infra): worker SCRAPER_PROVIDER staging env
4768b56 chore(api): imports wiring + ffmpeg + WORKER_BASE_URL empty-string
fa71bc0 feat(mobile): item detail screen
103de4e feat(api): IG import pipeline
0402913 chore(build): Cloud Build egress probe config
b4eca10 feat(api): Bright Data sync-scrape first, trigger fallback
83b5c93 fix(api): Apify actor URL tilde-encoding + 404 taxonomy
7bd9cbd feat(api): egress_check supports brightdata provider
15cc9c1 chore(mcp): Bright Data MCP placeholder config
(older: fe77ca5 docs Cobalt retire → back to 49fb7b4 initial docs; full log: git log --oneline)
```
