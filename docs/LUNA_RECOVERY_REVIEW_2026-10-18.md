# Recovery review and staging-test handoff — 2026-10-18

This review supersedes the prior assertion that Batch 1 has no remaining source
blockers. Keep the implemented CAS, evidence budget and local consumer changes,
but finish the repairs below before accepting T02/T20a. This turn reviewed code
and updated instructions; it did not modify application code or test staging.

## Confirmed findings

| ID | Priority | Evidence and impact | Required change and proof |
|---|---|---|---|
| R07 | P1 | `infra/envs/staging/main.tf:240` recovery Job has no `WORKER_BASE_URL`; `tasks/enqueue.py` requires it outside dev. Every hosted delivery fails configuration. | Set the recovery Job target to `module.worker.url`; check the resulting env against required transport settings without credentials or provider calls. |
| R08 | P2 | `infra/envs/staging/main.tf:154` still grants dispatcher access to `SUPABASE_SECRET_KEY`, contrary to the last report. Settings no longer needs this secret. | Remove that secret IAM binding as well as keeping it absent from env. Update stale API-identity comments. Verify effective permissions separately when authorized cloud validation runs. |
| R09 | P2 | `tasks/dispatch_due.py:39` never configures logging. A fresh-process synthetic log failed JSON parsing; Terraform metrics filter `jsonPayload`. | Configure logging before the first runner log. A subprocess regression in staging mode must parse safe batch fields as JSON without DB/provider access. |
| R10 | P2 | `tasks/outbox.py:320` uses default gather. Claim/finalization DB exceptions can unwind the runner before siblings finish; CLI cleanup can race those tasks. At line 287, loss of finalization ownership is still returned as False and counted failed. | Collect bounded per-job outcomes, await or explicitly cancel/drain every child before cleanup, preserve cancellation semantics, distinguish actual failure from lost ownership. Test one failing job alongside a blocked sibling and the stale successful finalizer. |
| R11 | P2 acceptance gap | `tests/integration/test_task_outbox_concurrency.py:107` replaces a lease, commits, then calls finalization. It proves stale-token rejection but has no simultaneous transactions. | Keep it; add coordinated two-connection claim/finalize interleavings with barriers, bounded timeouts and exact state assertions for both success and error branches. |

The existing `test_pgvector_store.py:83` runs Alembic against its target database
and line 101 deletes a fixed `auth.users` ID. Never point the full current suite at
shared staging. Its skip message also interpolates the full DSN: remove that
credential leak before any remote test runner can exercise this fixture.

## Owner authorization and staging procedure

The owner explicitly authorizes testing the staging database using
`apps/api/.env.staging` and opening the installed Docker Desktop as needed.
Both `DATABASE_URL` and `DATABASE_URL_DIRECT` are present; only presence was checked,
not connection success. `apps/mobile/.env.staging` is not the backend DB source.

1. Read both installed Supabase skills. Use the API Python 3.12 virtualenv.
   Parse the dotenv file into memory with dotenv tooling, without shell sourcing,
   printing credentials, embedding DSNs in command arguments, or defaulting to
   `.env`. Pass only the selected DB variables to test subprocesses. Sanitize
   exceptions and test output; disable SQL echo, shell tracing and show-locals.
2. Confirm the designated staging target using its own configuration. Start with
   read-only connectivity, server/role/extension information, migration-version
   inventory and relevant schema/constraint/grant metadata. Do not read user
   content. Report redacted target labels and schema drift rather than migrating
   the existing staging application schema.
3. Test the application's pooler connection separately from the direct connection.
   Exercise repeated transactions through SQLAlchemy and native asyncpg with the
   production cache configuration. Determine pool mode from evidence, not port
   alone. A direct-connection pass does not establish pooler compatibility.
4. For synthetic outbox CAS/overlap checks, use a unique, non-exposed test schema on
   staging, after verifying permission and reliable schema isolation. Prefer direct
   or verified session-mode access for the current fixture; do not assume startup
   search_path works through a transaction pooler. Fully qualify test objects or
   prove the intended schema before every DDL/DML path. Deny public access, mock
   transport/providers, create no real users, and clean up only objects created by
   this run in a finally block. If those isolation conditions cannot be met, retain
   read-only staging results and run the write tests on disposable local Postgres.
5. Run migration upgrade/downgrade, destructive fixtures, role/ownership matrices
   and the full integration suite only on disposable local PostgreSQL with pgvector
   and synthetic auth bootstrap. Explicitly set BOTH database URL settings to local
   test endpoints so the Alembic direct URL cannot leak back to staging. Never run
   the normal dispatcher against shared staging rows: it would enqueue real work.
6. Record read-only staging, isolated staging writes, local migrations and pooler
   tests as separate results. Failures/skips are not passes. If networking or
   credentials fail, report only sanitized error class and continue local work.

This authorizes bounded staging tests and cleanup of this run's synthetic objects,
not migrations of shared staging schemas, deployment, real-data deletion or paid
extraction. Keep all previously confirmed product decisions unchanged.

## Docker Desktop permission and local database

Luna may run `open -a Docker` to launch the installed Docker Desktop even if it is
currently closed, then poll `docker info` with a bounded startup wait. If ordinary
sandbox access fails, use the normal approval mechanism for the already-authorized
launch/daemon access; distinguish daemon unavailability from sandbox denial.
Do not force-kill Docker, factory-reset it, prune volumes, or stop unrelated
containers. Start only a dedicated disposable pgvector container with its own name,
local-only published port and synthetic credentials. Clean up only that container.
Avoid starting the entire Compose stack with staging secrets: its application
services run Alembic on startup and pass through `DATABASE_URL_DIRECT`.

## Next execution batches

1. **Batch 1b: repairs and real validation.** Parent owns Terraform root, CLI/log
   contract and integration. Delegate outbox outcome/drain regressions to
   fastapi-developer; database isolation and concurrent tests to postgres-pro or
   test-automator with separate files. Fix R07–R11 and DSN leakage, test staging as
   above, run local migrations/integration. Initialize/validate Terraform locally
   with backend disabled, preferably in a scratch copy; no apply or remote state
   mutation. Get source-based code-reviewer/security-auditor review.
2. **Batch 2: worker correctness (T03/early T09).** Freeze the generation/attempt/
   lease/deletion-fence schema before delegation. Implement completed duplicate
   no-op, renewable claims and fenced DB/vector/storage publication. Validate exact
   configured producer identities (API plus recovery dispatcher) and route audience
   before work. Preserve Cloud Run invocation checks. Run actual duplicate, crash,
   late-finalization, ingest/enhance overlap and deleted-account regressions.
3. **Batch 3: mobile state (T12 and T04 deadlines).** Separate mobile ownership:
   authoritative auth, account-scoped cache, stale-response rejection, multipart,
   cancellation and aligned end-to-end search deadlines. Verify switching accounts,
   expired sessions and late responses. Use Impeccable/native brief for UI changes.
4. **Batch 4 onward:** follow `LUNA_NEXT_IMPLEMENTATION_PLAN.md`: transactional
   lifetime import admission and progress, library/organization, resumable deletion,
   real mobile screens, native share/offline/push, then release evidence. Free bulk
   remains 150 Instagram + 100 pooled other apps once/account; premium 1,000 pooled
   once/account; full ingestion; manual shares separate. Resolve D01–D06 only when
   activating dependent policy. Build independent work meanwhile.

Use at most three children, no nested delegation, exclusive editor ownership and
parent integration. Update the master ledger after every reviewed batch. Do not
restart cache/citation work or upgrade Expo to avoid diagnosis.

## Evidence from this review

- Focused outbox/search/settings unit tests: 31 passed in the project virtualenv.
- Fresh-process logging probe without configure_logging: JSON parsing failed.
- Staging dotenv: required DB keys exist; no values emitted; no connection made.
- A Luna code-reviewer independently assessed supplied source excerpts and
  confirmed the findings; it did not run shell commands or inspect deployed state.
- The earlier 369-pass full-suite result is historical and does not close these
  untested paths. Previous no-blocker and DB-secret-only IAM claims are corrected.
- No separate Luna execution chat was identified; the revised start prompt is the
  handoff for the next Luna execution in this repository.

## Batch 1b execution update

The parent closed R07–R11 in source and isolated staging tests. The recovery Job
now receives `WORKER_BASE_URL`; dispatcher secret access is scoped to
`DATABASE_URL`; the CLI initializes structured logging; batch failures are
collected while siblings drain; stale successful finalization is counted as
skipped; and the PostgreSQL fixture observes true lock contention. The isolated
staging test passed **4/4** and a read-only metadata count confirmed no
`r01_outbox_%` schema remained. Local API suite: **372 passed, 21 skipped**.
Code-reviewer and security-auditor source packets accepted the repairs.

The two configured staging URLs were separately exercised with repeated
read-only asyncpg and SQLAlchemy transactions. Host/port metadata shows the
`DATABASE_URL` URL is Supavisor transaction mode (port 6543) and the variable
named `DATABASE_URL_DIRECT` is Supavisor session mode (port 5432), not a direct
`db.<project>.supabase.co` connection. No direct endpoint was present in the
staging file. A scratch `terraform init -backend=false` could not resolve
`registry.terraform.io`; formatting passed. Docker Desktop launched, but the
bounded daemon check timed out, so disposable local pgvector migration tests are
still open. No migrations, deployment, user data reads or deletion occurred.
