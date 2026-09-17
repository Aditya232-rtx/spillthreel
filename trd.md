# SpillTheReel — Technical Requirements Document (TRD)

**Version:** 1.0
**Status:** Draft — pre-build
**Owner:** Aditya Jadhav
**Last updated:** 2026-09-10

Companion documents: [prd.md](./prd.md), [architecture.md](./architecture.md), [buildphase.md](./buildphase.md).

---

## 1. Purpose

This document translates the product requirements into concrete engineering decisions: languages, frameworks, third-party services, data models, API contracts, interface boundaries, security, cost model, and observability. It is the source of truth for *how* we build SpillTheReel.

Any technical decision not documented here should default to the simplest option consistent with the principles in Section 2.

---

## 2. Engineering Principles

1. **Interface-first for volatile dependencies.** Any component we might swap (extractor, memory layer, LLM provider, queue) sits behind a stable internal interface.
2. **Immutability by default.** No in-place mutation of records; every state change produces a new row / new event.
3. **Async everywhere the user waits.** Anything > 250ms is a background job with client-visible state.
4. **Cost-aware by construction.** Every pipeline call has a documented cost estimate and a hard budget guard.
5. **Fewer moving parts.** Prefer one well-understood dependency over three trendy ones.
6. **Schema tolerance at boundaries.** External data (IG export, third-party APIs) is parsed with best-effort field extraction and unknown fields logged, never fatal.
7. **Observability from day one.** Structured logs, traces, and per-user cost accounting from the first deploy.

---

## 3. Tech Stack Summary

| Layer | Technology | Rationale |
|-------|------------|-----------|
| Mobile app | React Native + Expo SDK 55+ (managed workflow), TypeScript, `expo-router` | Cross-platform, EAS Build handles both stores, CNG covers all native needs |
| Share-sheet — iOS | `expo-share-extension` (custom mini-view) | Only path to a Pinterest-style in-share-sheet toast on iOS |
| Share-sheet — Android | `expo-share-intent` + native Toast | Simplest cross-platform hook, native Toast for silent save UX |
| State mgmt | React Query (TanStack) + Zustand | RQ for server state, Zustand for UI-only state |
| Auth | Firebase Auth (Google, Apple, Email) | Solved auth; Expo has first-class support |
| Push | Expo Notifications (APNs + FCM under the hood) | One integration for both platforms |
| Backend API | Python 3.12, FastAPI, `pydantic v2`, `uvicorn` | Async-first, MVP proven, best-in-class for LLM/RAG orchestration |
| Queue | Google Cloud Tasks | Managed, no ops, scales to zero, integrates with Cloud Run |
| Workers | Same FastAPI codebase running in `worker` mode, deployed as a separate Cloud Run service | Same codebase, same models, different entry point |
| Metadata store | Cloud SQL Postgres 16 | Relational, transactional, supports pgvector as fallback vector backend |
| Object storage | Google Cloud Storage | Thumbnails, transient media, IG export tmpfs handoff |
| Memory / RAG | Cognee Cloud (Developer tier) behind our `MemoryStore` interface | Fastest to ship; OSS adapter as escape hatch |
| Multimodal LLM | Gemini 2.5 Flash-Lite / Flash / Pro (migrating to Gemini 3.x pre-Oct 2026) | One provider for video + audio + image + summary |
| ASR fallback (long audio) | Groq Whisper-v3-turbo | Cheapest ASR for content ≥ 5 min |
| Extractor — primary | Cobalt (self-hosted on GKE Autopilot) | Purpose-built for social; AGPL-3.0 (compliance note in §12) |
| Extractor — fallback | yt-dlp (Python subprocess in worker) | Widest coverage, edge cases |
| Extractor — images | gallery-dl (Python subprocess in worker) | IG carousels / X image threads |
| Media processing | ffmpeg (system binary in worker container) | Thumbnails, frame sampling, audio extraction |
| IG export parser | BeautifulSoup4 + lxml | HTML export from Meta |
| Deployment | GCP: Cloud Run (API + workers), Cloud Tasks, Cloud SQL, GCS, GKE Autopilot (Cobalt), Firebase Auth | Native fit for Firebase Auth + Gemini quotas |
| CI/CD | GitHub Actions → EAS Build/Submit + gcloud deploy | Standard, cheap |
| Observability | Cloud Logging + Cloud Trace + Sentry (mobile + backend) | Managed + best-in-class error monitoring |
| Analytics | PostHog (self-host later, cloud v1) | Product analytics + feature flags |
| Feature flags | PostHog flags | One tool |

---

## 4. Monorepo Structure

Single Git repository, `spillthereel/`, containing:

```
spillthereel/
  apps/
    mobile/            # Expo React Native app
    api/               # FastAPI backend
    workers/           # Same codebase as api, worker entrypoint (or Cloud Run job)
  packages/
    shared-types/      # TypeScript + Python (pydantic) shared model definitions via a schema generator
  infra/
    terraform/         # GCP resources
    cobalt/            # Cobalt self-hosted manifests (GKE Autopilot)
  docs/
    prd.md
    trd.md
    architecture.md
    buildphase.md
  .github/workflows/   # CI
```

Rationale: mobile + backend evolve together; single-repo minimizes coordination overhead. See [architecture.md](./architecture.md) §5 for the full tree.

---

## 5. Mobile Client — Detailed Spec

### 5.1 Framework choices

- **Expo SDK 55+ managed workflow with Continuous Native Generation (CNG).** All native modules added via config plugins — no manual Xcode/Gradle edits.
- **TypeScript strict mode.** No `any`. `strict: true`, `noImplicitAny: true`, `strictNullChecks: true`.
- **`expo-router`** for file-based routing. Native-tab layout for Home / Search / Library / Settings.
- **`react-native-reanimated` v3** for shared-element transitions on item detail sheets.

### 5.2 Auth flow

- Firebase JS SDK on the client.
- Sign-in via `@react-native-firebase/auth` config plugin (or `firebase/auth/react-native` for Expo).
- ID token attached to every backend request as `Authorization: Bearer <token>`. Token refresh handled by Firebase SDK; backend verifies token cryptographically via Firebase Admin SDK on every request.
- Refresh token persisted in `expo-secure-store` (Keychain / Keystore).
- Shared with iOS Share Extension via App Group (`group.com.spillthereel.shared`) and with Android share intent via Encrypted SharedPreferences.

### 5.3 Share extension — iOS

- Config plugin `expo-share-extension` with a custom view.
- Bundle ID: `com.spillthereel.app.ShareExtension`.
- Sees the shared URL, reads Firebase ID token from App Group storage, POSTs to `/v1/saves`, updates UI to "Saved ✓", auto-dismisses in 1.2 s.
- Bundle target size < 5 MB (App Store review consideration).
- Extension does not use React Navigation or heavy libraries — hand-tuned minimal view.

### 5.4 Share intent — Android

- Config plugin `expo-share-intent` registering an activity for `android.intent.action.SEND` with `text/plain` MIME type.
- Intent receiver: a lightweight foreground `Service` posts to backend and immediately shows a native `Toast` (`Toast.makeText(context, "Saved to SpillTheReel ✓", Toast.LENGTH_SHORT).show()`).
- Service auto-terminates after HTTP response.
- Firebase ID token read from EncryptedSharedPreferences.

### 5.5 Offline queue

- Saves that fail due to network are persisted to `expo-sqlite` in the shared container.
- Background task (`expo-background-fetch`) retries on next network availability.

### 5.6 State management

- Server state: **TanStack Query v5**. `useQuery` for reads, `useMutation` for writes, optimistic updates for share/enhance/delete.
- Local UI state: **Zustand** slices per screen.
- No Redux.

### 5.7 Client-side data model (TypeScript)

```ts
type ItemState =
  | 'queued' | 'downloading' | 'analyzing' | 'indexing'
  | 'text_indexed' | 'fully_indexed' | 'source_gone' | 'failed';

type Platform = 'instagram' | 'tiktok' | 'youtube' | 'x';

interface Item {
  id: string;                     // ULID
  userId: string;
  sourceUrl: string;
  platform: Platform;
  state: ItemState;
  createdAt: string;              // ISO
  savedAt: string;                // when user saved on source platform (may equal createdAt)
  title: string | null;
  summary: string | null;
  transcript: string | null;
  onScreenText: string | null;
  caption: string | null;         // from IG export
  hashtags: string[];             // from IG export or Gemini
  owner: { name: string; username: string; url: string } | null;
  category: string | null;        // canonical taxonomy value
  userCategory: string | null;    // user override
  collectionIds: string[];        // memberships
  thumbnailUrl: string | null;
  durationSeconds: number | null;
  failureReason: string | null;
}

interface Collection {
  id: string;                     // ULID
  userId: string;
  name: string;
  privacy: 'private' | 'public';
  source: 'instagram_import' | 'user_created';
  createdAt: string;
  itemIds: string[];
}
```

---

## 6. Backend API — Detailed Spec

### 6.1 Framework

- **FastAPI + Pydantic v2** on Python 3.12.
- ASGI server: `uvicorn` with `uvloop` in production.
- All request models are Pydantic; automatic OpenAPI schema at `/openapi.json`.

### 6.2 Auth middleware

Every route below `/v1/` requires `Authorization: Bearer <Firebase ID token>`. Middleware:
1. Extracts token.
2. Verifies via `firebase_admin.auth.verify_id_token()` (cached certificates, ~1ms per verify after warmup).
3. Injects `current_user: User` into request state.
4. Rejects with 401 on invalid.

### 6.3 REST endpoints (v1)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/saves` | Create a new save from a URL (from share extension) |
| `GET` | `/v1/items` | List items with filters (platform, category, collection, state, cursor) |
| `GET` | `/v1/items/{id}` | Item detail |
| `POST` | `/v1/items/{id}/enhance` | Upgrade a `text_indexed` item to full multimodal |
| `POST` | `/v1/items/{id}/retry` | Retry a `failed` item |
| `DELETE` | `/v1/items/{id}` | Delete an item |
| `PATCH` | `/v1/items/{id}` | Update user overrides (userCategory, collection membership) |
| `GET` | `/v1/collections` | List collections |
| `POST` | `/v1/collections` | Create a user collection |
| `PATCH` | `/v1/collections/{id}` | Rename or update collection |
| `DELETE` | `/v1/collections/{id}` | Delete collection (items remain) |
| `POST` | `/v1/import/instagram` | Upload IG export ZIP (multipart) — kicks off F8 pipeline |
| `GET` | `/v1/import/{importId}` | Get import status + progress |
| `POST` | `/v1/search` | Search query → answer + result cards |
| `POST` | `/v1/search/similar/{itemId}` | "Find more like this" |
| `GET` | `/v1/me` | Current user profile |
| `DELETE` | `/v1/me` | Account deletion (async cascade) |
| `POST` | `/v1/notifications/token` | Register Expo push token |
| `GET` | `/v1/events` | SSE stream of item-state transitions (per user) |

### 6.4 Search endpoint contract

Request:
```json
POST /v1/search
{
  "query": "the red car reel",
  "filters": {
    "platforms": ["instagram", "tiktok"],
    "categories": ["Products"],
    "collectionIds": null,
    "onlyFullyIndexed": false,
    "since": "2026-01-01T00:00:00Z"
  },
  "topK": 8
}
```

Response:
```json
{
  "answer": "You saved a Mustang restoration reel from @vintagecars on Aug 12 [1] and a red Ferrari showroom clip on Sep 2 [2].",
  "citations": [
    {"index": 1, "itemId": "01H..."},
    {"index": 2, "itemId": "01H..."}
  ],
  "items": [
    {"id": "01H...", "score": 0.91, ...},
    ...
  ],
  "queryEmbeddingId": "qemb_...",
  "durationMs": 1740
}
```

### 6.5 Save endpoint contract

Request:
```json
POST /v1/saves
{ "url": "https://www.instagram.com/reel/DbM7eWINqAy/", "source": "share_extension" }
```

Response (synchronous, target < 300ms):
```json
{
  "itemId": "01H...",
  "state": "queued",
  "duplicateOf": null
}
```

The client updates the toast immediately from this response. State transitions arrive via SSE or push.

---

## 7. Ingestion Pipeline — Detailed Spec

### 7.1 High-level flow

```
POST /v1/saves
   ↓
Postgres INSERT (items row, state=queued)
   ↓
Cloud Task enqueued: task_type=ingest, item_id=...
   ↓
Cloud Run worker picks task
   ↓
   1. Platform detection (regex)
   2. Extractor selection (interface, §7.3)
   3. Media download (video, audio, thumbnails)
   4. Frame sampling (ffmpeg, 1 frame / 2s)
   5. Long-audio branch: ffmpeg extract audio → Groq Whisper
   6. Gemini multimodal call (frames + audio or transcript)
   7. Structured summary parsing (Pydantic)
   8. Thumbnail upload to GCS
   9. MemoryStore.write() → Cognee Cloud
  10. Postgres UPDATE state=fully_indexed
  11. SSE emit + push notification
```

### 7.2 State machine (server authoritative)

Postgres `items.state` column, enforced by DB `CHECK` constraint on transitions:

```
queued → downloading → analyzing → indexing → fully_indexed
                                            → failed
text_indexed → queued  (via /enhance)
              → source_gone (if extractor 404s during enhance)
```

### 7.3 `Extractor` interface

```python
class Extractor(Protocol):
    platform: Platform
    async def can_handle(self, url: str) -> bool: ...
    async def extract(self, url: str) -> ExtractResult: ...

class ExtractResult(BaseModel):
    media_type: Literal['video', 'image', 'text']
    video_path: Path | None
    audio_path: Path | None
    image_paths: list[Path]
    original_caption: str | None
    original_owner: Owner | None
    duration_seconds: float | None
    resolved_at: datetime
```

Implementations: `CobaltExtractor`, `YtDlpExtractor`, `GalleryDlExtractor`. A registry maps `Platform → list[Extractor]` with priority order (Cobalt first, yt-dlp fallback). If all fail, item transitions to `failed`.

### 7.4 `MemoryStore` interface

```python
class MemoryStore(Protocol):
    async def write(self, user_id: str, item: IndexedItem) -> None: ...
    async def delete(self, user_id: str, item_id: str) -> None: ...
    async def search(
        self, user_id: str, query: str, filters: SearchFilters, top_k: int
    ) -> list[MemoryHit]: ...
    async def similar(
        self, user_id: str, item_id: str, top_k: int
    ) -> list[MemoryHit]: ...
    async def delete_namespace(self, user_id: str) -> None: ...
```

Implementations: `CogneeCloudStore` (v1 default), `CogneeOSSStore` (escape hatch, MVP-derived).

Namespacing: every `write()`/`search()` call scopes to the user's Cognee namespace (`f"user_{user_id}"`) — strict isolation.

### 7.5 Gemini call structure

For a typical short reel (≤ 90s):

- Input: sampled frames (up to 45) + full audio track + system prompt requesting the F2.5 structured summary.
- Model: `gemini-2.5-flash-lite` (default), `gemini-2.5-pro` on user "Deep" preference.
- Output: JSON matching the summary schema, validated by Pydantic.
- On JSON parse error: one retry with a repair prompt.
- Cost estimate per reel (Flash-Lite): ~$0.008–$0.015 depending on duration.

### 7.6 Long-content branch (≥ 5 min)

- ffmpeg extract mono 16kHz WAV.
- Groq Whisper-v3-turbo transcription (~$0.04/hr).
- Full transcript + reduced frame sample (1 frame per 10s) sent to Gemini for summary.

### 7.7 Ingestion cost model (v1 estimates)

| Item type | Extractor | ASR | Gemini | Total est. |
|-----------|-----------|-----|--------|------------|
| Reel ≤ 90s | Cobalt (~free) | Gemini implicit | Flash-Lite | $0.008–$0.015 |
| Long YT (5–30 min) | yt-dlp | Groq Whisper | Flash | $0.05–$0.10 |
| IG image carousel | gallery-dl | n/a | Flash-Lite | $0.003–$0.006 |
| IG bulk-import (text-indexed) | none | none | Embedding only | ~$0.0001 |
| Enhance (single item) | same as reel | same as reel | same as reel | $0.008–$0.015 |

Per-user daily budget alarm: $0.50. Monthly per-user target for typical usage: < $3.

---

## 8. Instagram Bulk Import — Detailed Spec

### 8.1 Upload flow

1. Client uploads ZIP via multipart to `POST /v1/import/instagram`.
2. Backend streams to a per-request tmpfs directory (never persisted to durable storage).
3. Backend responds `202 Accepted` with `importId` immediately.
4. Backend enqueues a Cloud Task `import_ingest`.
5. Worker unzips, locates `saved/saved_posts.html`, `saved/saved_collections.html`, `saved/saved_music.html`, and `start_here.html` (for schema version detection).

### 8.2 Parser (`InstagramExportParser`)

Uses BeautifulSoup4 + lxml. Schema-tolerant selectors keyed on visible field labels ("URL", "Caption", "Hashtags", "Owner") rather than fragile CSS class names (which Meta obfuscates and rotates).

**Per-entry extraction algorithm for `saved_posts.html`:**

```
for each <div class="pam ... uiBoxWhite noborder"> that contains a top-level <table>:
    entry = {}
    for each <tr> in the top-level <table>:
        label_cell, value_cell = tr.find_all('td')
        label = label_cell.get_text(strip=True)
        if label starts with "URL": entry['url'] = value_cell.find('a')['href']
        elif label == "Caption": entry['caption'] = value_cell.get_text('\n', strip=False)
        elif label starts with "Hashtags": entry['hashtags'] = extract_hashtag_list(value_cell)
        elif label starts with "Owner": entry['owner'] = extract_owner(value_cell)
        else: entry['_unknown'][label] = value_cell.get_text()
    entry['saved_at'] = parse_timestamp(sibling with class '_a6-o')
    yield entry
```

`extract_owner()` recursively descends into the nested owner table and returns `{url, name, username}`.

**Per-collection extraction for `saved_collections.html`:**

```
for each top-level collection container:
    collection = {'name': ..., 'type': ..., 'privacy': ..., 'update_time': ..., 'media': []}
    for each media entry inside collection.Media section:
        collection['media'].append(<same schema as saved_posts entry>)
    yield collection
```

Music parser mirrors this shape for `saved_music.html`.

### 8.3 Tiered processing decision (per entry)

Executed inline as parser yields entries:

```python
def decide_tier(entry, now, taxonomy):
    age_days = (now - entry.saved_at).days
    if age_days <= 30 and is_category_clear(entry, taxonomy):
        return Tier.TEXT_INDEXED
    if age_days <= 30 and not is_category_clear(entry, taxonomy):
        return Tier.FULL_MULTIMODAL
    return Tier.TEXT_INDEXED

def is_category_clear(entry, taxonomy):
    if entry.caption and len(entry.caption) >= 40:
        if any(kw in entry.caption.lower() for kw in taxonomy.keywords):
            return True
    matches = [h for h in entry.hashtags if h.lower() in taxonomy.hashtag_synonyms]
    return len(matches) >= 2
```

Thresholds (40 chars, 2 hashtag matches) live in a `taxonomy_config.yaml`.

### 8.4 Deduplication

- Unique constraint on `(user_id, source_url_normalized)` in Postgres.
- On collision: keep existing item, take earliest of the two `saved_at` timestamps, take highest tier reached.

### 8.5 Text-indexed write to Cognee

For text-indexed items, we still call `MemoryStore.write()` — but the `IndexedItem` contains:
- `text_content = f"{caption or ''} {' '.join(hashtags)} {owner.name if owner else ''}"`
- `title = first_sentence_of_caption or hashtags_summary`
- `no video/audio/thumbnail fields`

Cognee generates the embedding and indexes it. Search works immediately.

### 8.6 Progress reporting

`imports` table columns: `id, user_id, total_parsed, text_indexed_done, full_queued, full_done, failed, state, started_at, finished_at`. Worker updates counts every 100 entries. Client polls `GET /v1/import/{id}` or subscribes to SSE.

### 8.7 Collections import

- Each parsed collection creates a `collections` row with `source='instagram_import'`.
- `collection_items` join table populated from the `media[]` array.
- Collection rails appear on the client home screen (F4.3).

### 8.8 Error tolerance

- Malformed entries are logged with `_unknown` fields, skipped, and counted in `failed`.
- If more than 5% of entries fail parsing, the import is halted and the user notified — this is our early-warning signal for a Meta schema change.
- Structured log field: `parser_schema_version = hash(sorted(known_labels_seen))` — a change triggers a Slack/PagerDuty alert.

### 8.9 Storage during processing

- ZIP: tmpfs, deleted after parse completes or 15 min timeout.
- No original media files retained from imported items (bulk import never downloads video for text-indexed items).
- For items promoted to Full Multimodal, standard ingestion pipeline applies.

---

## 9. Data Model — Postgres Schema

Primary keys are ULIDs. All tables have `created_at`, `updated_at`. All FK constraints named. RLS is not used (application-level user scoping); every query includes `WHERE user_id = :current_user_id`.

```sql
CREATE TABLE users (
  id          TEXT PRIMARY KEY,       -- ULID; NOT Firebase UID
  firebase_uid TEXT UNIQUE NOT NULL,
  email       TEXT,
  display_name TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at  TIMESTAMPTZ
);

CREATE TABLE items (
  id                TEXT PRIMARY KEY,
  user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source_url        TEXT NOT NULL,
  source_url_norm   TEXT NOT NULL,     -- normalized (strip query params, lowercase host)
  platform          TEXT NOT NULL,
  state             TEXT NOT NULL,
  saved_at          TIMESTAMPTZ NOT NULL,
  title             TEXT,
  summary           TEXT,
  transcript        TEXT,
  on_screen_text    TEXT,
  caption           TEXT,
  hashtags          TEXT[] NOT NULL DEFAULT '{}',
  owner_name        TEXT,
  owner_username    TEXT,
  owner_url         TEXT,
  category          TEXT,
  user_category     TEXT,
  duration_seconds  REAL,
  thumbnail_url     TEXT,
  failure_reason    TEXT,
  source_type       TEXT NOT NULL,     -- 'share_extension' | 'instagram_import'
  cognee_id         TEXT,              -- id assigned by memory store, for delete/update
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT items_user_url_unique UNIQUE (user_id, source_url_norm),
  CONSTRAINT items_state_valid CHECK (state IN
    ('queued','downloading','analyzing','indexing','text_indexed','fully_indexed','source_gone','failed'))
);

CREATE INDEX items_user_saved_at_idx  ON items(user_id, saved_at DESC);
CREATE INDEX items_user_platform_idx  ON items(user_id, platform);
CREATE INDEX items_user_category_idx  ON items(user_id, category);
CREATE INDEX items_user_state_idx     ON items(user_id, state);

CREATE TABLE collections (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  privacy     TEXT NOT NULL,
  source      TEXT NOT NULL,          -- 'instagram_import' | 'user_created'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT collections_user_name_unique UNIQUE (user_id, name)
);

CREATE TABLE collection_items (
  collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  item_id       TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (collection_id, item_id)
);

CREATE TABLE imports (
  id                   TEXT PRIMARY KEY,
  user_id              TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  state                TEXT NOT NULL,  -- 'parsing' | 'indexing' | 'complete' | 'failed'
  total_parsed         INTEGER NOT NULL DEFAULT 0,
  text_indexed_done    INTEGER NOT NULL DEFAULT 0,
  full_queued          INTEGER NOT NULL DEFAULT 0,
  full_done            INTEGER NOT NULL DEFAULT 0,
  failed               INTEGER NOT NULL DEFAULT 0,
  parser_schema_hash   TEXT,
  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at          TIMESTAMPTZ,
  failure_reason       TEXT
);

CREATE TABLE saved_audio (
  id          TEXT PRIMARY KEY,
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT,
  artist      TEXT,
  saved_at    TIMESTAMPTZ,
  source_type TEXT NOT NULL             -- 'instagram_import'
);

CREATE TABLE ingestion_events (
  id         TEXT PRIMARY KEY,
  item_id    TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  from_state TEXT NOT NULL,
  to_state   TEXT NOT NULL,
  detail     JSONB,
  at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE push_tokens (
  user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token       TEXT NOT NULL,
  platform    TEXT NOT NULL,           -- 'ios' | 'android'
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, token)
);
```

`ingestion_events` gives an append-only audit trail of state transitions — useful for debugging pipeline anomalies and for the SSE stream.

---

## 10. Cognee Memory Layer

### 10.1 Deployment choice

- **v1: Cognee Cloud Developer tier** ($35/mo, 1,000 documents cap per single-developer plan — we operate the app on our shared plan with per-user namespaces; scale plan up as our aggregate document count grows).
- Access via Cognee's Python SDK, wrapped in `CogneeCloudStore` (our `MemoryStore` implementation).

### 10.2 Namespace isolation

Every `MemoryStore.write()` / `.search()` call includes `dataset=f"user_{user_id}"`. Cognee treats each namespace as isolated — no cross-user leakage.

### 10.3 What we send to Cognee

For each `write()`:
- Text corpus = `f"{title}\n{summary}\n{transcript}\n{on_screen_text}\n{caption}\nHashtags: {' '.join(hashtags)}\nCategory: {category}\nOwner: {owner_name}"`.
- Metadata dict = `{item_id, platform, saved_at, category, collection_ids, owner_username, duration_seconds, state}`.

Cognee handles embedding, chunking, graph construction.

### 10.4 What we retrieve

`search()` returns:
```python
MemoryHit(item_id: str, score: float, matched_chunks: list[str])
```

Backend joins `item_id` against Postgres for full item hydration.

### 10.5 Escape-hatch adapter

`CogneeOSSStore` is a stub in v1 (returning `NotImplementedError`) with the same interface. If we need to migrate:
- Deploy Cognee OSS to Cloud Run alongside a Postgres+pgvector+Kuzu bundle.
- Implement the four methods against the OSS Python API.
- Feature-flag flip the store singleton.
- Bulk re-index by iterating items table (cost = one text embedding per item ≈ negligible).

### 10.6 Deletion

On item delete: `MemoryStore.delete(user_id, item_id)` → Cognee remove call.
On account delete: `MemoryStore.delete_namespace(user_id)` → Cognee namespace purge.

---

## 11. LLM Providers

### 11.1 Gemini

- SDK: `google-genai` Python.
- Models: `gemini-2.5-flash-lite`, `gemini-2.5-flash`, `gemini-2.5-pro`. Migration plan to `gemini-3-flash-preview` before Oct 16 2026 when 2.5 Flash deprecates. Model IDs are configurable via env.
- API key: single project-wide key stored in Secret Manager. Per-user rate limiting done application-side.
- Retry: max 3 attempts with exponential backoff (250 ms, 500 ms, 1 s), skipping retries on 4xx.
- Timeout: 60 s per call (short reel), 180 s (long content).
- Cost logging: every call logged with `model, prompt_tokens, output_tokens, audio_tokens, image_frames, user_id, item_id`.

### 11.2 Groq (ASR fallback)

- SDK: `groq` Python.
- Model: `whisper-large-v3-turbo`.
- Called only for content ≥ 5 min.
- Retry: 2 attempts. Timeout: 120 s per audio file.

### 11.3 Provider abstraction

`SummaryModel` and `TranscriptionModel` protocols wrap providers so we can swap models without touching the pipeline:

```python
class SummaryModel(Protocol):
    async def summarize(self, frames: list[Frame], audio: AudioTrack | None,
                        transcript: str | None) -> StructuredSummary: ...

class TranscriptionModel(Protocol):
    async def transcribe(self, audio: AudioTrack) -> str: ...
```

---

## 12. Extractor Layer — Detail

### 12.1 Cobalt (primary)

- Deployed to GKE Autopilot cluster in the same GCP region as our workers.
- **Docker image**: upstream `ghcr.io/imputnet/cobalt:latest` (or a version-pinned tag), unmodified.
- Configured with API key auth (`API_URL_KEY`) — our workers include the key in every request.
- Zero persistent volume; ephemeral by design.
- HPA: min 2 pods, max 10.
- Called from workers via HTTP: `POST /api/json { "url": ..., "downloadMode": "auto" }` → returns tunneled download URL.

**AGPL-3.0 compliance note:** We run upstream Cobalt unmodified. If we ever modify Cobalt, AGPL requires we make our modified source available to users of the network service. Our SpillTheReel application code is not derivative of Cobalt (we talk to it over HTTP) and is therefore not encumbered. Any Cobalt modifications must live in `infra/cobalt/` and be public.

### 12.2 yt-dlp (fallback)

- Vendored as a Python package (`yt-dlp`) inside the worker container.
- Invoked in-process via the Python API (not subprocess) for speed.
- Cookies: none by default. If a specific platform demands cookies, we add a per-platform cookie file managed as a Secret Manager secret and refreshed manually on breakage.

### 12.3 gallery-dl (image content)

- Vendored as a Python package.
- Handles IG carousels and X image threads.

### 12.4 Extractor selection & fallback

```python
async def extract(url: str) -> ExtractResult:
    platform = detect_platform(url)
    for extractor in REGISTRY[platform]:  # priority-ordered
        if not await extractor.can_handle(url):
            continue
        try:
            return await extractor.extract(url)
        except ExtractorError as e:
            log.warning("extractor_failed", extractor=extractor.__class__.__name__, url=url, err=str(e))
            continue
    raise NoExtractorSucceededError(url)
```

Per-extractor per-platform success rate is a first-class metric (§17).

---

## 13. GCP Infrastructure

### 13.1 Services

| Service | Config | Notes |
|---------|--------|-------|
| Cloud Run — `api` | 1 vCPU, 512 MiB, min 1 max 20 instances, concurrency 80 | Public HTTPS via Cloud Load Balancer |
| Cloud Run — `worker` | 2 vCPU, 2 GiB, min 0 max 30, concurrency 4 | Consumes Cloud Tasks |
| Cloud Tasks | 3 queues: `ingest`, `import`, `enhance` | Per-queue rate limits & retry policies |
| Cloud SQL Postgres 16 | Small instance (2 vCPU, 8 GiB) with HA | Private VPC access |
| Cloud Storage | 1 bucket `spillthereel-media` with lifecycle rule (thumbnails cool after 30d) | Signed URLs for client fetches |
| GKE Autopilot | Cobalt namespace, 2–10 pods | Same region as Cloud Run |
| Secret Manager | Firebase creds, Gemini key, Groq key, Cobalt API key, Cognee API key | IAM-gated to Cloud Run service accounts |
| VPC connector | Cloud Run ↔ Cloud SQL private IP | |
| Firebase Auth | Google + Apple + Email providers | Free tier ample for v1 |
| Cloud CDN | In front of GCS thumbnail URLs | Cache TTL 30d |

### 13.2 Environments

- `dev` — a single shared cluster in `us-central1`, low-tier instances.
- `staging` — mirrors prod at ~10% capacity, real Gemini/Groq keys but a separate Cognee namespace.
- `prod` — `us-central1` primary, `us-east1` failover DNS for the API.

### 13.3 Regions & data residency

Primary region `us-central1`. Cloud SQL PITR enabled with 7-day window. Cognee Cloud is US-hosted; we surface this to users in the privacy policy.

### 13.4 IaC

Terraform in `infra/terraform/`. State in GCS bucket with versioning. Modules per service. One `prod.tfvars`, one `staging.tfvars`.

---

## 14. Rate Limits & Throttling

### 14.1 Client → API

- Global: 100 req/min per user, enforced by an in-memory token bucket per (user_id) in the API layer.
- `/v1/import/instagram`: 1 concurrent import per user, 5 imports per day.
- `/v1/items/{id}/enhance`: 60 per hour per user.

### 14.2 API → Extractors

- Per-user Cobalt calls: 60/hour (share-sheet + enhance combined).
- Bulk-import Full-Multimodal queue: throttled at 20 items/hour per user (F8.11) via Cloud Tasks queue rate limit.

### 14.3 API → Gemini

- Global concurrency cap: 40 in-flight requests, application-side semaphore.
- Per-user hard daily budget: $0.50 → alarm; $1.00 → 429 to client.

### 14.4 API → Cognee

- Batch writes when possible (up to 50 items per call in bulk imports).
- Per-user QPS cap: 10/sec.

---

## 15. Security

### 15.1 Transport

- HTTPS/TLS 1.3 on all endpoints. Cloud Load Balancer with Google-managed cert.
- HSTS `max-age=63072000; includeSubDomains; preload`.

### 15.2 Auth

- Firebase ID tokens (JWTs, RS256). Verified via `firebase_admin.auth.verify_id_token` — cached JWK certificates.
- No custom session cookies. No CSRF concerns (Bearer tokens only, no cookie auth).

### 15.3 Authorization

- Every DB query includes `WHERE user_id = :current_user_id`.
- Every Cognee call includes `dataset=f"user_{user_id}"`.
- Every GCS signed URL scoped to the specific object.

### 15.4 Secrets

- All API keys in Secret Manager, mounted to Cloud Run at boot via env var refs.
- No secrets in code, `.env`, or CI. `.env.example` documents required var names only.

### 15.5 Input validation

- All request bodies parsed by Pydantic v2 with strict mode.
- URL validation: allowlist of source hosts (`instagram.com`, `tiktok.com`, `youtube.com`, `youtu.be`, `x.com`, `twitter.com`). Anything else rejected 400.
- IG export ZIP: max 500 MB, single-file scan for zip bombs (max uncompressed 5 GB, max entry count 100k).

### 15.6 Sandboxing

- Workers run non-root in distroless containers.
- ffmpeg / yt-dlp subprocess calls use `-nostdin` and time-limited timeouts.
- No arbitrary user code execution paths.

### 15.7 Data retention

- Ingested source video: deleted from worker tmp within 15 min of processing complete.
- Thumbnails: retained indefinitely (small, cheap).
- IG export ZIP: destroyed after parse, never persisted.
- Postgres backups: 7 days.
- Cognee data: retained until user deletes item / account.

### 15.8 Compliance

- GDPR: right to export (F7.3) implemented as a bg job dumping Postgres rows to JSON in GCS with 24h signed URL. Right to delete (F5.7) cascades to Cognee and GCS.
- CCPA: same primitives cover it.
- No CCPA "sale" — we do not sell user data.
- Privacy policy explicitly enumerates: LLM providers, Cognee, Firebase, GCP, Sentry, PostHog.

---

## 16. Deployments & CI/CD

### 16.1 Branch model

- `main` — always deployable to prod.
- Feature branches → PR → CI → merge → auto-deploy to staging → manual promote to prod.

### 16.2 GitHub Actions

Workflows:
- `mobile-ci.yml` — TS type-check, ESLint, Jest, `eas build --profile preview` on PR.
- `mobile-release.yml` — `eas build --profile production` + `eas submit` on tag `mobile-v*`.
- `api-ci.yml` — ruff, mypy, pytest, docker build.
- `api-deploy-staging.yml` — on merge to `main` → `gcloud run deploy` to staging.
- `api-deploy-prod.yml` — manual dispatch → prod.
- `infra.yml` — Terraform plan on PR, apply on merge with manual approval.

### 16.3 Mobile release channels

- `preview` — internal builds, TestFlight external testers + Play Console internal track.
- `production` — App Store + Play Store.
- EAS Update channels: `preview`, `production` — over-the-air JS-only fixes.

---

## 17. Observability

### 17.1 Logs

- Structured JSON via `structlog`.
- Fields: `ts, level, event, user_id, item_id, request_id, trace_id, span_id, extractor, model, cost_usd, duration_ms`.
- Sink: Cloud Logging with 30-day retention.

### 17.2 Tracing

- OpenTelemetry SDK in FastAPI + workers.
- Spans: HTTP handler, extractor call, ffmpeg, Gemini call, Groq call, Cognee write, Postgres query.
- Export to Cloud Trace.

### 17.3 Metrics

Custom metrics exported to Cloud Monitoring:

| Metric | Type | Tags |
|--------|------|------|
| `ingest_duration_ms` | histogram | platform, tier, outcome |
| `extractor_success_rate` | gauge | platform, extractor |
| `gemini_cost_usd_sum` | counter | model, user_id (aggregated) |
| `groq_cost_usd_sum` | counter | user_id (agg) |
| `cognee_write_latency_ms` | histogram | outcome |
| `search_latency_ms` | histogram | outcome, has_filters |
| `import_parse_success_rate` | gauge | parser_schema_hash |
| `active_workers` | gauge | queue |

### 17.4 Errors

- Sentry SDK in mobile (Expo integration) and backend (FastAPI middleware).
- PII scrubbing rules: redact URLs, captions, transcripts from Sentry payloads. Only stack + `event, request_id, user_id_hash` retained.

### 17.5 Alerts

- Ingest failure rate > 10% for 30 min on any platform → PagerDuty.
- API p99 latency > 3s for 15 min → PagerDuty.
- Any single user's Gemini spend > $1/day → email + throttle to 429.
- Parser schema hash change → Slack alert.
- Cloud SQL CPU > 80% for 10 min → PagerDuty.

### 17.6 Analytics

- PostHog SDK on mobile.
- Events: `app_open`, `sign_in`, `import_started`, `import_completed`, `share_saved`, `search_run`, `search_result_tap`, `enhance_tap`, `item_deleted`.
- No content payloads in events — event props limited to counts and enums.

---

## 18. Testing Strategy

- **Unit tests** (pytest, Jest) — pure functions, model methods, parser edge cases. Coverage target 80%.
- **Integration tests** (pytest with docker-compose Postgres) — API endpoints against real DB.
- **Contract tests** — mobile TS types generated from backend Pydantic schemas, kept in `packages/shared-types/`. CI enforces regeneration.
- **Parser corpus tests** — a suite of anonymized IG export snippets covering every field variation observed. Any parser change re-runs the corpus.
- **E2E tests (mobile)** — Detox on iOS + Android simulators, covering: sign-in, save-via-share, search, enhance flow.
- **Ingestion smoke tests** — nightly against a fixed set of URLs across platforms.
- **Load test** — k6 script targeting `/v1/search` and `/v1/saves` at 100 rps sustained.

Failure of any of the above blocks the corresponding deploy pipeline.

---

## 19. Cost Model (steady-state estimate)

Assumptions (typical mid-cohort user):
- 30 new saves / month via share sheet, all fully-indexed.
- 1 bulk import at signup with 2,000 items (all text-indexed, 200 auto-enhanced via clarity heuristic).
- 10 searches / month.

Per-user monthly:
- Gemini: 30 × $0.012 (share saves) + 200 × $0.012 (auto-enhance) + 10 × $0.003 (search synthesis) ≈ $2.79
- Groq: negligible unless long-form YT
- Cognee Cloud: prorated Developer-tier cost (start ~$0.05 / active user / month; will re-tier at growth)
- GCS + Cloud Run + Cloud SQL: ~$0.15 / user / month at scale
- Cobalt (GKE): ~$0.05 / user / month at scale

**Total ≈ $3–4 per active user per month steady-state.** Free tier is sustainable up to ~10k active users on runway alone; monetization kicks in at 1–5 lakh users (per PRD §7).

---

## 20. Migration & Rollback

- Every Cloud Run deploy is a new revision; traffic gradually shifted (10% → 100% over 10 min).
- Rollback = `gcloud run services update-traffic ... --to-revisions=REV=100`.
- Mobile: EAS Update rollback = `eas update:republish --channel production` to a prior update ID.
- DB migrations: `alembic` upward-only; every migration paired with a documented forward-compatible rollout plan (add column nullable → backfill → make non-null in later deploy).

---

## 21. Open Technical Questions

- **iOS silent share UX polish** — decide between `expo-share-extension` custom view (Pinterest-style) vs opening the app briefly. Recommendation: custom view. Confirmation needed after UI design.
- **Chunking strategy for very long captions in text-indexed items** — Cognee handles chunking, but do we pre-truncate captions > 5000 chars? Draft: yes, truncate to 5000 and keep full in Postgres.
- **User-provided taxonomy override** — should users be able to define their own custom categories that then influence category-clarity heuristic? Deferred to v1.5.
- **Which region for Cognee** if they offer choice — pick us-central1 for latency to our GCP workers.
