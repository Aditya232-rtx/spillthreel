# T01 contracts: database pooler, background work, and recall

**Status:** accepted for local implementation planning, 2026-10-18. This records
code and schema contracts; it does not authorize a live migration or deployment.

## Evidence captured

- Local Alembic history has one head: `20261009_0001_rls_hardening`, with the
  chain `0001_initial → 20260930_0001_pgvector_memory → 20261009_0001_rls_hardening`.
  This does not establish the revision applied in any hosted database.
- Installed local dependencies are SQLAlchemy 2.0.54 and asyncpg 0.31.0. The
  asyncpg SQLAlchemy adapter's own prepared-statement LRU defaults to 100; passing
  `statement_cache_size=0` disables only asyncpg's native cache. Its DBAPI adapter
  consumes `prepared_statement_cache_size` separately before calling asyncpg.
- `apps/api/app/db/engine.py` now disables both cache layers for SQLAlchemy and
  asyncpg's native cache for the raw pgvector pool. Online Alembic disables both
  asyncpg cache layers. Direct URL versus transaction-pooler behavior against a
  live Supabase project was not exercised; `apps/api/.env.example` describes the
  API URL as pooler and the Alembic direct URL as preferred.
- The local migration graph initially had one head; T02 has since added
  `20261018_0001_task_outbox` as a single child of the existing head. Offline
  `alembic upgrade head --sql` generated the full chain through the new head. No
  isolated PostgreSQL `upgrade` was run because Docker was unavailable. ORM
  constraints are incomplete for composite tenant
  ownership: embedding, category and collection relationships use separate
  single-column FKs. `Item` has `(user_id, source_url_norm)` uniqueness; `Import`
  has aggregate counters only.
- The `Item.source_url_norm` uniqueness is the identity boundary for duplicate
  URL detection. A duplicate bulk entry may attach import provenance to an existing
  item without creating a second item or charging another admission.

## Background-work contract for T02/T03

The item/import/admission state transition and its outbox intent are committed in
the same database transaction. A dispatcher reads committed outbox jobs only and
may deliver the same intent more than once. Queue acceptance updates outbox delivery
state only after successful acceptance; a lost acknowledgement is reconciled by
repeating the same immutable job identity. Non-duplicate queue failures remain
retryable and observable.

An outbox identity includes operation, target type/ID and generation. A deliberate
retry receives a new generation; re-dispatch of one generation reuses its identity.
Task-name dedup is an optimization, not the source of business idempotency. The
local dispatcher follows the same state contract as Cloud Tasks.

Workers atomically claim a short lease and commit the claim before calling external
providers. They verify generation/lease ownership before each terminal database
write and storage/vector side effect; stale workers cannot overwrite a newer attempt
or an account deletion. A completed duplicate performs no paid extraction/model call.
Lease expiry permits safe recovery. Provider operations without idempotency support
may still be repeated after a crash; record that limit and avoid promising exactly-once
external billing.

The initial `TaskOutbox` proposal stores task type, target type/ID, minimal JSONB
payload, generation, attempts, availability, lease token/expiry, dispatch time,
last error and creation time. Unique identity is task type + target type + target
ID + generation. It intentionally has no FK to a user/item because deletion work
must survive target/profile removal. RLS is enabled with no client-facing policy.
This migration is not evidence that hosted grants or policies are effective;
verify under the actual database roles before release.

Outbox payloads contain minimal identifiers, not media, archives, secrets, or
unbounded user text. A future dispatcher must have bounded claims, retry/backoff,
visibility into oldest pending age, and a declared Cloud Run trigger/identity. Do
not rely solely on the lifetime of a request process to flush it.

## Import/admission contract for T05–T07

Account allowance is serialized transactionally. A unique durable reservation is
linked to one import entry/item admission. A manual share remains outside this
allowance. Replays of the same import selection do not make a second reservation.
Lifetime consumed usage survives logout, another ZIP, month change and item deletion.
Free allowance buckets remain distinct: Instagram up to 150, other supported apps
pooled up to 100, total 250. Premium has one total pool up to 1,000. Full ingestion
is the only accepted media processing path.

Potential schema should keep `import_entries` with a unique stable identity per
import; retain duplicate/rejected/unsupported/admitted outcomes. Use composite
owner foreign keys or equivalent constraints where relationships join a user-owned
parent and child. Candidate `item_embeddings` ownership is enforced by separate
FKs today; adding a composite FK requires a unique `(user_id,item_id)` parent key
and a migration/review, not an ORM-only assertion.

Do not count saved-audio metadata until D03 is confirmed. Do not activate premium,
refund, downgrade or retention assumptions until their policy version is configured
by an owner-approved decision. Existing historical aggregate counters are not
sufficient to backfill lifetime allowance without an account inventory and a
documented reconciliation rule.

## Search contract for T04

After user-scoped retrieval, deduplicate IDs and hydrate active items. Build one
final ordered list with contiguous 1-based indexes. Each list entry contains the
same item ID, card data and bounded evidence fields from that item. Allowed evidence
is title, summary, transcript, on-screen text, caption and hashtags; omit absent
fields and cap total prompt size. Treat all retrieved user content as untrusted data,
never as system instructions.

Both text markers (`[n]`) and structured citations must bind to the exact ID at that
index. Every marker must be in range and represented by a matching citation; every
citation ID/index must match the same entry. A missing/invalid/mismatched citation,
or empty evidence, triggers a deterministic extractive fallback over the final card
list instead of showing the model's unsupported text. If no active card remains,
return the exact no-match sentence without an answer-model call.

Keep the existing score threshold until a labeled evaluation justifies a change.
T04 adds deterministic no-provider tests; paid live-model evaluation is separate.
Coordinate total backend and mobile request deadlines so valid results are not
cancelled by a shorter client timeout.

## Migration and validation ownership

The primary agent owns all shared schema/model changes and creates one Alembic
revision at a time. Specialists return schema recommendations; they do not run
autogenerate or edit migration files concurrently. Validate metadata against raw
SQL migrations and use an explicitly disposable local PostgreSQL database before
claiming an upgrade succeeds. The CI-only Supabase auth bootstrap is only for
vanilla test Postgres and must never run against Supabase Cloud.

This contract can evolve after a regression or a confirmed owner decision. Update
this record and the plan ledger before dependent T02/T04 interfaces diverge.

## Implementation evidence and open release gates (2026-10-18)

T02 locally implements transactional intent writes and post-commit dispatch for
the existing save, retry/enhance, import, category and account-deletion flows.
The parent-owned Alembic revision adds `task_outbox`; offline SQL generation
passes. The fastapi-developer reported 61 focused tests passing; parent full API
unit suite passes 356 tests (12 skipped, 16 warnings). A Cloud Run Job/Scheduler
caller for `dispatch_due_jobs(limit<=200)` is not configured in this repository,
so a process-independent recovery trigger is still required before hosted use.
Cloud Tasks outage/duplicate-delivery integration and DB transaction tests remain
open. A successful account deletion must eventually scrub retained outbox
identifiers/payloads; keep this work in T10 so the active deletion intent survives
until it reaches a terminal state.

T04 now uses the provider system-instruction channel for policy, sends the
question/evidence JSON as user content, aligns citation indexes with the final
hydrated item order, and drops optional spans not found in that item's evidence.
Malformed model-response logging excludes response content. Independent
code-reviewer and security-auditor follow-up confirmed the fixes. Focused search
regressions are included in the passing suite; live-model evaluation is not run.

Security review leaves these explicit gates: bind worker OIDC email to the
configured Cloud Tasks service account and verify deployed Cloud Run invoker IAM
(T09/T20); bound streaming upload and ZIP decompression/count/CPU resources
(T07); validate `task_outbox` grants/RLS using an isolated Postgres/PostgREST setup.
The outbox migration was not applied because no local Postgres/Docker runtime was
available. These checks remain local or staging-only; no live service was touched.
