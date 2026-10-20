# SpillTheReel: current-state review

Review started 2026-10-16; reconciled with the working tree on 2026-10-18.
Execution plan: [LUNA_IMPLEMENTATION_PLAN.md](LUNA_IMPLEMENTATION_PLAN.md).
Design direction: [DESIGN_EXECUTION_BRIEF.md](DESIGN_EXECUTION_BRIEF.md).

## Product understood

SpillTheReel is a private mobile memory library for saved social content. The
useful loop is **save or import → understand the content → find it by meaning →
open the original**. A person should be able to find a remembered recipe, place,
exercise, or idea without remembering its creator or caption. The app is not a
public feed, a downloader product, or a social account synchronization service.

The owner explicitly chose full ingestion for imported media because export
metadata alone does not provide the transcription needed for recall. Free bulk
imports allow 250 items once per account: 150 Instagram and 100 pooled across
other supported apps. Premium allows 1,000 total once per account across supported
apps. These allowances do not reset monthly or per ZIP, and do not define the
allowance for individual share saves. Upgrade and refund semantics remain design
assumptions to resolve before releasing quota enforcement.

## Evidence and limits

This review inspected the original build phases, PRD/TRD/architecture, current
routes, services, models/migrations, mobile routes/components/data/auth/API code,
Terraform, CI, and tests. It is an architecture and implementation review, not a
claim of exhaustive line-by-line security auditing.

The baseline commit was `b59d427`; findings include the working tree. Existing
uncommitted database-cache edits in `app/db/engine.py`, migrations `env.py`, and
`test_engine_statement_cache.py` were preserved. `HANDOFF.md` was already present
and remains historical. No application fixes are delivered by this planning batch.

The frontend was rendered in Expo Web. Welcome was inspected at desktop size;
Home, Categories, Ask, Import, Profile, and Settings were inspected at 390×844
through a temporary local route importing the actual screen components with
signed-out defaults. That route was removed after review. This is visual evidence
of the implemented screens, not authenticated end-to-end or native device evidence.
Item detail and remaining auth flows were reviewed in source. No screenshot
goldens or native accessibility certification were produced.

Earlier local checks passed: 345 API unit tests, 12 skipped private parser
fixtures; Ruff lint/format, mypy, and mobile TypeScript. The final handoff's
validation record in the execution plan supersedes that count after the latest
working-tree changes. Integration, real provider, native build, and cloud load
tests were not run in this review.

Supabase MCP table discovery succeeded and reported RLS enabled for the listed
public application tables, including embeddings and join tables. This does not
prove policy correctness, grants, API role behavior, or the applied migration
revision. Automatic approval review hit a usage limit before the migration-version
query and gcloud project lookup executed. Cloud deployment claims in old updates
are therefore unverified here. No live migration, deployment, deletion, or paid
extraction was performed.

## What exists and should be kept

| Area | Implemented foundation | Remaining gap |
|---|---|---|
| Mobile | Expo 57 / RN 0.86, Router, branded components, Supabase auth, live search/chat, item detail/retry, account deletion request | Much of library/profile/import/settings still uses local or illustrative data; no complete native capture loop |
| API | FastAPI routes for saves, items, search, imports, categories, events, users, push-token registration | Durable admission/dispatch, lifetime quotas, complete import status, authenticated-role integration evidence |
| Ingestion | Extractor interfaces/fallbacks, media handling, Gemini/Groq branches, YouTube native path, summaries, embeddings | Duplicate delivery, concurrency fencing, retry accounting, persistent budgets |
| Retrieval | User-scoped pgvector, filters, cards, answer model and citation checks | Actual content evidence in answer prompt and stronger citation binding |
| Data | Supabase Auth/Postgres/Storage, SQLAlchemy + raw asyncpg, Alembic, HNSW, RLS migration | Quota/outbox/import attribution/deletion lifecycle schema and role-aware tests |
| Infrastructure | Cloud Run/Tasks/Secret Manager/WIF Terraform and CI definitions | Environment verification, capacity/secret scope, recovery/rollback and release evidence |

Keep these boundaries. The project does not need a framework rewrite, another
vector vendor, Kubernetes, or a new migration framework to finish the product.
TanStack Query and Zustand appear in old plans/comments but are not installed;
do not describe them as current architecture.

## Findings ordered by product impact

Priority P1 blocks trustworthy beta behavior; P2 should be addressed before a
broader release. “Confirmed” means visible in source or rendered UI; a runtime
failure still needs the acceptance reproduction described in the plan.

| ID | Priority / evidence | Finding and consequence | Source | Plan |
|---|---|---|---|---|
| B01 | P1, confirmed ordering | Save/retry commits before enqueue; import items enqueue before chunk commit. A dispatch failure can strand committed work; early delivery can miss an uncommitted item. Retrying a duplicate does not reliably repair dispatch. | `api/v1/saves.py`, `api/v1/items.py`, `services/imports/ig_pipeline.py::_run`, `tasks/enqueue.py` | T02 |
| B02 | P1, confirmed control flow | The ingestion transition helper returns an already indexed item, but the caller continues into expensive processing. A short row lock does not protect the full external processing interval. | `services/ingest/pipeline.py::ingest_item`, `_lock_and_transition_downloading` | T03 |
| B03 | P1, confirmed | Imports stop at indexing; there is no durable item attribution to finish counters. Re-entry resets local queue counts, skips prior rows, and can overwrite totals. Collection media is flattened without materializing folders. | `services/imports/ig_pipeline.py`, `db/models.py` | T06 |
| B04 | P1, confirmed absence | No lifetime allowance ledger or trusted premium entitlement is implemented. Full ingestion is intentional; the missing control is admission and bounded processing, not text-first tiering. | `api/v1/imports.py`, `services/imports/ig_pipeline.py`, `db/models.py` | T05–T07 |
| B05 | P1, confirmed | Vector hits have empty matched chunks; card hydration omits content evidence. Answer snippets are therefore absent despite stored summaries/transcripts. The model is asked to answer largely from titles. | `services/memory/pgvector_store.py::search`, `services/search/pipeline.py::_SessionCardLoader`, `search` | T04 |
| B06 | P1, confirmed | Citation filtering checks allowed IDs and index ranges independently, not their pairing. Dropped citations do not force fallback. Filtering deleted hits can leave noncontiguous answer indexes. Unsupported answer text can remain visible. | `services/search/pipeline.py::search`, `tests/unit/test_search_pipeline.py` | T04 |
| B07 | P1, confirmed reads; exploit not executed | Upload size is checked after reading the whole file. ZIP member reads lack decompressed-byte/entry/ratio bounds; large uploads still parse inline. Missing recognized files may look like an empty import. | `api/v1/imports.py::create_import`, `services/imports/ig_parser.py` | T07 |
| B08 | P1, failure-path risk | Deletion removes profile data before the external auth deletion has definitely succeeded. If auth remains, lazy profile creation can undermine deletion. Concurrent workers need a durable deletion fence. | `services/users/deletion.py`, `auth/middleware.py`, `api/v1/users.py` | T10 |
| B09 | P1, evidence gap | RLS is enabled, but service/owner roles can bypass it. Transaction-local claims are affected by commits. Explicit owner filters exist; no cross-tenant leak was demonstrated. Join/embedding ownership consistency needs DB tests under real restricted roles. | `auth/middleware.py`, `db/migrations/versions/20261009_0001_rls_hardening.py`, `db/models.py` | T01, T09 |
| B10 | P2, confirmed | Thumbnail signed URLs are persisted although storage URLs expire. Later cards can contain expired images. Item deletion also needs storage cleanup, not only relational/vector deletion. | `services/ingest/pipeline.py::_finish_indexed`, `services/media/supabase_storage.py`, `api/v1/items.py` | T08, T10 |
| B11 | P2, confirmed | Timestamp-only pagination can omit equal-time rows. Long-lived SSE sessions poll while retaining a session/transaction; reconnect semantics lack a stable resumable cursor. | `api/v1/items.py`, `api/v1/events.py` | T08 |
| B12 | P1 before scaled bulk import | Budget code enforces request estimates, not cumulative spend. In-memory rate limits are not cross-instance limits. Legitimate retries, concurrent imports, and extractor fallback can amplify spend. | `services/llm/budget.py`, `ratelimit.py`, `infra/modules/cloud_tasks` | T11 |
| B13 | P2, defense-in-depth risk | Worker verifier accepts a service-account email suffix instead of the exact configured caller. IAM currently supplies another boundary; exploitability depends on deployed policy. | `tasks/worker_entry.py::verify_worker_oidc`, `infra/envs/staging/main.tf` | T09, T20 |
| B14 | P2, latest edits require verification | Raw asyncpg now disables its statement cache. The latest edit removes the separate SQLAlchemy prepared cache setting; installed dialect defaults that cache to 100. Factory-argument tests alone do not validate transaction-pooler behavior. | `db/engine.py`, `db/migrations/env.py`, `tests/unit/test_engine_statement_cache.py` | T01 |
| B15 | P2, confirmed mismatch | Mobile aborts all requests after 15 seconds; server search permits retrieval plus answer budgets longer than that. “Enhance” currently re-summarizes stored text, not fresh media ingestion. | mobile `src/lib/api.ts`; API `services/search/pipeline.py`, `services/ingest/enhance.py` | T04, T12, T16 |
| B16 | P2, configuration risk | Cloud Run concurrency and two DB pools need a combined connection budget. Project-wide secret access and image-tag ownership need tightening. README and updates disagree about deployed state. | `infra/envs/staging`, `infra/modules/secret_manager`, `db/engine.py`, Cloud Build definitions | T20 |

All API paths in that table are relative to `apps/api/app/` unless prefixed otherwise.

## Frontend and experience findings

| ID | Evidence | Finding | Plan |
|---|---|---|---|
| U01 | Source + rendered | Home displays hardcoded 342 saves, growth/chart and collection placeholders. Profile claims connected sources and counts without account-sync evidence. Users cannot trust the overview. | T14, T17 |
| U02 | Source + rendered | Import completion is a timer after picking a file, not an upload. Privacy copy says original media never leaves Instagram despite server ingestion. | T15 |
| U03 | Source + rendered | Categories are stored under an unscoped local key with seeded counts; filters do not represent server filtering. The 30-slot dial repeats categories and is an awkward primary browse control. | T12, T14 |
| U04 | Source + rendered | Oswald serves both headlines and body text; several essential labels are tiny uppercase text. The current palette is distinctive, but semantic typography and contrast rules are inconsistent. | T13 |
| U05 | Source + rendered | Five Unicode-only tabs lack clear names and selected-state accessibility; fixed top/bottom spacing does not establish safe-area behavior. Chat keyboard/native focus behavior remains unverified. | T13, T16 |
| U06 | Source + rendered | Settings has temporary toggles/toast-only actions. Live auth, chat, item detail and deletion exist and should be extended rather than rebuilt. | T12, T17 |
| U07 | Source | Share packages/config exist, but the app has no complete share-intent handler/offline queue. Token registration is not push delivery; notifications service is empty. | T18, T19 |

The detailed design brief defines the native treatment and screen acceptance
criteria. No user research or usability study was conducted; the proposed
information architecture is a design recommendation to validate in implementation.

## Reconciliation with buildphase.md

| Original phase | Current interpretation |
|---|---|
| 0 Foundations | Substantial scaffold and CI exist; deployment/native-build acceptance is incomplete |
| 1 Save → ingest → search | Substantial backend and chat implementation; reliable dispatch/duplicates and grounded citations block acceptance |
| 2 Instagram bulk import | Parser/worker exists; UI upload, quotas, terminal status, safety limits and folders remain |
| 3 Share / notifications | Package and endpoint foundations only; end-to-end native work remains |
| 4 Library / settings | Many screens exist, but illustrative state is not a completed library |
| 5 Hardening / observability / cost | Some logging, Sentry and request limits exist; cumulative budgets, recovery and release evidence remain |
| 6 Launch | Not release-ready based on the evidence collected |
| 7 Stabilization | Instrumentation/recovery gates should be built before launch, then exercised after launch |

Do not turn this into a completion percentage. Old phase estimates, deployed
claims, and unchecked checklists are not proof of either absence or completion.
Old age-based tiering, unlimited historical backfill, and “no premium gate”
requirements are superseded by the owner's explicit import policy.

## Implementation update — 2026-10-18

The baseline above is historical. A local T02/T04 implementation batch now
records task intent in the same transaction as save/retry/enhance/import/category/
deletion state and dispatches only after commit. A bounded outbox reconciler
service exists, but this repository still needs a Cloud Run Job/Scheduler trigger
and IAM/monitoring before pending intents recover in a hosted environment. No
isolated PostgreSQL transaction or Cloud Tasks crash/outage integration was run.

Search now hydrates bounded transcript/summary/on-screen/caption evidence and
keeps the returned card order, answer-hit order and citation index map aligned.
Invalid citation mappings fall back to deterministic excerpts. Optional quote
spans are retained only when they occur in that exact item's evidence. Independent
code and security reviews confirmed these T04 fixes. The Gemini policy prompt is
now passed as `system_instruction`, and malformed-response logs no longer include
response content. These are local code/test results, not live-model evaluation.

The new `task_outbox` Alembic revision is a single child of the prior head and the
full chain generated offline SQL. Docker/PostgreSQL was unavailable, so the
migration was not applied to even a disposable database. The enabled RLS/no-policy
setup still needs role/grant/PostgREST verification. The security review also
retains follow-up gates for exact configured worker identity and deployed Cloud Run
invoker IAM (B13), cleanup of outbox identifiers after successful account deletion
(T10), and bounded upload buffering/ZIP expansion (B07/T07). No production or
cloud resources were changed.

## Architecture recommendation

Retain the modular FastAPI service and worker deployment. Add a transactional
outbox and worker leases, durable import membership/admission accounting, evidence
hydration for answers, and a recoverable deletion lifecycle. Keep PostgreSQL as
the source of truth; Cloud Tasks delivers work and the mobile app observes state.
Use Alembic for incremental schema changes. Avoid building a second orchestration
platform or treating an SSE connection as durable job state.

The highest-value product milestone is a real, reliable vertical slice: accept
one save, complete ingestion once, show the actual item, answer from its content,
open its original, and delete it safely. The next milestone is a bounded real
import with truthful progress. Visual consistency should be developed alongside
those contracts, then applied to real data throughout the app.
