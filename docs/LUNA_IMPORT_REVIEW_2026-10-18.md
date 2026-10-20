# Luna admission and import recovery review — 2026-10-18

## Verdict

Keep T03, T05 and T06 partial. The durable intake direction is useful, but the
latest cleanup change introduces a data-loss path and import writes still lack
transactional execution fencing. Repair these before expanding import execution.
This review supersedes the earlier acceptance of the cleanup path for ambiguous
commit outcomes. It does not invalidate completed cache/citation work.

Scope: current working-tree admission service, intake, source cleanup, import
pipeline, worker wrapper, migration/test source, and execution handoffs. Existing
working changes are preserved. This is a code review, not hosted or native QA.

## Findings

### R12 / P1 — Ambiguous commit can delete a committed job's source

`apps/api/app/api/v1/imports.py:287–315` includes `session.commit()` inside a
`BaseException` handler that rolls back and deletes the staged ZIP. A connection
loss or cancellation during commit does not prove the server rolled back. If the
server committed before its acknowledgement was lost, the reservation, Import and
outbox survive while this code deletes their source. Reupload now excludes the
already-admitted keys, so that is not a recovery mechanism.

Offline fault injection reproduced: simulated committed transaction, HTTP 500,
and source deletion. This models a distributed failure; it is not a real database
connection-drop experiment. Existing commit-failure tests simulate only a definite
failure and therefore miss this distinction.

Required fix: distinguish definite pre-commit failure from unknown outcome.
Retain source on uncertainty and reconcile durable identity through a fresh
connection; failure to reconcile must preserve source. Prefer a durable staging
lifecycle with a bounded orphan reconciler. Never delete based solely on an
exception from commit or successful subsequent rollback. Test lost commit ACK,
cancellation during commit, database unavailable during reconciliation, and a
proven orphan. Cleanup must be scoped and must not race a still-committing intake.

### R13 / P1 — Import writes do not enforce the execution lease/deletion fence

`apps/api/app/tasks/worker_entry.py:305–348,386` claims/heartbeats a target intent,
but calls `process_stored_import` without the token/generation. In
`apps/api/app/services/imports/ig_pipeline.py:114–150,248–294`, chunk item/outbox
writes, import counters and failure publication do not check the current lease or
`Profile.deleted_at` in their transaction. A final completion check cannot undo
writes committed after takeover/deletion. Cancellation on the next heartbeat is
not an atomic fence.

Source-confirmed; real takeover/deletion interleavings were not run in this review.
Pass an execution context and validate owner, live profile, generation, token and
lease in every mutation transaction. Reuse the item-worker lock-order discipline.
Fence error paths too. Test a paused old worker resumed after reclaim, and profile
soft deletion between chunks. The old worker must write/dispatch nothing further.

### R14 / P2 — Retry overwrites persisted progress with this attempt's count

`ig_pipeline.py:244,274,283–294` starts `queued=0` on every attempt and assigns it
to `Import.full_queued`. Previously committed items become conflicts/duplicates on
retry and are not included. Offline reproduction: two items queued, replay of the
same source, persisted count falls from **2 to 0**. A crash after one committed
chunk produces the same undercount. This is known unfinished T06 behavior, now
reproduced; do not accept the current counter as durable progress.

Derive counts from durable import-entry/item membership or guarded transitions.
Do not patch with `old + current` or `max` alone: those cannot distinguish partial
replay, shared items, completion or duplicates. Test chunk-boundary crashes,
repeated completion events and mixed terminal outcomes.

### R15 / P1 activation gate — Archive bounds remain absent

`imports.py:122` buffers the entire upload before testing compressed size;
`ig_parser.py:121–139` calls unbounded member `read()` and parses synchronously.
Small compressed archives can expand far beyond the request limit. This is a
pre-existing T07 gap, not a regression from the last helper change. Production
admission must remain disabled until streaming/request limits, member/aggregate
expanded-byte limits, entry-count/ratio limits and bounded parser execution are
verified with small synthetic bomb fixtures. No large bomb was executed here.

### R16 / P2 — Account lock spans external Storage upload

The admission service obtains the Profile row lock before returning;
`imports.py:193–295` holds that transaction through ZIP upload and commit. Storage
latency therefore blocks other operations using the same Profile lock, including
worker heartbeats and deletion. Stage/validate the immutable source before the
short account admission transaction, then reconcile abandoned staging separately.
Test a stalled upload while another owner-scoped operation proceeds; preserve
last-slot serialization at the final admission transaction.

## What the changes do establish

- Intake has one staged-source/outbox path for small and large imports.
- Newly admitted keys bind worker parsing to the admitted subset; replaying an
  existing lifetime reservation cannot create another import job through intake.
- Free bucket calculations, server-owned tier selection and inactive policy/history
  configuration are explicit. Premium entitlement is not implemented.
- Source-key validation and empty-key rejection exist.

No durable import-entry membership, completion attribution, collection materialization,
request-idempotency/result rediscovery or bounded failed-import retry is complete.
Rejecting a replay with 409 prevents new work but does not return the original
accepted import ID after response loss. Implement discovery/reuse in T06/T07.

## Validation and review evidence

- Fresh API suite with both database URLs explicitly synthetic/local and
  `TEST_DATABASE_URL` empty: **410 passed, 29 skipped, 16 warnings** (6.81s).
- `docs/review_evidence/luna_import_probe.py` reproduced R12 and R14 offline using
  existing test fakes. Run from `apps/api` with `.venv/bin/python` and the relative
  path `../../docs/review_evidence/luna_import_probe.py`. It intentionally asserts
  observed defects; convert its scenarios to safe regression expectations during
  implementation. It does not contact PostgreSQL, Storage or paid providers.
- A separate `code-reviewer` reviewed exact source excerpts without shell/edit
  access and independently confirmed R12–R14. Parent inspected the files and ran
  the checks. A reviewer summary is not a substitute for database concurrency tests.
- No staging test, Docker launch, migration apply, Terraform init, deployment or
  device rendering was performed in this review. Earlier runs recorded different
  environment outcomes; do not flatten historical success and later DNS/socket
  failure into a claim about current availability. `DATABASE_URL_DIRECT` was
  previously identified as a session pooler, not proof of a direct host connection.

## Handoff corrections

`LUNA_START_PROMPT.md` still instructed Batch 2 and cited 372 tests; it is replaced
with the new priority sequence. The previous review's cleanup acceptance covered
ordinary rollback only and is superseded by R12. D01 upgrade/downgrade remains an
owner decision: a proposed carry-forward implementation is not evidence that the
owner approved it. T03 source completion is reopened for import transaction fences.

## Batch A implementation update (2026-10-18)

The working tree now addresses the core R12/R13/R16 defects. Unknown commits
reconcile using a fresh READ COMMITTED transaction that locks Profile, Import and
the exact outbox row; only a matching committed identity returns accepted, and
only both durable rows absent while the Profile exists permits deleting this
request's staged object. Errors, partial state and canceled requests retain their
failure/cancellation semantics. Import writes receive the exact worker execution
lease and check it inside each mutation transaction. Storage upload now precedes
the short account-admission lock.

Evidence: focused import/worker/delete/backfill tests passed (83); full API suite
passed (424, 31 skipped); isolated local PostgreSQL 17 lease/outbox/admission and
commit-reconciliation tests passed (14). Reviewers found no new confirmed issue in
the fence/reconciler from the source packet. They identified a remaining crash
window after Storage upload and before admission, which can leave an orphan ZIP;
bounded staging/orphan cleanup is still required. A dedicated concurrent account
deletion versus reconciliation regression is also open. Security review confirms
that full-file upload buffering and missing ZIP expansion/member/ratio bounds block
public import activation (R15); bucket privacy and server-derived platform
classification still need independent evidence. Batch A and T03/T06 remain partial.
