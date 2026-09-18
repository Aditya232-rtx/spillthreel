# SpillTheReel — Product Requirements Document (PRD)

**Version:** 1.1
**Status:** Draft — pre-build
**Owner:** Aditya Jadhav
**Last updated:** 2026-09-10

---

## 1. Product Summary

**SpillTheReel** is a cross-platform mobile "second brain" for saved short-form social media content. Users share any Reel / Short / TikTok / X video / post URL into the app; the backend downloads the media, extracts audio + key visual frames, uses multimodal AI (Gemini) to generate a rich searchable summary, and stores the result in a semantic/graph memory layer (Cognee). Users then query their library in natural language ("the red car reel", "the ramen recipe with miso", "the workout with the resistance band") and get an answer with the source content preview and deep link back to the original post.

New users can also **bulk-import their entire Instagram save history** (thousands of reels + collections) from Instagram's official data export in a single onboarding step — captions, hashtags, and creators become instantly searchable with zero video processing cost.

**Tagline:** *Turn doomscrolling into instant recall.*

**One-line pitch:** Your saved Instagram / TikTok / YouTube Shorts / X posts, all searchable by what they contain — voice, visuals, and topic — not just by keyword or hashtag.

---

## 2. Problem Statement

Modern users save hundreds of short-form videos across Instagram, TikTok, YouTube Shorts, and X. The platforms' native "Saved" folders are:

- **Chronological, not searchable.** You cannot find "the recipe with tahini" among 800 saved reels.
- **Siloed.** Content saved on TikTok cannot be searched from Instagram.
- **Opaque.** The metadata is limited to the caption; the actual visual, audio, and semantic content of the video is invisible to search.
- **Ephemeral.** Original posts get deleted; the saved reference dies.

Result: users save content aspirationally but functionally never revisit it. The value of the save is destroyed by inaccessibility.

**SpillTheReel makes every saved reel first-class content:** transcribed, visually analyzed, summarized, categorized, cross-platform, permanently indexed, and answerable by natural-language query — plus a lossless bulk backfill of the user's entire historical Instagram library from Meta's own data export.

---

## 3. Goals & Non-Goals

### 3.1 Goals (v1.0)

1. **Frictionless capture.** Sharing a URL into SpillTheReel from any supported platform takes ≤ 2 taps and never requires opening the SpillTheReel app.
2. **Deep multimodal understanding.** Every new-save item is analyzed on video, audio, and image dimensions — not just caption text.
3. **Lossless historical backfill.** Users can import their entire Instagram save history from Meta's data export in one step, with cost-aware tiered processing.
4. **Natural-language recall.** Users find items by describing them, not by keyword. "The reel with the dog on a skateboard" works.
5. **Cross-platform library.** Instagram, TikTok, YouTube Shorts, X — one searchable library.
6. **Store-ready native app.** Ships as installable apps on Apple App Store and Google Play Store.
7. **Answer, not just link.** Query results include a synthesized answer plus previews and deep links back to the source content.

### 3.2 Non-Goals (v1.0)

- **LinkedIn ingestion** — deferred to v2 (technical reasons documented in TRD).
- **Content creation / editing** — we ingest and index, we don't produce content.
- **Social features** — no following, no sharing libraries between users. Personal knowledge base only.
- **Offline-first search** — v1 requires connectivity; offline caching of recent items is v1.5.
- **Web app / desktop app** — mobile-only for v1. Web is v2.
- **Automatic ingestion from platform bookmarks (live scraping)** — no scraping of user's IG "Saved" folder in real time. Only explicit share and the official Meta data export.
- **Content re-upload / re-hosting** — we store summaries and thumbnails only; we do not re-host source videos.
- **Team / shared collections** — single-user only in v1.
- **Automatic full-multimodal processing of the entire historical library** — cost-guarded; only last-30-days items with ambiguous caption/hashtags get automatic full processing (see F8).

### 3.3 Non-goals we get asked about

- We are **not** a downloader app. Downloaders retain files for the user; we consume them transiently to build an index.
- We are **not** a public search engine. Every user has a private library.

---

## 4. Target Users & Personas

### 4.1 Primary — "The Aspirational Saver"
Age 18–35, saves 20+ reels/week across 2–3 platforms. Categories: recipes, workouts, travel, product recommendations, style, life-hacks. Has never successfully re-found a saved reel after a week. Pain: guilt + FOMO on their own saved content.

### 4.2 Secondary — "The Professional Researcher"
Creator / marketer / analyst. Saves competitor content, trend references, market examples, sound-bite templates. Needs to cite and re-locate specific posts fast. Willing to pay for a serious tool.

### 4.3 Tertiary — "The Curious Learner"
Saves educational shorts (finance, science, coding, languages). Wants to build a personal knowledge graph of concepts from short-form content.

---

## 5. Supported Platforms & Content Types

### 5.1 Source platforms (v1)

| Platform | Content types supported | Notes |
|----------|------------------------|-------|
| Instagram | Reels, Feed video posts, Feed image posts, Carousels, Collections (via bulk import) | Public posts only via share-sheet. Bulk import supports everything in the user's Meta data export. |
| TikTok | Videos, Photo carousels | Watermark-stripping not offered (respect creator attribution). |
| YouTube | Shorts, standard videos ≤ 30 min | Full-length videos allowed but flagged as "long-form" (different summarization strategy). |
| X (Twitter) | Native video posts, image tweets, GIF tweets, text threads | Text-only tweets are ingested as summaries of the text. |

### 5.2 Deferred (v2+)

- **LinkedIn** (v2, via paid extractor)
- **Facebook Reels / Watch** (v2)
- **Reddit videos / images** (v2, low effort — Cobalt supports it)
- **Pinterest** (v2)
- **Podcasts / audio-only** (v2.5, changes the pipeline)
- **TikTok bulk import** (v1.5, if their export format matures)
- **YouTube Takeout bulk import** (v1.5)

### 5.3 Client platforms

- **iOS** ≥ 15.1 (App Store)
- **Android** ≥ API 24 / Android 7 (Play Store)
- **No web / desktop in v1.**

---

## 6. Core Features (v1)

### F1. Frictionless Share-to-Save

**User story:** *As a user browsing Instagram, when I see a reel I want to save, I tap Share → SpillTheReel, see a small "Saved ✓" confirmation, and continue scrolling Instagram without ever opening SpillTheReel.*

**Sub-features:**

- **F1.1 — Native share target registration.** SpillTheReel appears as a first-class destination in the iOS Share Sheet and Android Share Intent picker on install.
- **F1.2 — Background submit.** The share extension POSTs the URL to our backend and returns immediately — no app launch, no navigation away from the source app.
- **F1.3 — Confirmation toast.**
  - **Android:** native `Toast` (floating pill over Instagram at bottom-mid) reading "Saved to SpillTheReel ✓".
  - **iOS:** in-share-sheet mini popup (~100pt tall, custom view via `expo-share-extension`) showing "Saving…" then "Saved ✓", auto-dismissing in ~1.2s. iOS sandboxing makes true floating-over-other-apps toasts impossible for any third-party app; this is the closest legal equivalent and matches Pinterest / Raindrop's UX.
- **F1.4 — Duplicate detection.** If the same URL is shared twice, backend deduplicates and toast reads "Already saved". No wasted processing.
- **F1.5 — Offline queue.** If the device has no network at share time, URL is stored locally in the share extension's shared container and retried on next network availability.
- **F1.6 — Auth handoff.** Share extension inherits the user's auth session from the main app via Apple App Groups (iOS) and Android Keystore-backed shared preferences (Android).
- **F1.7 — Unsupported URL feedback.** If the shared item is a text-only IG story link or a LinkedIn post, the toast reads "Not supported yet — coming soon" and the URL is logged (privately, aggregated) so we can prioritize new platform support.

**Acceptance criteria:**

- 95th-percentile share-to-toast latency < 900ms on both platforms.
- Zero cases of the main app launching during a normal share flow.
- Share extension bundle < 5MB on iOS.

---

### F2. Multimodal Ingestion Pipeline

**User story:** *As a user, after I save a reel, within ~60 seconds it becomes searchable — with the visual content, spoken audio, on-screen text, and semantic meaning all indexed.*

**Sub-features:**

- **F2.1 — URL resolution & platform detection.** Regex-based classifier maps URL → platform → extractor.
- **F2.2 — Media extraction.** Backend calls the correct extractor:
  - **Cobalt** (self-hosted on our GKE) for TikTok, X, IG Reels, YT Shorts — primary
  - **yt-dlp** for edge cases and long-form YT — fallback
  - **gallery-dl** for IG carousels / X image threads — image-only content
  - All three sit behind a single `Extractor` interface (see TRD).
- **F2.3 — Audio transcription.** Extracted audio is transcribed:
  - Content < 5 min: sent directly to **Gemini 2.5 Flash / Gemini 3 Flash** as multimodal input (transcription happens implicitly during understanding).
  - Content ≥ 5 min: audio-only extracted with `ffmpeg`, transcribed with **Groq Whisper-v3-turbo** (cheaper for long-form), then fed as text to Gemini.
- **F2.4 — Visual analysis.** Video is sampled at 1 frame per 2 seconds (configurable) and passed to Gemini for scene description, object detection, on-screen text extraction (OCR), and dominant color/aesthetic capture.
- **F2.5 — Unified summary generation.** Gemini produces a structured summary object:
  ```
  {
    "title": string,           // ~8-word natural title
    "summary": string,         // 2–3 sentence description
    "transcript": string,      // full transcript
    "on_screen_text": string,  // OCR
    "objects": string[],       // detected objects/subjects
    "scenes": string[],        // 3–5 scene descriptions
    "topics": string[],        // auto-categorization tags
    "sentiment": enum,         // informative | funny | inspirational | tutorial | product | other
    "primary_language": string,
    "duration_seconds": number
  }
  ```
- **F2.6 — Thumbnail generation.** Middle-frame thumbnail + poster frame extracted with `ffmpeg`, stored in Cloud Storage, served via signed URL.
- **F2.7 — Memory-layer indexing.** Summary + transcript + metadata written to **Cognee Cloud** with the user's namespace. Cognee builds embeddings + graph nodes + relationships automatically.
- **F2.8 — Auto-categorization.** Topics from F2.5 are mapped to a canonical taxonomy (Food, Fitness, Travel, Tech, Fashion, Finance, DIY, Comedy, Education, Product, Music, Other) for filter chips in the UI. If the user imported Instagram Collections, their custom collection names are preserved as additional user-defined categories.
- **F2.9 — Item lifecycle states.** Each item has one of these states:

  | State | Meaning |
  |-------|---------|
  | `queued` | Waiting for worker |
  | `downloading` | Extractor running |
  | `analyzing` | Gemini processing |
  | `indexing` | Writing to Cognee |
  | `text_indexed` | Only caption/hashtags indexed (bulk-import path) |
  | `fully_indexed` | Complete multimodal indexing done |
  | `source_gone` | Original URL 404s; metadata-only entry preserved |
  | `failed` | Non-recoverable error after 3 retries |

  Status is exposed to the client via WebSocket/SSE and via push notification on transition to `text_indexed` or `fully_indexed`.

- **F2.10 — Failure handling & retry.** Failed jobs are retried with exponential backoff up to 3 times, then surfaced in the UI with a "Retry" button and a debug reason.

**Acceptance criteria:**

- Median time from share to `fully_indexed`: < 60 seconds for reels ≤ 90 seconds long.
- 95th percentile: < 3 minutes.
- Failure rate (non-retryable) < 5% on supported platforms.
- Storage cost per item < $0.01.
- Compute cost per item (extraction + Gemini) < $0.02 for reels ≤ 90s.

---

### F3. Natural-Language Search & Retrieval

**User story:** *As a user, I type "the red car video" in the search bar and instantly see the reel I saved three weeks ago showing a red vintage Mustang — with a plain-English answer, a video thumbnail, and a tap-to-open link back to Instagram.*

**Sub-features:**

- **F3.1 — Query understanding.** User query is passed to Cognee's retrieval pipeline (semantic + graph). Cognee returns top-K candidate items with relevance scores.
- **F3.2 — Answer synthesis.** Top candidates + query are sent to Gemini with a RAG prompt to produce a conversational answer citing specific items.
- **F3.3 — Multi-modal query surface.** Search is invoked from three entry points:
  - **Search tab** — dedicated screen, chat-like UI with query history.
  - **Home surface** — search bar at top of home feed.
  - **Voice input** — long-press mic icon, speech-to-text via `expo-speech-recognition`, then normal query flow.
- **F3.4 — Filter chips.** Below the search bar: Platform (IG, TikTok, YT, X), Category (F2.8 taxonomy + user IG Collections), Time (last week, month, all), Processing state (all, fully-indexed only). Chips narrow the retrieval space server-side.
- **F3.5 — Search-by-visual.** User can also tap a saved item and ask "find more like this" — server sends that item's embedding as query vector.
- **F3.6 — Search-by-example-text.** "Find reels that mention 'protein'" is a keyword-fallback path (BM25 over stored transcripts + captions) when semantic search underperforms for exact phrases.
- **F3.7 — Result cards.** Each result shows: thumbnail, title, 1-line summary, source platform icon, saved date, processing state badge (text-only vs full), and a "Open in [Platform]" button (deep link) + "Preview" button (in-app modal).
- **F3.8 — Answer citations.** The synthesized answer text has inline superscript numbers `[1] [2]` that link to the corresponding result card.
- **F3.9 — No-results fallback.** If retrieval returns nothing above threshold, Gemini produces a "Nothing matched — did you mean X, Y, Z?" reformulation suggestion, and if the user has many `text_indexed` items, suggests enhancing them.
- **F3.10 — Query history & saved queries.** Recent queries are stored locally + synced; user can pin a query as a persistent filter (e.g., "recipes with under 5 ingredients").

**Acceptance criteria:**

- 95th-percentile search response time (answer + top 5 results): < 2.5 seconds.
- Recall@5 on hand-labeled test queries: ≥ 0.85 for fully-indexed items, ≥ 0.70 for text-only items.

---

### F4. Library / Home / Browse

**User story:** *As a user, I open the app and see my recent saves organized by category and by my Instagram Collections, freshest first, with quick filters for platform and topic.*

**Sub-features:**

- **F4.1 — Chronological feed.** Newest-first list of all saved items. Infinite scroll, virtualized list.
- **F4.2 — Category rails.** Horizontally scrolling rails per auto-detected category (Food, Fitness, etc.) on home screen.
- **F4.3 — Collections rails.** If the user imported IG Collections, each collection becomes a horizontally scrolling rail on the home screen (labeled with the collection's original name).
- **F4.4 — Platform tabs.** Toggle to filter by source platform.
- **F4.5 — Item detail view.** Full-screen sheet showing: thumbnail (auto-plays a 3-sec loop if we cached one), full summary, transcript, tags, save date, source URL, "Open original" button, "Enhance with video analysis" button (only shown if `text_indexed`), "Delete" and "Ask about this reel" buttons.
- **F4.6 — Manual re-tagging.** Long-press a card → menu with "Move to category", "Move to collection", "Add tag". User taxonomy overrides auto-tags.
- **F4.7 — Delete.** Deletes the item from Cognee, Postgres, and Cloud Storage. Confirmation modal.
- **F4.8 — Bulk actions.** Multi-select with long-press → bulk delete, bulk tag, or bulk enhance (queue all selected `text_indexed` items into the full multimodal pipeline).
- **F4.9 — Empty state.** For new users who skipped bulk import: illustrated onboarding walkthrough of the share-sheet flow with a "Try me" demo reel we pre-load.

---

### F5. Authentication & Onboarding

**User story:** *As a new user, I install the app, sign in with Google or Apple in one tap, am offered to bulk-import my Instagram save history, and am guided through granting the share-sheet permission before I see the home screen.*

**Sub-features:**

- **F5.1 — Sign-in options.** Google Sign-In (mandatory), Apple Sign-In (mandatory for App Store approval), Email/password (fallback).
- **F5.2 — Supabase Auth backend.** Tokens are issued by Supabase Auth (asymmetric ES256/RS256 JWTs by default on new projects). Backend verifies the JWT signature offline via the project's public JWKS endpoint (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`), cached in-process for ~10 minutes — no per-request round-trip to Supabase after warmup. Legacy HS256 secrets are supported as a fallback for older projects; the middleware branches on the token's `alg` header.
- **F5.3 — Session persistence.** Refresh tokens stored in `expo-secure-store` (iOS Keychain / Android Keystore).
- **F5.4 — Bulk import invitation (optional, dismissible, anytime).** Immediately after first sign-in, an onboarding card offers: *"Bring in your saved Instagram reels — takes 60 seconds."* Tapping it launches F8's guided flow.
  - The card has three equally-weighted actions: **Import now**, **Maybe later** (dismisses for this session), and **Skip forever** (never shown again on onboarding).
  - The user is **never forced** to import to reach the home screen. Skipping proceeds straight to F5.5.
  - Import remains fully accessible after onboarding from three surfaces at any time: (a) Settings → *"Import from Instagram"*, (b) an empty-state prompt on the home screen when the library is small, (c) a persistent "Import" chip in the Library screen header until the user's first successful import.
  - Import is repeatable — the user can upload a fresh export ZIP at any time to backfill newer saves (F8.8 dedup handles this cleanly).
- **F5.5 — Share-extension permission onboarding.** After sign-in (regardless of whether the user imported, skipped-for-now, or skipped-forever), an interactive 3-step guide shows the user how to trigger the share sheet from Instagram, how SpillTheReel appears, and confirms they've completed one test save before dismissing.
- **F5.6 — Sign-out.** Full local wipe (SecureStore + async storage + query cache).
- **F5.7 — Account deletion.** Required for App Store compliance. Deletes the user's Cognee namespace, Supabase Postgres rows (RLS-scoped cascade), Supabase Storage bucket subfolder, and the Supabase Auth user record (which is the PK — a single `DELETE FROM auth.users` cascades everything else).
- **F5.8 — Push notification permission.** Requested on first successful save ("We'll ping you when it's ready to search") — deferred permission ask, not on first launch.

---

### F6. Notifications

**User story:** *As a user who saved a reel and closed my phone, I get a light push notification 45 seconds later saying "3 reels ready to search" so I know my library grew.*

**Sub-features:**

- **F6.1 — Ingestion complete push.** When a saved item transitions to `fully_indexed`, if the user has left the app, a push is scheduled. Batched: multiple items in the last 60 seconds → single grouped notification.
- **F6.2 — Ingestion failed push.** After 3 failed retries, a "Couldn't save that reel — tap to retry" push.
- **F6.3 — Bulk import status push.** For long-running imports and backfills: "Import finished — 1,842 reels ready to search" or "Backfill 40% complete".
- **F6.4 — Weekly digest (opt-in).** Sunday 6 PM local: "You saved 14 reels this week — top topic: recipes."
- **F6.5 — Notification preferences.** Toggle each category in settings.

---

### F7. Settings & Preferences

- **F7.1 — Language.** UI language + primary content language preference (impacts Gemini prompts).
- **F7.2 — Auto-delete originals.** Toggle: after N months, prompt user to re-verify original still exists (deep link check) and optionally delete stale entries.
- **F7.3 — Data export.** Download JSON of all summaries + metadata (SpillTheReel-native format).
- **F7.4 — Danger zone.** Account deletion, wipe all data.
- **F7.5 — Model quality preference.** "Fast" (Gemini Flash) vs "Deep" (Gemini Pro) — trades cost/latency vs summary depth. Global default: Fast.
- **F7.6 — Import history.** List of past bulk imports with dates and per-import counts. Ability to re-run import with an updated export ZIP.

---

### F8. Instagram Bulk Import (from Meta Data Export)

**User story:** *As a new user with 3,000+ historical Instagram saves, I upload my Meta data export ZIP once, and within ~60 seconds my entire save history — captions, creators, hashtags, and my custom Collections — is searchable in SpillTheReel, without any video processing cost.*

This is the **flagship onboarding feature** that turns SpillTheReel from "empty app" to "3,000 searchable memories" in one action. **It is entirely optional and can be triggered at any point in the user's lifecycle** — onboarding, weeks later, or repeatedly whenever the user generates a fresh Meta export. Nothing about the app is gated behind having imported.

**How the user gets their data (documented in-app):**

Instagram Settings → Accounts Center → Your information and permissions → Export your information → Create export → Choose account → Export to device → **Select "Saved content and your collections"** → download the ZIP. The ZIP contains a `start_here.html` and a `saved/` folder with:
- `saved_posts.html` (individual saves)
- `saved_collections.html` (user-organized collection folders)
- `saved_music.html` (saved audio tracks)

**Sub-features:**

- **F8.0 — Entry surfaces (import is anytime).** Users can start an import from all of the following surfaces:
  - Onboarding invitation card (F5.4) — with clear *Skip forever* option.
  - Settings → *Import from Instagram* — always available, always discoverable.
  - Home screen soft prompt when total library size is small (< 20 items) — dismissible.
  - Library screen header chip — persists until the user's first successful import, then hides.
  - Push notification tap-through for users who requested their export from Meta (optional v1.5 hookup — reminder logic).
  - Nothing about the core save/search/recall loop is gated behind having imported. A user can happily use SpillTheReel forever purely through the share-sheet flow (F1) and never import.
- **F8.1 — Guided export walkthrough.** In-app step-by-step guide with screenshots of Instagram's export flow. Deep link to Instagram's Accounts Center where possible.
- **F8.2 — Upload interface.** User picks the export ZIP from their device via native file picker. Supports both HTML and JSON export formats (JSON when Meta ships it consistently; HTML is our v1 primary since the user has confirmed HTML in Aug 2026).
- **F8.3 — Parser (schema-tolerant).** Backend parses:
  - **saved_posts.html:** per-entry extracts URL, Caption, Hashtags[], Owner {URL, Name, Username}, Save timestamp.
  - **saved_collections.html:** per-collection extracts Name, Type, Privacy, Update time, Media[] (each with the same URL/Caption/Hashtags/Owner schema as saved_posts).
  - **saved_music.html:** per-entry extracts Title, Artist, Save timestamp (music entries create a separate "Saved Audio" section, not treated as reels).
  - Parser is field-tolerant: if Instagram removes/renames fields in a future export version, present fields are used and missing ones default to null. A schema-version detector warns the user if a field is unrecognized (so we can add support quickly).
- **F8.4 — Tiered processing decision.** For each parsed saved-post entry, the ingestion pipeline decides the processing tier at parse time:

  | Age of item | Category clarity from caption/hashtags | Processing tier |
  |---|---|---|
  | ≤ 30 days | Clear (heuristic in F8.5) | **Text-indexed** (caption + hashtags → embedding + auto-category) |
  | ≤ 30 days | Ambiguous | **Full multimodal** (auto-queued) |
  | > 30 days | Any | **Text-indexed** only |
  | Any | User taps "Enhance" post-import | **Full multimodal**, on-demand |

  **Post-scale evolution (documented switch, not v1):** once we cross ~1–5 lakh users and have infrastructure headroom, remove the "clarity" gate — all items ≤ 30 days get full multimodal automatically; older items stay text-only unless user enhances.

- **F8.5 — Category-clarity heuristic.** An item is "clear" if:
  - Caption length ≥ 40 chars AND contains ≥ 1 category keyword from our canonical taxonomy, OR
  - ≥ 2 hashtags matching taxonomy synonyms.
  - Otherwise → "ambiguous".
  - Thresholds are stored as config values, tunable post-launch without a deploy.

- **F8.6 — Collections preservation.** Every Instagram Collection becomes a first-class "Collection" object in the user's library, with the original name and privacy. Its members are linked to their parsed post entries. Collections show up as home-screen rails (F4.3) and as filter chips (F3.4).

- **F8.7 — Saved audio ingestion.** Items from `saved_music.html` are stored as `SavedAudio` records (not reels). v1 exposes them as a simple searchable list (title + artist) under a "Saved Audio" tab in the library, but does not integrate them into the multimodal pipeline.

- **F8.8 — Progress + resumability.** Import shows a live progress screen: `Parsed 3,420 items → text-indexed 3,120 → queued for full processing 80 → done`. If the user closes the app mid-import, the job resumes server-side and pushes a completion notification. Idempotent: re-uploading the same ZIP does not create duplicates.

- **F8.9 — Duplicate detection with share-sheet saves.** If a URL is present in both the import and the user's share-sheet saves, the entry is unified (single item, save timestamp from the earliest source, processing state from the highest tier reached).

- **F8.10 — Dead-link handling.** If the extractor later fails on a URL (creator deleted their reel), the item is marked `source_gone` and remains searchable via caption/hashtags/creator, but flagged in the UI ("Original no longer available").

- **F8.11 — Instagram rate-limit courtesy.** For items in the "queued for full processing" tier, extractor calls are throttled to 20 items/hour per user to avoid tripping Instagram's anti-bot rate limits on Cobalt/yt-dlp.

- **F8.12 — Selective bulk enhance.** Even after import, user can tap "Enhance all text-indexed items in [Collection X]" to send an entire collection through full multimodal processing at their discretion. Progress screen + throttled queue.

**Acceptance criteria:**

- Median parse-and-text-index time for a 1,000-item export: < 60 seconds.
- Median parse-and-text-index time for a 5,000-item export: < 4 minutes.
- Text-indexed items are searchable by caption content within 10 seconds of parse completion.
- Collections import creates the exact same category structure the user had in Instagram, with 100% fidelity.
- Zero data loss on re-import of an updated export ZIP (dedup by URL).
- Total cost per bulk-import of 5,000 items: < $0.30 (text-indexing only, before any enhancements).

---

## 7. Monetization

**Status: DEFERRED to build-phase planning.**

v1.0 launches with:
- **Freemium — unlimited saves for all users.** No caps at launch.
- No premium tier gated behind payment at launch.

We revisit pricing when we reach 1–5 lakh users. Model, gates, and tier design will be finalized during the build phase in consultation with real usage data. Placeholder framing:

- Likely a two-tier freemium model with the free tier capped on either monthly saves or on full-multimodal enhancements per month, and the paid tier unlocking unlimited enhancements + Gemini Pro quality + weekly digests + data export.
- Payment infra will be RevenueCat wrapping Apple IAP + Google Play Billing when we ship the tier.

Cost-control guardrails **while free tier is unlimited**:
- Full multimodal processing on bulk imports is opt-in beyond the last-30-days ambiguous slice (F8.4).
- Extractor throttling per user (F8.11).
- Hard per-user daily Gemini budget alarm at $0.50/user/day.

---

## 8. Non-Functional Requirements

### 8.1 Performance

- Cold start of the app: < 2.5s on iPhone 12 / Pixel 6.
- Share-to-toast: < 900ms P95.
- Save-to-fully-indexed median: < 60s for ≤ 90s reels.
- Bulk-import parse + text-index for 1k items: < 60s median.
- Search response P95: < 2.5s.
- Home feed initial render: < 1.5s from cold.

### 8.2 Reliability

- API uptime SLO: 99.5% monthly.
- Ingestion pipeline success rate: ≥ 95% on supported platforms.
- Bulk-import parser success rate: ≥ 99% (schema-tolerant).
- Push delivery success: ≥ 90% (industry norm).

### 8.3 Security & Privacy

- All API traffic HTTPS/TLS 1.3.
- Supabase JWTs validated on every request.
- Per-user data isolation enforced at THREE layers: (a) Supabase Postgres Row-Level Security policies, (b) Cognee per-user namespaces, (c) application-level `WHERE user_id = auth.uid()` filters as defense-in-depth. Every query passes all three checks.
- Bulk-import ZIP is streamed to backend, parsed in-memory (or in an ephemeral tmpfs), and destroyed after processing. **Original ZIP is never persisted to durable storage.**
- No sharing of user data with third parties beyond the LLM providers (Gemini, Groq) — and those receive only the media content the user explicitly submitted or imported.
- No storage of user's IG/TikTok credentials — we never ask for them; we only accept public URLs from the share sheet and Meta-issued data exports.
- Full GDPR + CCPA compliance: right to export (F7.3), right to delete (F5.7).

### 8.4 Accessibility

- WCAG 2.1 AA compliance for in-app screens.
- Voice-input search (F3.3) for motor-impaired users.
- Dynamic type support on iOS.
- Screen-reader labels on all interactive elements.

### 8.5 Internationalization

- v1 English-only UI; content ingestion supports any language Gemini supports (~50).
- v1.5: UI translations for Hindi, Spanish, French, Portuguese, Indonesian.

---

## 9. Success Metrics

### 9.1 North-star metric

**Weekly Active Recallers (WAR):** users who both save (or import) ≥ 1 item AND run ≥ 1 search in a 7-day window. This captures the loop's completion.

### 9.2 Supporting metrics

| Metric | Target (Month 3 post-launch) |
|--------|-------------------------------|
| D1 retention | ≥ 40% |
| D7 retention | ≥ 20% |
| D30 retention | ≥ 12% |
| % of new users completing bulk import | ≥ 55% |
| Median items per bulk import | ≥ 500 |
| Avg saves per WAU (new saves via share sheet) | ≥ 5 |
| Avg searches per WAU | ≥ 3 |
| Search → item-tap CTR | ≥ 55% |
| Share-flow completion (share → toast) | ≥ 98% |
| Ingestion success rate | ≥ 95% |
| Bulk-import parser success rate | ≥ 99% |
| % of text-indexed items ever enhanced | tracked, no target |
| CSAT (in-app 5-star ask, monthly) | ≥ 4.2 |
| App Store rating | ≥ 4.5 |

### 9.3 Guardrails (regression alarms)

- Median save-to-fully-indexed > 120s for 3 days → alert.
- Ingestion failure rate > 10% on any single platform for 24h → alert.
- Gemini API cost per user > $0.50/day → alert (cost creep).
- Bulk-import parse failure rate > 2% → alert (schema change).

---

## 10. User Flows (canonical)

### 10.1 First-run WITH import (happy path)

Install → open → sign in (Google/Apple) → **bulk-import invitation** (three choices: Import now, Maybe later, Skip forever) → user taps *Import now* → guided IG export walkthrough → upload ZIP → progress screen → import complete → share-sheet permission walkthrough → home (populated with imported items + demo tour).

### 10.2 First-run WITHOUT import (equally supported path)

Install → open → sign in → invitation card → user taps *Maybe later* or *Skip forever* → share-sheet permission walkthrough → home (empty state with demo reel + subtle "you can still import your Instagram history anytime" chip on Library screen).

### 10.2b Delayed import (weeks later)

User has been using the app via share-sheet only → generates a fresh Meta export → opens Settings → *Import from Instagram* → same guided walkthrough → upload → progress → complete. All existing share-saved items dedup cleanly with imported entries.

### 10.3 Save via share sheet

In IG → tap Share on a reel → share sheet → tap SpillTheReel → toast "Saved ✓" → stay in IG.
(In background: URL posted → Cloud Task queued → worker downloads → Gemini analyzes → Cognee indexes → push "1 reel ready".)

### 10.4 Search

Open app → tap search / voice / home search bar → type "red car" → answer + result cards → tap card → detail sheet with "Open in Instagram" deep link.

### 10.5 Selective enhance from library

Open library → tap a text-indexed item → detail view → tap "Enhance with video analysis" → confirmation → item state transitions text_indexed → queued → analyzing → fully_indexed → push notification.

### 10.6 Recovery flow (failed save)

Push "Couldn't save that reel" → tap → app opens on the failed item detail → "Retry" button → re-queues job.

---

## 11. Assumptions & Dependencies

- Gemini API availability and pricing remain within 30% of Sept 2026 published rates.
- Cognee Cloud SLA holds; if it degrades, we have the OSS-adapter escape hatch.
- Cobalt self-hosted remains a functional extractor for IG/TikTok/X. If a platform hard-blocks it, we fall back to yt-dlp and log ingestion failures for reactive fixes.
- Instagram, TikTok, YouTube, and X do not fundamentally break their share-URL formats.
- Meta's data export continues to include Caption + Hashtags + Owner fields for saved posts (verified against the user's Aug 2026 export). Parser is schema-tolerant if fields disappear.
- App Store and Play Store approve the share-extension-based save flow (well-precedented — Pinterest, Raindrop, Pocket).

---

## 12. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| Extractor breakage (IG/TikTok anti-bot escalation) | High | Medium | Multi-extractor fallback chain; monitoring per-platform success rate; ability to hot-swap adapters |
| Gemini API cost overrun | High | Medium | Tiered processing (F8.4); Flash-first policy; batch API for non-urgent reprocessing; hard budget alerts |
| Cognee Cloud outage | High | Low | OSS adapter behind same interface; can migrate namespace via API export |
| App Store rejection of share extension | Medium | Low | Pattern is well-precedented; test build reviewed by TestFlight external testers first |
| Copyright/DMCA claim from creators | Medium | Low | We don't rehost media; thumbnails are fair-use previews; original URL always deep-linked back; DMCA takedown process documented |
| Meta changes data export schema | Medium | Medium | Schema-tolerant parser; schema-version detector alerts us within 24h of first affected import; hotfix cycle < 48h |
| User's IG/TikTok account risk | High | Very Low | We never scrape logged-in content; we consume only what the user explicitly shares via the OS share sheet or the Meta-issued data export |
| LLM hallucination in search answers | Medium | Medium | Answers always cite source item cards; UI makes clear the answer is AI-generated |
| Bulk-import runaway cost (aggressive full processing) | High | Medium | Tiered decision (F8.4); throttled queue (F8.11); daily per-user budget alarms |

---

## 13. Out-of-Scope / Later Versions

### v1.5

- Web app (read-only search)
- Pricing / premium tier via RevenueCat (revisited when we hit 1–5 lakh users)
- UI translations
- Offline caching of last 100 items
- iOS custom share-extension polish (larger preview, per-category picker)
- TikTok / YouTube Takeout bulk import

### v2.0

- LinkedIn ingestion (via Apify or equivalent paid adapter)
- Facebook Reels, Reddit, Pinterest ingestion
- Team libraries (shared collections)
- Weekly AI-generated "digest reels" — mini summary videos of the user's week
- Voice-first mode (Siri Shortcut / Android Assistant integration)

### v2.5+

- Podcast / audio-only ingestion
- Chrome / Safari desktop extension for saving from web
- Public API for third-party integrations
- Auto-daily incremental sync from a re-uploaded IG export

---

## 14. Open Questions

- Do we cache the source video briefly (24h) to enable in-app preview playback, or always deep-link out to the source? (Legal review pending.)
- Voice-input search language auto-detection vs manual selection.
- Post-scale, what's the exact user-count threshold to enable "all last-30-days items get full processing" (F8.4 evolution)?
- Should IG Collections marked "Private" in the export be imported by default, or require user opt-in per collection?

---

## 15. Glossary

- **Reel** — colloquial term for any short-form vertical video; in this doc used platform-agnostically.
- **Save** — the act of a user sharing a URL into SpillTheReel via the share sheet.
- **Import** — the act of a user uploading a Meta data export ZIP for bulk historical backfill.
- **Ingestion** — the backend pipeline that turns a URL into an indexed item.
- **Item** — one indexed piece of content in a user's library.
- **Collection** — a user-defined folder of items, imported from Instagram Collections or created in-app.
- **Cognee namespace** — per-user isolated memory partition in Cognee.
- **Extractor** — a platform-specific module that resolves a source URL to downloadable media.
- **Text-indexed / fully-indexed** — the two primary processing tiers per item state (F2.9).
- **Enhance** — upgrading a text-indexed item to fully-indexed via user-initiated full multimodal processing.
