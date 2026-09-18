# SpillTheReel — Architecture Document

**Version:** 1.0
**Status:** Draft — pre-build
**Owner:** Aditya Jadhav
**Last updated:** 2026-09-10

Companion documents: [prd.md](./prd.md), [trd.md](./trd.md), [buildphase.md](./buildphase.md).

---

## 1. Purpose

Whereas the TRD documents *what* we use, this document documents *how it fits together*: the runtime topology, the data-flow paths for every user-facing scenario, the service boundaries, the directory layout of the monorepo, and the deployment topology. Read this to understand the system as a whole; read the TRD to understand the internals of any single component.

---

## 2. High-Level System Diagram

```
                                     ┌───────────────────────────┐
                                     │        End User            │
                                     │  (iPhone / Android device) │
                                     └────────────┬──────────────┘
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                          │                       │                       │
                          ▼                       ▼                       ▼
                 ┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
                 │  Main RN App    │    │  iOS Share       │    │  Android Share  │
                 │  (Expo managed) │    │  Extension       │    │  Intent         │
                 │                 │    │  (mini popup)    │    │  (silent toast) │
                 └────────┬────────┘    └────────┬─────────┘    └────────┬────────┘
                          │                       │                       │
                          │        HTTPS + Bearer <Supabase JWT>           │
                          └───────────────────────┬───────────────────────┘
                                                  │
                                                  ▼
                                     ┌──────────────────────────┐
                                     │  Cloud Load Balancer      │
                                     │  (HTTPS, HSTS)            │
                                     └────────────┬──────────────┘
                                                  │
                                                  ▼
                                     ┌──────────────────────────┐
                                     │  Cloud Run — API          │
                                     │  FastAPI + Pydantic v2    │
                                     └───┬──────────────────┬────┘
                                         │                  │
              ┌──────────────────────────┼──────────────────┼──────────────────────────┐
              │                          │                  │                          │
              ▼                          ▼                  ▼                          ▼
     ┌─────────────────┐        ┌────────────────────┐  ┌───────────────┐    ┌────────────────┐
     │ Supabase Auth   │        │ Supabase Postgres  │  │ Cloud Tasks   │    │ Cognee Cloud   │
     │ (JWT offline    │        │ (RLS-scoped,       │  │ 3 queues:     │    │ (memory / RAG) │
     │  verify — HS256)│        │  auth.uid()=user)  │  │ ingest, import│    │  per-user      │
     └─────────────────┘        └────────────────────┘  │ enhance       │    │  namespaces    │
                                                        └───────┬───────┘    └────────────────┘
                                                            │                          ▲
                                                            ▼                          │
                                                ┌───────────────────────┐              │
                                                │  Cloud Run — Workers  │──────────────┘
                                                │  (same codebase,      │
                                                │   worker entrypoint)  │
                                                └─┬───────┬───────┬─────┘
                                                  │       │       │
                             ┌────────────────────┘       │       └────────────────────┐
                             │                            │                            │
                             ▼                            ▼                            ▼
                    ┌────────────────┐          ┌──────────────────┐          ┌────────────────┐
                    │ Cobalt (GKE)   │          │ Gemini API       │          │ Groq API       │
                    │ Extractor      │          │ (multimodal)     │          │ (long ASR)     │
                    │ (self-hosted)  │          └──────────────────┘          └────────────────┘
                    └────────────────┘

                    ┌────────────────┐          ┌──────────────────┐
                    │ yt-dlp         │          │ gallery-dl       │
                    │ (in worker)    │          │ (in worker)      │
                    └────────────────┘          └──────────────────┘

                    ┌──────────────────────────────┐
                    │  Supabase Storage             │
                    │   'media' bucket (thumbnails, │
                    │   private, signed URLs +      │
                    │   Cloud CDN in front)         │
                    │   'exports' bucket (GDPR      │
                    │   dumps, 24h signed URL)      │
                    └──────────────────────────────┘

                    ┌──────────────────────────────┐
                    │  Push (Expo Notifications)    │
                    │  → APNs + FCM                 │
                    └──────────────────────────────┘

                    ┌──────────────────────────────┐
                    │  Observability:               │
                    │  Cloud Logging + Trace,       │
                    │  Sentry, PostHog              │
                    └──────────────────────────────┘
```

---

## 3. Service Boundaries

There are three deployable units:

1. **`api`** (Cloud Run) — synchronous HTTP layer. All client requests land here. Owns auth, validation, DB writes, task enqueue, and search orchestration. No blocking I/O beyond DB and Cognee search.

2. **`workers`** (Cloud Run) — asynchronous processing layer. Pulls Cloud Tasks and runs ingestion, imports, enhances. Owns extractor calls, ffmpeg, Gemini, Groq, Cognee writes, and push emission. Shares the exact codebase with `api` — only the entrypoint differs.

3. **`cobalt`** (GKE Autopilot) — self-hosted media extractor. Called by workers only. Stateless. Upstream image unmodified.

External managed services: Firebase Auth, Cloud SQL, Cloud Tasks, Cloud Storage, Cognee Cloud, Gemini API, Groq API, PostHog, Sentry, Expo push service.

---

## 4. Data Flow Walkthroughs

### 4.1 Scenario A — Share-sheet save from Instagram (Android)

```
User taps Share in Instagram
        │
        ▼
Android ShareIntentReceiver activity
  reads Firebase ID token from EncryptedSharedPreferences
        │
        ▼
POST https://api.spillthereel.app/v1/saves
  body: { url, source: 'share_extension' }
        │
        ▼
Cloud Load Balancer → Cloud Run api
        │
        ▼
FastAPI middleware verifies Firebase ID token
        │
        ▼
POST /v1/saves handler:
  1. Normalize URL, detect platform.
  2. INSERT INTO items (state='queued') — returns item_id.
     ON CONFLICT: return existing item_id + duplicateOf.
  3. Enqueue Cloud Task: ingest{item_id}.
  4. Respond 201 { itemId, state: 'queued', duplicateOf }.
        │
        ▼
Android receiver shows Toast "Saved to SpillTheReel ✓"
Android receiver dies. Instagram remains foregrounded.
        │
        ▼
Cloud Tasks fires task → Cloud Run worker
        │
        ▼
Worker.ingest(item_id):
  1. SELECT items.state → 'downloading', UPDATE.
  2. Extractor registry: try Cobalt → success or fallback to yt-dlp.
  3. ffmpeg: extract audio + sample frames.
  4. If duration < 5 min: Gemini multimodal call with frames + audio.
     If ≥ 5 min: Groq Whisper transcribe → Gemini with frames + transcript.
  5. Parse StructuredSummary, validate.
  6. Upload thumbnail to GCS.
  7. Cognee.write(namespace=user_id, item=indexed).
  8. UPDATE items state='fully_indexed', fill summary/transcript/etc.
  9. INSERT ingestion_events row.
 10. Emit SSE event on user's channel.
 11. Send Expo push notification if app in background.
```

### 4.2 Scenario B — Bulk import from Instagram data export

```
User uploads export.zip via native file picker
        │
        ▼
POST /v1/import/instagram (multipart/form-data)
        │
        ▼
FastAPI api:
  1. Auth check.
  2. Stream ZIP to per-request tmpfs directory.
  3. INSERT imports (state='parsing') → import_id.
  4. Enqueue Cloud Task: import{import_id, zip_path}.
  5. Respond 202 { importId }.
        │
        ▼
Client polls GET /v1/import/{importId} for progress OR subscribes to SSE.
        │
        ▼
Cloud Tasks fires → Worker.process_import(import_id, zip_path):
  1. Unzip to per-worker tmpfs.
  2. Locate saved/saved_posts.html, saved_collections.html, saved_music.html.
  3. Compute parser_schema_hash from labels seen; UPDATE imports.
  4. For each parsed entry:
     - decide_tier(entry) → TEXT_INDEXED or FULL_MULTIMODAL
     - INSERT items ON CONFLICT do update (dedup)
     - if TEXT_INDEXED: build text_content, batch write to Cognee (batch size 50)
     - if FULL_MULTIMODAL: enqueue Cloud Task ingest{item_id}, throttled queue
     - update imports counters every 100 entries
  5. For each collection:
     - INSERT collections + collection_items
  6. For each music entry:
     - INSERT saved_audio
  7. UPDATE imports state='complete', finished_at=now.
  8. Emit SSE + push "Import finished — N reels ready to search".
  9. Delete tmpfs ZIP.
```

Throttling for FULL_MULTIMODAL enqueues is enforced by the `enhance` queue's Cloud Tasks rate limit (20 tasks/hour per user, keyed via task name prefix `enhance-{user_id}-`).

### 4.3 Scenario C — Natural-language search

```
User types "red car reel" in search screen
        │
        ▼
POST /v1/search
  { query, filters, topK: 8 }
        │
        ▼
api handler:
  1. Auth check.
  2. Optionally rewrite query with tiny Gemini call for spelling/intent (Flash-Lite).
     (v1 skip — go direct to memory.)
  3. Cognee.search(namespace=user_id, query, filters, top_k) → list[MemoryHit].
  4. SELECT items WHERE id IN (...) AND user_id=... (hydrate).
  5. Build RAG prompt: query + hits' (title, summary, transcript, hashtags).
  6. Gemini.summarize_answer(prompt) → answer + citations[].
  7. Respond { answer, citations, items, durationMs }.
        │
        ▼
Client renders answer with [1][2] superscripts linking to item cards.
```

### 4.4 Scenario D — Selective enhance

```
User taps "Enhance with video analysis" on a text_indexed item
        │
        ▼
POST /v1/items/{item_id}/enhance
        │
        ▼
api:
  1. Auth check + ownership check.
  2. Guard: state must be 'text_indexed' or 'source_gone' retry.
  3. UPDATE items state='queued'.
  4. Enqueue Cloud Task ingest{item_id} on 'enhance' queue.
  5. Respond 200 { state: 'queued' }.
        │
        ▼
Worker runs standard ingestion (§4.1 steps 1–11).
```

### 4.5 Scenario E — Account deletion

```
User taps Delete Account
        │
        ▼
DELETE /v1/me
        │
        ▼
api:
  1. Mark users.deleted_at = now (soft flag; blocks further logins).
  2. Enqueue Cloud Task delete_user{user_id}.
  3. Respond 202.
        │
        ▼
Worker.delete_user:
  1. Cognee.delete_namespace(user_id).
  2. DELETE FROM items WHERE user_id=... (cascades to collection_items, ingestion_events).
  3. DELETE FROM collections, saved_audio, imports, push_tokens.
  4. Enumerate GCS prefix media/{user_id}/ and delete.
  5. firebase_admin.auth.delete_user(firebase_uid).
  6. DELETE FROM users.
  7. Emit final analytics event.
```

---

## 5. Monorepo File Structure

```
spillthereel/
├── apps/
│   ├── mobile/                              # React Native + Expo app
│   │   ├── app/                             # expo-router routes
│   │   │   ├── (tabs)/
│   │   │   │   ├── _layout.tsx              # Tab bar layout
│   │   │   │   ├── index.tsx                # Home (feed + rails + collections)
│   │   │   │   ├── search.tsx               # Search tab
│   │   │   │   ├── library.tsx              # Library browse
│   │   │   │   └── settings.tsx             # Settings
│   │   │   ├── item/[id].tsx                # Item detail sheet
│   │   │   ├── collection/[id].tsx          # Collection view
│   │   │   ├── import/                      # IG bulk import flow
│   │   │   │   ├── intro.tsx
│   │   │   │   ├── guide.tsx                # Step-by-step walkthrough
│   │   │   │   ├── upload.tsx
│   │   │   │   └── progress.tsx
│   │   │   ├── auth/
│   │   │   │   ├── sign-in.tsx
│   │   │   │   └── sign-out.tsx
│   │   │   ├── onboarding/
│   │   │   │   └── share-permission.tsx     # Guide user to try share sheet
│   │   │   └── _layout.tsx                  # Root layout, auth gate
│   │   ├── components/
│   │   │   ├── ItemCard.tsx
│   │   │   ├── ItemStateBadge.tsx
│   │   │   ├── SearchBar.tsx
│   │   │   ├── FilterChips.tsx
│   │   │   ├── AnswerBlock.tsx
│   │   │   ├── CollectionRail.tsx
│   │   │   ├── EnhanceButton.tsx
│   │   │   └── ProgressBar.tsx
│   │   ├── hooks/
│   │   │   ├── useAuth.ts
│   │   │   ├── useItems.ts
│   │   │   ├── useSearch.ts
│   │   │   ├── useImport.ts
│   │   │   ├── useCollections.ts
│   │   │   ├── useShareIntent.ts
│   │   │   └── useSSE.ts                    # Item-state stream
│   │   ├── lib/
│   │   │   ├── api.ts                       # fetch wrapper with token attach
│   │   │   ├── firebase.ts                  # Firebase init
│   │   │   ├── secure-store.ts              # Wrapper over expo-secure-store
│   │   │   ├── analytics.ts                 # PostHog helpers
│   │   │   └── deep-links.ts                # Platform-specific "open source"
│   │   ├── stores/                          # Zustand slices
│   │   │   ├── ui-store.ts
│   │   │   └── filters-store.ts
│   │   ├── share-extension-ios/             # expo-share-extension custom view
│   │   │   ├── ShareViewController.swift    # generated from config plugin
│   │   │   └── index.tsx                    # React view for iOS extension
│   │   ├── share-intent-android/            # expo-share-intent config
│   │   │   └── receiver.ts                  # background service
│   │   ├── assets/
│   │   ├── app.json                         # Expo config
│   │   ├── eas.json                         # EAS profiles
│   │   ├── tsconfig.json
│   │   ├── package.json
│   │   └── README.md
│   │
│   ├── api/                                 # FastAPI backend
│   │   ├── app/
│   │   │   ├── main.py                      # ASGI app factory
│   │   │   ├── settings.py                  # Pydantic settings
│   │   │   ├── auth/
│   │   │   │   ├── firebase.py              # Verify ID token
│   │   │   │   └── middleware.py
│   │   │   ├── api/
│   │   │   │   ├── v1/
│   │   │   │   │   ├── saves.py             # POST /v1/saves
│   │   │   │   │   ├── items.py             # GET/PATCH/DELETE items
│   │   │   │   │   ├── collections.py
│   │   │   │   │   ├── imports.py           # POST /v1/import/instagram
│   │   │   │   │   ├── search.py            # POST /v1/search
│   │   │   │   │   ├── notifications.py     # POST /v1/notifications/token
│   │   │   │   │   ├── users.py             # /v1/me
│   │   │   │   │   └── events.py            # SSE
│   │   │   │   └── health.py
│   │   │   ├── db/
│   │   │   │   ├── engine.py                # SQLAlchemy async engine
│   │   │   │   ├── models.py                # SQLAlchemy models
│   │   │   │   └── migrations/              # Alembic
│   │   │   ├── services/
│   │   │   │   ├── extractors/
│   │   │   │   │   ├── base.py              # Extractor Protocol
│   │   │   │   │   ├── cobalt.py
│   │   │   │   │   ├── ytdlp.py
│   │   │   │   │   ├── gallerydl.py
│   │   │   │   │   └── registry.py
│   │   │   │   ├── memory/
│   │   │   │   │   ├── base.py              # MemoryStore Protocol
│   │   │   │   │   ├── cognee_cloud.py
│   │   │   │   │   └── cognee_oss.py        # Escape-hatch stub
│   │   │   │   ├── llm/
│   │   │   │   │   ├── base.py              # SummaryModel/TranscriptionModel Protocols
│   │   │   │   │   ├── gemini.py
│   │   │   │   │   └── groq_whisper.py
│   │   │   │   ├── media/
│   │   │   │   │   ├── ffmpeg.py            # Frame sampling, audio extract, thumbnails
│   │   │   │   │   └── gcs.py               # Upload helper
│   │   │   │   ├── ingest/
│   │   │   │   │   ├── pipeline.py          # ingest_item(item_id) orchestrator
│   │   │   │   │   ├── platform_detect.py
│   │   │   │   │   └── taxonomy.py          # Canonical categories + heuristic
│   │   │   │   ├── imports/
│   │   │   │   │   ├── ig_parser.py         # BeautifulSoup parser (F8.3)
│   │   │   │   │   ├── ig_pipeline.py       # process_import(import_id)
│   │   │   │   │   └── schema_watch.py      # parser_schema_hash calc
│   │   │   │   ├── search/
│   │   │   │   │   ├── retriever.py         # Calls MemoryStore
│   │   │   │   │   └── answerer.py          # RAG prompt to Gemini
│   │   │   │   ├── notifications/
│   │   │   │   │   ├── expo_push.py
│   │   │   │   │   └── sse.py
│   │   │   │   └── users/
│   │   │   │       └── delete.py            # cascade
│   │   │   ├── tasks/
│   │   │   │   ├── enqueue.py               # Cloud Tasks helpers
│   │   │   │   └── worker_entry.py          # Worker HTTP endpoints
│   │   │   ├── observability/
│   │   │   │   ├── logging.py               # structlog config
│   │   │   │   ├── tracing.py               # OTel setup
│   │   │   │   ├── metrics.py               # Cloud Monitoring
│   │   │   │   └── sentry.py
│   │   │   └── worker.py                    # ASGI app factory for workers
│   │   ├── tests/
│   │   │   ├── unit/
│   │   │   ├── integration/
│   │   │   └── parser-corpus/               # IG export fixtures
│   │   ├── pyproject.toml
│   │   ├── Dockerfile
│   │   └── README.md
│   │
│   └── workers/                             # Container entrypoint = api/app/worker.py
│       ├── Dockerfile
│       └── README.md
│
├── packages/
│   └── shared-types/                        # TS + Python generated from single source
│       ├── src/                             # OpenAPI or JSON Schema definitions
│       ├── typescript/                      # Generated .ts
│       ├── python/                          # Generated .py (pydantic)
│       ├── generate.sh
│       └── package.json
│
├── infra/
│   ├── terraform/
│   │   ├── modules/
│   │   │   ├── cloud_run_service/
│   │   │   ├── cloud_tasks/
│   │   │   ├── cloud_sql/
│   │   │   ├── gcs/
│   │   │   └── gke_cobalt/
│   │   ├── environments/
│   │   │   ├── staging/
│   │   │   └── prod/
│   │   └── main.tf
│   └── cobalt/
│       ├── deployment.yaml
│       ├── service.yaml
│       └── hpa.yaml
│
├── docs/
│   ├── prd.md
│   ├── trd.md
│   ├── architecture.md
│   └── buildphase.md
│
├── scripts/
│   ├── local-dev.sh                         # docker-compose up for dev
│   ├── seed-demo-data.py
│   └── smoke-test.sh
│
├── .github/
│   └── workflows/
│       ├── mobile-ci.yml
│       ├── mobile-release.yml
│       ├── api-ci.yml
│       ├── api-deploy-staging.yml
│       ├── api-deploy-prod.yml
│       └── infra.yml
│
├── .env.example
├── README.md
└── LICENSE
```

---

## 6. Ingestion Pipeline — Component Diagram

```
                  ┌─────────────────────┐
                  │ ingest_item(item_id) │  ← called by worker HTTP endpoint
                  └──────────┬───────────┘
                             │
             ┌───────────────┼───────────────┐
             ▼               ▼               ▼
    ┌────────────────┐  ┌──────────┐  ┌──────────────┐
    │ platform_detect │  │ registry │  │ pipeline     │
    │ (regex)         │  │ (order)  │  │ orchestrator │
    └────────┬────────┘  └────┬─────┘  └──────┬───────┘
             │                │               │
             ▼                ▼               ▼
         Platform      List[Extractor]  Orchestrated call
                                        chain
                                              │
                                              ▼
                                    ┌───────────────────────┐
                                    │ Extractor.extract()   │
                                    │  → ExtractResult      │
                                    └──────────┬────────────┘
                                               │
                                               ▼
                                    ┌───────────────────────┐
                                    │ ffmpeg.sample_frames  │
                                    │ ffmpeg.extract_audio  │
                                    │ ffmpeg.thumbnail      │
                                    └──────────┬────────────┘
                                               │
                                               ▼
                                    ┌───────────────────────┐
                                    │ Long-content branch?  │
                                    │  yes → Groq.transcribe │
                                    │  no  → skip           │
                                    └──────────┬────────────┘
                                               │
                                               ▼
                                    ┌───────────────────────┐
                                    │ Gemini.summarize      │
                                    │  → StructuredSummary  │
                                    └──────────┬────────────┘
                                               │
                                               ▼
                                    ┌───────────────────────┐
                                    │ gcs.upload_thumbnail  │
                                    └──────────┬────────────┘
                                               │
                                               ▼
                                    ┌───────────────────────┐
                                    │ MemoryStore.write     │
                                    │  (Cognee Cloud)       │
                                    └──────────┬────────────┘
                                               │
                                               ▼
                                    ┌───────────────────────┐
                                    │ Postgres UPDATE items │
                                    │ INSERT ingestion_events│
                                    └──────────┬────────────┘
                                               │
                                               ▼
                                    ┌───────────────────────┐
                                    │ notifications.emit    │
                                    │  (SSE + push)         │
                                    └───────────────────────┘
```

Any step can fail; failure is captured in `items.failure_reason` and drives the state to `failed` after 3 retries with exponential backoff at the Cloud Tasks layer.

---

## 7. Instagram Import Pipeline — Component Diagram

```
       ┌─────────────────────────┐
       │ POST /v1/import/instagram │
       └───────────┬──────────────┘
                   │
                   ▼
       ┌───────────────────────────┐
       │ Stream upload → tmpfs zip │
       │ INSERT imports (parsing)  │
       │ Enqueue Cloud Task        │
       └───────────┬───────────────┘
                   │
                   ▼
       ┌───────────────────────────┐
       │ Worker.process_import      │
       └───────────┬───────────────┘
                   │
                   ▼
       ┌───────────────────────────┐
       │ ig_parser.parse_zip        │
       │   yields entries + colls   │
       └───────────┬───────────────┘
                   │
                   ▼
   ┌────────────────────────────────────────────────────┐
   │ For each entry:                                     │
   │  1. taxonomy.decide_tier(entry)                     │
   │  2. Postgres UPSERT items (dedup on url_norm)       │
   │  3. If TEXT_INDEXED:                                │
   │       batch buffer → Cognee.batch_write             │
   │  4. If FULL_MULTIMODAL:                             │
   │       Enqueue Cloud Task ingest{item_id}            │
   │       (rate-limited queue: 20/hr per user)          │
   │  5. Update imports counters every 100               │
   └───────────┬────────────────────────────────────────┘
               │
               ▼
   ┌───────────────────────────┐
   │ For each collection:       │
   │   INSERT collections       │
   │   INSERT collection_items  │
   └───────────┬───────────────┘
               │
               ▼
   ┌───────────────────────────┐
   │ For each music entry:      │
   │   INSERT saved_audio       │
   └───────────┬───────────────┘
               │
               ▼
   ┌───────────────────────────┐
   │ UPDATE imports (complete)  │
   │ Emit SSE + push            │
   │ Delete tmpfs               │
   └───────────────────────────┘
```

---

## 8. Search & RAG Flow

```
      Client (search screen)
             │
             ▼
      POST /v1/search
             │
             ▼
      Auth middleware
             │
             ▼
      services.search.retriever:
        MemoryStore.search(user_id, query, filters, top_k=8)
             │
             ▼
      List[MemoryHit] (item_ids + scores + chunks)
             │
             ▼
      Postgres SELECT items IN (ids) → hydrate
             │
             ▼
      services.search.answerer:
        Build RAG prompt with hydrated items
        Gemini.generate(prompt) → answer + citations
             │
             ▼
      Response: { answer, citations, items }
             │
             ▼
      Client renders answer with clickable citations
```

**Retrieval + generation cost:** ~$0.001–$0.005 per query at Flash-Lite.

**Latency budget (P95 2.5s):**
- Cognee search: 400 ms
- Postgres hydrate: 60 ms
- Gemini answer: 1.6 s
- Serialization + network: 400 ms

---

## 9. Deployment Topology

### 9.1 Environments

| Environment | Region | Purpose |
|-------------|--------|---------|
| dev (local) | laptop | docker-compose Postgres + local FastAPI, Cognee Cloud dev namespace, real Gemini keys behind a $10/mo cap |
| staging | us-central1 | Full mirror of prod at ~10% capacity, separate Firebase project, separate Cognee namespace prefix |
| prod | us-central1 primary | Live app |

### 9.2 Prod network diagram

```
                Internet
                    │
                    ▼
    ┌──────────────────────────────┐
    │ Cloud Load Balancer (HTTPS)  │
    │ + Cloud Armor (rate limits)  │
    └──────────────┬───────────────┘
                   │
                   ▼
    ┌──────────────────────────────┐
    │ Cloud Run — api (public)     │◄──── outbound: Supabase (Auth + Postgres + Storage),
    │ 1 vCPU / 512MiB / min 1 max 20│                Cognee, Gemini
    └──────────────┬───────────────┘
                   │ HTTPS to Supabase pooler:6543
                   ▼
    ┌──────────────────────────────┐
    │ Supabase (managed, external) │
    │  * Postgres (RLS enforced)   │
    │  * Auth (JWT source)         │
    │  * Storage (media + exports) │
    └──────────────────────────────┘

    ┌──────────────────────────────┐
    │ Cloud Tasks (managed)        │
    └──────────────┬───────────────┘
                   ▼ push HTTPS
    ┌──────────────────────────────┐
    │ Cloud Run — worker (internal)│──── outbound: Cobalt, Gemini, Groq, Cognee,
    │ 2 vCPU / 2 GiB / min 0 max 30│                Supabase (via service-role key)
    └──────────────────────────────┘

    ┌──────────────────────────────┐
    │ GKE Autopilot — Cobalt        │◄──── inbound from workers only (internal LB)
    └──────────────────────────────┘

    ┌──────────────────────────────┐
    │ Cloud CDN                     │
    │ in front of Supabase Storage  │
    │ signed URLs (thumbnails)      │
    └──────────────────────────────┘
```

### 9.3 Failover

- Cloud Run is regional but auto-recovers on zone failures.
- Cloud SQL HA replica in a secondary zone; automatic failover.
- Cobalt cluster runs across ≥ 2 zones via GKE Autopilot.
- Disaster recovery region (`us-east1`) is a documented runbook only — no live capacity in v1 (revisit at 10k+ DAUs).

---

## 10. Auth & Session Topology

```
Client                                    Supabase Auth         Backend
  │                                             │                 │
  │ Google/Apple/Email flow                     │                 │
  │─────────────────────────────────────────────►│                 │
  │                                             │                 │
  │◄──── Access JWT (HS256, 1hr) + Refresh Token│                 │
  │                                             │                 │
  │ POST /v1/saves                              │                 │
  │  Authorization: Bearer <access JWT>         │                 │
  │─────────────────────────────────────────────┼────────────────►│
  │                                             │  jwt.decode()   │
  │                                             │  (offline,      │
  │                                             │   SUPABASE_JWT  │
  │                                             │   _SECRET, ~1µs)│
  │◄──────────────── 201 Created ───────────────┼─────────────────│
```

- Access JWT auto-rotated every ~1h by the Supabase JS SDK on the client — transparent to the app.
- Backend never sees the refresh token; verification is fully offline (no per-request round-trip to Supabase).
- Share extension reads the currently valid access token from the shared App Group / EncryptedSharedPreferences; if expired, extension calls Supabase's token refresh endpoint directly (~200ms) using the stored refresh token before POSTing to `/v1/saves`.
- Backend inside a request handler `SET`s `request.jwt.claim.sub = <user_id>` on the Postgres session so that Supabase RLS policies fire naturally on every subsequent query in that transaction.

---

## 11. Storage Lifecycle

| Data | Store | Retention |
|------|-------|-----------|
| User account (auth) | Supabase `auth.users` | Indefinite, until account delete (cascades to all our tables) |
| User profile fields | Supabase `profiles` | Same |
| Item metadata | Supabase `items` | Indefinite, until item/account delete |
| Item summary / transcript / caption | Supabase `items` (long text cols) | Same |
| Ingestion audit trail | Supabase `ingestion_events` | 90 days (rolling delete job) |
| Cognee memory | Cognee Cloud | Same as item lifetime |
| Thumbnails | Supabase Storage `media/thumbs/{user_id}/{item_id}.jpg` | Indefinite until delete |
| Source video (worker tmp) | Worker container tmpfs | Deleted within 15 min of processing |
| IG export ZIP | Worker tmpfs | Deleted after parse or 15 min |
| GDPR data export dump | Supabase Storage `exports/{user_id}/{timestamp}.json` | 24h then deleted |
| Push tokens | Supabase `push_tokens` | Until user signs out |
| Analytics | PostHog Cloud | 12 months rolling |
| Sentry errors | Sentry | 90 days |
| Logs / traces | Cloud Logging / Trace | 30 days |
| Postgres backups | Supabase automated | 7 days PITR (Pro tier) |

---

## 12. Configuration Model

- All runtime config via env vars, injected from Secret Manager at Cloud Run boot.
- `apps/api/app/settings.py` — Pydantic `BaseSettings` class defines every var with type + default + description. Missing required vars fail-fast at boot.
- Non-secret toggles live in a `feature_flags.yaml` served by PostHog; app reads via PostHog SDK.
- Taxonomy + parser thresholds in `taxonomy_config.yaml`, deployed with the container.

---

## 13. Local Development

```
scripts/local-dev.sh
  ↓
docker-compose up:
  - postgres:16 (with initial schema)
  - api (FastAPI, hot-reload)
  - worker (FastAPI worker mode)
  - localstack (for GCS emulation) — optional
  - cobalt (upstream image)
  ↓
Mobile:
  npx expo start
  Uses staging Firebase project so real device auth works.
```

Cognee Cloud is accessed with a dev-tier API key. Gemini/Groq use rate-capped dev keys.

---

## 14. Extensibility Points

Places designed for future feature expansion without re-architecting:

1. **New source platforms** — add `Extractor` implementation + regex to `platform_detect`.
2. **New importers** (TikTok, YouTube Takeout) — replicate the IG import flow: parser + pipeline + `imports` schema reuse.
3. **New memory backends** — implement `MemoryStore`; flip a feature flag.
4. **New LLM providers** — implement `SummaryModel` / `TranscriptionModel`.
5. **Payment gating** — the `users` table has room for a `tier` column; `services.billing/` package sketched for v1.5.
6. **Web app** (v2) — the backend is client-agnostic; a Next.js app can consume the same `/v1/` API.

---

## 15. Assumptions Encoded in Architecture

- Single-region v1 is sufficient (< 100 ms P50 latency to users on non-US ISPs is acceptable).
- Cloud Run cold starts (~1 s) are tolerable since first request goes through Cloud LB.
- Cognee Cloud is available (>= 99.5% SLO).
- Instagram, TikTok, YouTube, X don't drop unauthenticated public URL access wholesale.
- GKE Autopilot for Cobalt is cheaper than dedicating Cloud Run RAM to it at expected scale (revisit if Cobalt QPS < 10).

---

## 16. Diagrams to Produce During Design Phase

- Mermaid state diagram of `items.state` transitions.
- Sequence diagram: share extension ↔ API ↔ worker for scenario A.
- Sequence diagram: IG import full pipeline.
- ER diagram of Postgres schema (auto-generated from Alembic).
- Component diagram of the `services/` directory.

These will be added to `docs/design/` in the design phase (buildphase.md Phase 1).
