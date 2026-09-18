# SpillTheReel — Build Phase Plan

**Version:** 1.0
**Status:** Draft — pre-build
**Owner:** Aditya Jadhav
**Last updated:** 2026-09-10

Companion documents: [prd.md](./prd.md), [trd.md](./trd.md), [architecture.md](./architecture.md).

---

## 1. Purpose & How to Read This

This document sequences the build. Each phase is:

- **Goal** — the outcome of the phase in one sentence.
- **Deliverables** — concrete artifacts (code, infra, docs).
- **Tasks** — the ordered work to produce those deliverables.
- **Exit criteria** — objective checks that must pass before moving on.
- **Estimated duration** — solo-founder pace; parallelizable if a second engineer joins.
- **Risks / decisions to lock** — what could stall this phase and what needs approval.

Phases are numbered 0–7. Phase 0 is setup and can start immediately. Phases 1–5 build v1.0. Phase 6 launches. Phase 7 covers immediate post-launch stabilization.

**Guiding principles across phases:**
- Ship the thin vertical slice first (Phase 1–2), then broaden.
- Write the smallest test that would have caught the bug you just fixed.
- Land infra changes with Terraform, never manually — from day 1.
- Every phase produces something demoable, even if narrow.

---

## Phase 0 — Foundations & Setup

**Goal:** Empty-but-real skeleton of the entire monorepo, GCP infra, and CI/CD, ready for feature work.

**Estimated duration:** 5–7 days.

### Deliverables

- GitHub repo `spillthereel` with the monorepo structure from architecture.md §5.
- Terraform for `dev` (local + minimal GCP) and `staging` environments.
- **Supabase projects (staging + prod)** with Auth, Postgres (RLS enabled), and Storage buckets `media` + `exports` provisioned. Google + Apple + Email providers configured. New-format keys captured: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (`sb_publishable_…`), `SUPABASE_SECRET_KEY` (`sb_secret_…`), plus `DATABASE_URL` (pooler `:6543`) and `DATABASE_URL_DIRECT` (`:5432`). JWT verification uses the public JWKS at `{SUPABASE_URL}/auth/v1/.well-known/jwks.json` — no shared JWT secret needed for new projects.
- Cognee Cloud Developer account created; API key in Secret Manager (staging).
- Gemini API key + Groq API key + Cobalt API key generated and stored in Secret Manager (staging).
- Expo project initialized with EAS Build configured for iOS + Android.
- FastAPI app skeleton with health endpoint deployed to Cloud Run staging.
- GKE Autopilot cluster with Cobalt deployed (staging).
- CI workflows for `mobile-ci`, `api-ci`, and Terraform plan.
- Docker Compose local-dev setup that boots api + worker + local Postgres 15 (Supabase-compatible schema) + Cobalt.
- Sentry projects (mobile + backend) linked.
- PostHog project created; SDK wired into mobile app (event schema TBD).
- Documentation: `README.md` at repo root with local dev instructions.

### Tasks

1. Create the monorepo skeleton (folders + placeholder README per subfolder).
2. Init Expo app `apps/mobile/` with TypeScript strict, `expo-router`, Zustand, TanStack Query.
3. Init FastAPI app `apps/api/` with Poetry / uv, pytest, ruff, mypy.
4. Add Alembic + initial DB schema migration (`profiles` + `items` skeleton, RLS policies enabled).
5. Wire Supabase project + JWT verification in FastAPI (`PyJWT` + `PyJWKClient` for asymmetric ES256 via the public JWKS endpoint; fallback branch for HS256 if `SUPABASE_LEGACY_JWT_SECRET` is set).
6. Write `MemoryStore`, `Extractor`, `SummaryModel`, `TranscriptionModel` protocols as empty ABCs — no implementations yet.
7. Add `apps/api/app/settings.py` with all env vars enumerated.
8. Write Terraform modules: Cloud Run, Cloud SQL, GCS, Secret Manager, Cloud Tasks, VPC connector, GKE Autopilot for Cobalt.
9. Provision staging with Terraform. Deploy `apps/api/` skeleton to Cloud Run staging.
10. Deploy Cobalt to GKE Autopilot staging.
11. Add GitHub Actions workflows.
12. Add `scripts/local-dev.sh` with docker-compose stack.
13. Add Sentry + PostHog SDK bootstrapping (mobile + backend).
14. Wire `expo-share-intent` and `expo-share-extension` config plugins with placeholder no-op targets — confirms iOS + Android build succeeds with share targets registered.

### Exit criteria

- ☐ `curl https://api.staging.spillthereel.app/health` returns 200 with `{"status": "ok"}`.
- ☐ A local `expo run:ios` and `expo run:android` succeed; app shows a "hello" screen.
- ☐ Signing into a test Firebase account from the mobile app returns a valid ID token.
- ☐ Backend accepts a signed token and rejects unsigned.
- ☐ `terraform plan` on `staging` shows no drift.
- ☐ Cobalt staging responds to a test download request.
- ☐ CI green on `main`.

### Risks / decisions to lock

- Apple Developer + Google Play Console accounts must exist and payments cleared. Non-trivial admin time — start this in parallel to Phase 0 day 1.
- Bundle identifiers finalized (`com.spillthereel.app` etc.).
- Cognee Cloud beta access confirmed; if unavailable, defer to Cognee OSS on Cloud Run (adds ~3 days).
- Supabase Pro plan ($25/mo) enabled for staging + prod — free tier does not include PITR backups or the pooler-in-session-mode we rely on.
- Google OAuth 2.0 Client + Apple Services ID uploaded to Supabase Auth → Providers.

---

## Phase 1 — Thin Vertical Slice: Save → Ingest → Search (Web-share Path)

**Goal:** End-to-end path from *paste-a-URL* (not yet share sheet) to *searchable answer* — the smallest possible vertical slice that proves the pipeline.

**Estimated duration:** 10–14 days.

### Deliverables

- `POST /v1/saves` accepting a URL and returning `itemId`.
- Cloud Tasks `ingest` queue and worker consuming it.
- `Extractor` impls: Cobalt (primary), yt-dlp (fallback).
- ffmpeg utilities for frame sampling, audio extract, thumbnail.
- `SummaryModel` impl: Gemini (Flash-Lite default).
- `MemoryStore` impl: Cognee Cloud.
- `POST /v1/search` returning answer + hydrated items.
- Mobile: temporary paste-URL screen, list view of items, tap-to-detail, search screen.
- Basic SSE stream at `/v1/events` for state updates.
- Postgres schema fully migrated: users, items, ingestion_events.

### Tasks

1. Implement `services/extractors/cobalt.py` calling the staging Cobalt HTTP API. Cover Instagram Reels, TikTok, YouTube Shorts, X.
2. Implement `services/extractors/ytdlp.py` as fallback using the Python API.
3. Implement `services/media/ffmpeg.py` — frame sampling and audio extraction to /tmp.
4. Implement `services/llm/gemini.py` with `SummaryModel.summarize` for Flash-Lite. JSON-schema-validated output via Pydantic. One-retry-with-repair-prompt on invalid JSON.
5. Implement `services/memory/cognee_cloud.py` with `write`, `delete`, `search`, `similar`, `delete_namespace`.
6. Implement `services/ingest/pipeline.py` — the orchestrator from architecture.md §6.
7. Wire Cloud Tasks: enqueue on `POST /v1/saves`; worker HTTP endpoint that consumes.
8. Implement `services/search/retriever.py` and `services/search/answerer.py`.
9. Implement `/v1/events` SSE endpoint scoped by user.
10. Mobile: build the paste-URL screen (temporary; will be gone in Phase 3).
11. Mobile: build the item list view (chronological, virtualized).
12. Mobile: build the item detail sheet.
13. Mobile: build the search screen with the answer block + result cards.
14. Mobile: subscribe to SSE and update item state in the list live.
15. Add unit tests for `Extractor` implementations against a fixed URL corpus.
16. Add integration test for the full pipeline against a real staging Cognee + Gemini (rate-limited).

### Exit criteria

- ☐ From the mobile app, paste an Instagram Reel URL → item appears in list with `queued` state → transitions through states → lands `fully_indexed` within 60 s.
- ☐ Search for a keyword present in that reel's audio → answer returned with the reel cited.
- ☐ Tapping the answer's citation opens the item detail.
- ☐ Deleting the item removes it from Cognee within 5 s (verified by re-search returning no result).
- ☐ Same flow works for TikTok, YouTube Shorts, X.
- ☐ Extractor fallback: temporarily disable Cobalt for a test URL → yt-dlp handles it → item still lands `fully_indexed`.
- ☐ Test coverage ≥ 70% on backend, ≥ 60% on mobile.

### Risks / decisions to lock

- Gemini Flash-Lite output quality on short reels — validate acceptable summary quality on a 20-URL manual eval before proceeding.
- Cobalt reliability per platform — measure success rate against Instagram Reels specifically (highest volume expected).
- Set cost alarm at $5/day for Phase 1 while iterating.

---

## Phase 2 — Instagram Bulk Import (F8)

**Goal:** New user can import their entire Instagram save history from a Meta data export ZIP and have everything searchable within minutes.

**Estimated duration:** 10–12 days.

### Deliverables

- `POST /v1/import/instagram` endpoint accepting multipart ZIP upload.
- `services/imports/ig_parser.py` — BeautifulSoup parser for `saved_posts.html`, `saved_collections.html`, `saved_music.html`.
- Schema-tolerant parsing with `_unknown` bucketing and `parser_schema_hash` telemetry.
- `services/imports/ig_pipeline.py` — orchestrator with tiered processing decisions.
- `services/ingest/taxonomy.py` — canonical taxonomy + `decide_tier` + `is_category_clear`.
- Postgres tables: `imports`, `collections`, `collection_items`, `saved_audio`.
- Batch write path in `MemoryStore.write` for text-indexed entries.
- Cloud Tasks `import` queue.
- Mobile: guided IG export walkthrough (`app/import/guide.tsx`), upload screen, progress screen.
- Mobile: collection rails on home screen, collection detail view.
- Item detail: "Enhance with video analysis" button for text-indexed items.
- Parser corpus test suite with real anonymized fixtures.

### Tasks

1. Write parser corpus fixtures using anonymized snippets of the user's Aug 2026 export as ground truth.
2. Implement `ig_parser.parse_zip()` yielding entries + collections + music.
3. Build `taxonomy_config.yaml` with initial categories, keywords, and hashtag synonyms.
4. Implement `taxonomy.decide_tier()` and `is_category_clear()`.
5. Extend `MemoryStore.write` to accept text-only items with metadata.
6. Implement `ig_pipeline.process_import()` orchestrator.
7. Add Cloud Tasks queue `import` and rate-limited `enhance` queue (20/hr/user).
8. Implement `POST /v1/import/instagram` (multipart, tmpfs streaming).
9. Implement `GET /v1/import/{id}` and SSE updates on progress.
10. Extend items table with `source_type`, `caption`, `hashtags`, `owner_*` columns via Alembic migration.
11. Implement `POST /v1/items/{id}/enhance` endpoint.
12. Mobile: build `import/guide.tsx` with step-by-step screenshots of Meta's export flow.
13. Mobile: build `import/upload.tsx` with file picker.
14. Mobile: build `import/progress.tsx` with live counters (parsed, text_indexed, queued, done, failed).
15. Mobile: collection rails on home + collection detail screen.
16. Mobile: "Enhance" button on item detail (text_indexed only) with confirmation.
17. Add "enhance all in collection" bulk action to collection view.
18. Add parser telemetry: schema hash change alert.

### Exit criteria

- ☐ Upload the Aug 2026 test export → parse completes; every item present as an `items` row with correct URL, caption, hashtags, owner; every collection preserved with correct members.
- ☐ Music entries appear under Library → Saved Audio tab.
- ☐ Text-indexed items are searchable by caption content immediately (verified: search "chicken jollof" → the recipe reel appears).
- ☐ Items ≤ 30 days with ambiguous captions auto-queue for full processing (throttled queue respected).
- ☐ Re-uploading the same ZIP does not duplicate items or collections.
- ☐ Deleting a collection does not delete member items.
- ☐ Enhance flow: tap on text_indexed item → confirms → item transitions through states → lands fully_indexed with full transcript & thumbnail.
- ☐ Parser corpus tests: 100% pass on fixtures; alert fires when a novel label appears.
- ☐ Cost per 5k-item import: ≤ $0.30 measured on staging.

### Risks / decisions to lock

- Confirm parser handles both HTML and JSON export formats (v1 focuses on HTML; JSON support fast-follow).
- Decide auto-import behavior for private collections — default v1: import with a "private" badge, user can hide.
- Verify Cognee Cloud batch write throughput at 5k-item bursts; if it throttles, add local buffering.

---

## Phase 3 — Native Share Flow (F1) & Push Notifications (F6)

**Goal:** Replace the temporary paste-URL screen with the real share-sheet flow on both iOS and Android, and wire push notifications for state changes.

**Estimated duration:** 10–14 days.

### Deliverables

- iOS Share Extension with custom mini-popup UI via `expo-share-extension`.
- Android Share Intent receiver with native `Toast` confirmation, no app open.
- App Group + EncryptedSharedPreferences for auth-token sharing.
- Offline queue (SQLite in shared container) with retry-on-connectivity.
- Expo Notifications integration; push token registration endpoint.
- Backend push emission on state transitions (fully_indexed, failed, import complete).
- Batched notification logic (group multiple ready items within 60s).
- Weekly digest push (opt-in, cron via Cloud Scheduler).
- Removed: paste-URL screen from Phase 1.

### Tasks

1. Configure `expo-share-extension` iOS target: bundle ID, App Group, custom Swift view.
2. Implement the iOS share extension React view: "Saving…" → "Saved ✓", auto-dismiss in 1.2 s.
3. Wire iOS share extension to POST to `/v1/saves` with Firebase ID token read from App Group storage.
4. Configure `expo-share-intent` Android intent filter + receiver.
5. Implement Android background Service that POSTs to `/v1/saves` and shows Toast.
6. Wire Android auth token to EncryptedSharedPreferences.
7. Implement offline queue: local SQLite in shared container, retry on next connectivity.
8. Wire `expo-notifications` push token registration on first sign-in.
9. Implement `POST /v1/notifications/token`.
10. Implement `services/notifications/expo_push.py` — batched push API calls.
11. Wire state-transition hooks in ingestion pipeline to enqueue push if user not foregrounded.
12. Implement weekly-digest Cloud Scheduler job hitting a worker endpoint.
13. Remove paste-URL screen from mobile.
14. E2E test with Detox: share a URL from Safari-mobile to the app → confirms Toast → confirms item lands `fully_indexed` → confirms push received.

### Exit criteria

- ☐ On iPhone with the app installed, share an Instagram Reel from Instagram → share sheet → SpillTheReel appears → tap → mini popup shows Saved ✓ → dismisses → Instagram remains foregrounded → item appears in library on next open.
- ☐ Same on Android with a floating Toast over Instagram.
- ☐ Share-to-toast P95 < 900 ms on both.
- ☐ Push notification arrives when the item transitions to fully_indexed.
- ☐ Offline share (airplane mode) queues locally; item posts on reconnect.
- ☐ App Store review test passes: submit a preview build, confirm no rejection reason.

### Risks / decisions to lock

- iOS Share Extension bundle size — must stay < 5 MB; avoid pulling heavy deps.
- App Group entitlement configured correctly in Apple provisioning profile.
- Test on real devices, not just simulator, from the start of this phase.

---

## Phase 4 — Library, Search Polish, Onboarding

**Goal:** The app feels complete: home feed, categories, collections, search polish, onboarding, settings, account deletion.

**Estimated duration:** 12–14 days.

### Deliverables

- Home screen with category rails + collection rails + chronological feed.
- Search: voice input (`expo-speech-recognition`), filter chips, similar-to-this, query history, saved queries.
- Item detail: transcript view, tag editor, delete, "ask about this reel" quick-query.
- Onboarding: sign-in → bulk-import invitation → share-permission walkthrough → home.
- Settings screen with all F7 items (language, model preference, auto-delete, data export, danger zone, import history).
- Account deletion cascade tested end-to-end.
- GDPR data export endpoint (F7.3) producing downloadable JSON via signed URL.
- Analytics events instrumented per PostHog event schema.
- Empty state + tour for users who skip import.

### Tasks

1. Build home screen with rails + collections; use TanStack Query for lists.
2. Add filter chips + apply-to-server logic.
3. Wire `expo-speech-recognition` for voice input in search.
4. Add "similar to this" endpoint + UI trigger.
5. Add query history + saved queries (local + synced).
6. Build settings screen with all toggles.
7. Wire data export: worker builds JSON, uploads to GCS, returns signed URL, sends push.
8. Wire account deletion async cascade (worker + Firebase Admin call).
9. Build onboarding flow with three checkpoints: import intro, share permission guide, "you're set" screen.
10. Wire PostHog events per schema.
11. Add empty state with pre-seeded demo reel.
12. Accessibility pass: screen-reader labels, dynamic type, contrast checks.
13. Localization scaffolding (en only in v1, structure ready for v1.5 additions).

### Exit criteria

- ☐ Fresh install → sign in → import → share permission walkthrough → home shows imported items + collections + rails.
- ☐ Search with voice works on both platforms.
- ☐ Filter chips narrow results server-side.
- ☐ Query history persists across app restarts.
- ☐ Data export produces a valid JSON with all items + collections + metadata.
- ☐ Account deletion removes user from Firebase, Postgres, Cognee, and GCS within 5 min.
- ☐ VoiceOver / TalkBack navigation works for all primary flows.

### Risks / decisions to lock

- Voice input latency + accuracy — validate on both platforms early in the phase.
- Data-export job runtime for a 5k-item user — verify < 30 s.

---

## Phase 5 — Hardening, Observability, Cost Control

**Goal:** The app is production-safe: instrumented, alerted, cost-guarded, load-tested.

**Estimated duration:** 8–10 days.

### Deliverables

- Full structured-log coverage across every backend code path.
- OpenTelemetry traces for all critical spans (§17 TRD).
- Custom metrics exported to Cloud Monitoring.
- Alert rules configured per TRD §17.5.
- Load test with k6 at 100 rps sustained on `/v1/search` and `/v1/saves`.
- Cost dashboard: per-user daily Gemini spend, per-platform extractor cost, aggregate weekly.
- Runbooks: extractor failure, Cognee outage, Gemini quota hit, parser schema change, DB failover.
- Chaos drills: simulate Cognee outage, extractor failure, Gemini 429 — confirm graceful degradation.
- Rate-limit tuning based on real staging usage.
- Sentry dashboards for mobile crash rates + API 5xx rates.
- Security review: pentest checklist run against staging (input validation, authz, secret exposure).

### Tasks

1. Add structlog config; instrument every service call.
2. Add OTel spans in `ingest.pipeline`, `search.retriever`, `search.answerer`, `ig_pipeline`.
3. Emit Cloud Monitoring custom metrics per TRD §17.3.
4. Configure alert policies in Terraform.
5. Write k6 scripts + run against staging.
6. Wire per-user Gemini budget guard (429 at $1/day, alarm at $0.50).
7. Write runbooks in `docs/runbooks/`.
8. Run chaos drills manually; document response times.
9. Security review with a checklist (OWASP mobile top 10 + backend basics).
10. Turn on Cloud Armor rate limits at the LB.
11. Verify Postgres backups and PITR by doing a test restore in a dedicated project.

### Exit criteria

- ☐ Every route has a trace span; every error goes to Sentry with `user_id_hash`.
- ☐ Cost dashboard shows real per-user daily spend, updated in ≤ 5 min.
- ☐ All TRD §17.5 alerts fire in test.
- ☐ k6 100 rps sustained for 10 min with no P99 regression.
- ☐ Simulated Cognee outage: search returns "temporarily unavailable" with a retry, no 500 leaks.
- ☐ Security checklist: no CRITICAL / HIGH findings unresolved.

### Risks / decisions to lock

- Rate-limit thresholds — tune based on staging observations, not guessing.
- Decision: hold a "beta" flag on prod so early users can be flipped out gracefully if we discover an unforeseen cost overrun.

---

## Phase 6 — Launch

**Goal:** Ship v1.0 to App Store and Play Store, and iterate on launch-blocking feedback.

**Estimated duration:** 3–5 days of active work, plus 3–14 days waiting on store review.

### Deliverables

- App Store listing: screenshots, description, categories, privacy nutrition label, support URL.
- Play Store listing: same equivalents + data safety form.
- Privacy Policy + Terms of Service pages hosted (static site).
- v1.0 EAS Build submitted via EAS Submit to both stores.
- Prod GCP environment provisioned (mirror of staging via Terraform).
- Prod Firebase project switched on with real Google + Apple OAuth clients.
- Prod Cobalt cluster live in GKE.
- Prod Cognee Cloud plan sized for expected volume.
- Prod DNS + Cloud CDN configured.
- Post-launch monitoring dashboard published to a shared Slack channel.
- Launch communication plan (personal socials, Product Hunt draft — timing TBD).

### Tasks

1. Terraform apply `prod` environment.
2. Configure prod Firebase project + OAuth consent screens.
3. Provision prod Cobalt + Cognee namespaces.
4. Write App Store metadata (title, subtitle, keywords, description, screenshots).
5. Write Play Store metadata + Data Safety form.
6. Host privacy policy + terms on a simple static site (Firebase Hosting is fine).
7. Set up support email + basic Fresh/Help ticketing (Notion form or a Google Form to start).
8. Build v1.0 with EAS: `eas build --profile production --platform all`.
9. Submit with EAS Submit; monitor review status daily.
10. Fix reviewer feedback iteratively (extension permissions, screenshots, etc.).
11. Once approved, publish; smoke-test from a fresh device with a real Meta data export.
12. Send launch comms.

### Exit criteria

- ☐ App live on both stores.
- ☐ First 10 real users complete: sign in → import → save via share → search → recall works.
- ☐ Zero P0 bugs in first 48 h.
- ☐ Cost per user in observed range (< $4/mo).

### Risks / decisions to lock

- Apple review is unpredictable (3–14 days). Buffer accordingly.
- Play Store data safety form is easy to get wrong — draft in advance.
- Have a rollback plan for prod deploy (EAS Update to prior JS bundle).

---

## Phase 7 — Post-Launch Stabilization & Feedback Loop (First 30 Days)

**Goal:** Fix what real users find; keep costs sane; decide next-batch priorities.

**Estimated duration:** rolling for 30 days after launch.

### Deliverables

- Daily crash triage from Sentry.
- Weekly retention snapshot from PostHog (D1/D7/D30 tracking).
- Weekly cost review vs cohort growth.
- Bug hotfix cadence: any P0 within 24 h via EAS Update or Cloud Run redeploy.
- Feedback intake mechanism (in-app "Send feedback" → email).
- Prioritized backlog for v1.1 and v1.5 features.
- Decision point: enable full multimodal on all last-30-days IG imports (F8.4 evolution) — triggered by user count + cost headroom.

### Ongoing tasks

- Watch Sentry, Cloud Monitoring, PostHog daily.
- Extractor-reliability triage: hot-swap per-platform priority order if a platform's success rate drops below 80%.
- Respond to store reviews.
- Interview 10 real users in the first two weeks to validate PMF signals.
- Draft v1.1 scope by day 30.

### Exit criteria (30-day)

- ☐ 500+ signups (target; adjust per launch channel).
- ☐ D7 retention ≥ 20% among users who imported.
- ☐ Zero unresolved P0 bugs.
- ☐ No cost surprises > 20% of forecast.
- ☐ v1.1 scope agreed and buildphase.md updated.

---

## 2. Cross-Phase Concerns

### 2.1 Testing gate

Every phase must not degrade coverage. Target ≥ 80% on backend, ≥ 60% on mobile by Phase 5.

### 2.2 Documentation gate

Every phase updates the four `docs/` files if any assumption or contract changes. TRD is a living document — never let it drift.

### 2.3 Cost gate

Weekly cost review starting Phase 1. Hard cap on staging: $50/week. Hard cap on prod pre-launch: $200/week. Post-launch: variable, monitored per-user.

### 2.4 Store compliance gate

By end of Phase 3 we should have submitted a TestFlight external build for Apple review to validate the share-extension pattern before we depend on it.

---

## 3. Timeline Summary

Assuming solo founder + focused execution, weekend availability:

| Phase | Duration | Cumulative |
|-------|----------|------------|
| 0. Foundations | 1 week | 1 week |
| 1. Thin vertical slice | 2 weeks | 3 weeks |
| 2. Bulk import | 2 weeks | 5 weeks |
| 3. Native share + push | 2 weeks | 7 weeks |
| 4. Library + polish | 2 weeks | 9 weeks |
| 5. Hardening | 1.5 weeks | 10.5 weeks |
| 6. Launch (active work) | 1 week + review wait | ~12 weeks |
| 7. Post-launch | rolling 4 weeks | ~16 weeks |

Parallelizable with a second engineer: Phases 3 + 4 can run partially in parallel with Phase 2 tail.

---

## 4. Decision Log (to be updated as we build)

| Date | Decision | Rationale | Reversible? |
|------|----------|-----------|-------------|
| 2026-09-10 | Expo managed workflow | 2026 default; CNG covers native needs | Yes (can eject) |
| 2026-09-10 | Cognee Cloud v1, OSS adapter behind interface | Fastest to ship, easy migration | Yes (adapter stub built day 1) |
| 2026-09-10 | Gemini as unified multimodal | Simplest cost + latency profile | Yes (behind SummaryModel) |
| 2026-09-10 | GCP + Firebase Auth | Fits Gemini quotas + auth already solved | Yes but expensive to switch |
| 2026-09-18 | Swap Firebase Auth + Cloud SQL + GCS → Supabase (Auth + Postgres + Storage) | Single vendor for stateful layer; RLS gives free per-user isolation at DB layer; $25/mo flat vs $70+/mo Cloud SQL minimum saves ~$60/mo pre-scale; GCP kept for Cloud Run + Cloud Tasks + GKE (Gemini/Cognee latency, queue infra) | Reversible in principle but expensive — schema swap + auth-middleware rewrite. See TRD §9, §13.1, §15.3 for the new topology. |
| 2026-09-10 | Cobalt self-hosted primary extractor | Purpose-built for social, no rate-limit dependency | Yes (behind Extractor) |
| 2026-09-10 | No LinkedIn v1 | OSS extractors don't support; paid API deferred | v2 add-on |
| 2026-09-10 | Freemium unlimited at launch | Prioritize adoption over monetization pre-scale | Revisit at 1–5 lakh users |
| 2026-09-10 | Text-indexed default for > 30d IG imports | Cost containment while preserving searchability | Config toggle |
| 2026-09-10 | Auto full-multimodal only for ≤ 30d ambiguous items | Cost containment | Config toggle |

---

## 5. Open Items to Resolve Before Phase 0 Starts

- Apple Developer Program enrollment.
- Google Play Console developer account.
- Domain name (`spillthereel.app` or similar) purchased.
- GCP billing account with a budget alarm.
- **Supabase account + Pro-plan projects created (staging, prod), region locked (us-east-1 recommended), DB password stored in password manager.**
- Cognee Cloud beta account confirmed.
- Gemini API quota confirmed for expected volume.
- Bundle identifiers reserved.
- App name reserved on both stores.
