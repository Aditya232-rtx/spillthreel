# Luna next implementation handoff — app logic completion

> **Review update (2026-10-18):** Read `LUNA_IMPORT_REVIEW_2026-10-18.md` first.
> The fresh suite passes 410 tests with 29 skips, but review reproduced destructive
> cleanup after an ambiguous commit and retry counter loss. Import chunk writes
> also lack execution/deletion fences. Start Batch A below; T03/T05/T06 remain
> partial. Historical Batch 1–3 descriptions are evidence, not restart instructions.

**Prepared:** 2026-10-18, after reviewing the first implementation batch.
**Entry point:** read AGENTS.md, the master `LUNA_IMPLEMENTATION_PLAN.md`, then
`LUNA_IMPORT_REVIEW_2026-10-18.md` and this document; earlier reviews remain historical evidence. This handoff changes execution
order and makes the next batches concrete; it does not replace the master task
specifications or confirm the proposed D01–D06 product policies. HANDOFF.md remains
historical. Preserve the current working tree, including the cache fixes.

## Outcome and scope

Complete a real product journey: authenticate → manually save/share → safely ingest
→ recall from evidence → open/organize/delete → preview and admit a bounded import
→ observe durable completion → recover after interruption or account switching.
Every visible control must have real persisted behavior or an explicit unavailable
state. Do not call the app complete while fake completion, dead controls, unsafe
cross-account state or unverified release-critical native/cloud behavior remains.

This is an execution plan for Luna, not authorization to deploy, change live data,
run paid extraction or publish a release. Do not restart T01 or rewrite successful
work. Staging read-only checks were attempted through the pooler and direct endpoints; both failed DNS resolution before connection, and no staging writes occurred. The latest priorities are R12–R16 below; earlier R01–R11 repairs are not to be restarted without new evidence. Keep confirmed limits: free 150 Instagram
+ 100 other supported apps pooled once/account; premium 1,000 pooled once/account;
full ingestion for every admitted imported media item; manual shares are separate.

## Delegation and integration

Actually invoke installed specialists. Use their supported custom profiles, or
read the profile and pass its developer instructions to a supported spawn call.
Use Luna where explicitly selectable; do not claim the parent model changed unless
it did. At most three concurrent children; no nested delegation. Parent owns all
shared contracts, model/migration sequencing, manifests, lockfiles and integration.
Give each editor exclusive files. Do not run two editors on worker_entry.py, models.py,
root mobile providers, shared API types or the same test module.

Each batch ends with focused checks, relevant suite, code-reviewer and (for database,
auth, imports, quota, deletion or IAM) security-auditor review. Reviewers do not run
shell or edit. If they cannot read files, give them a labeled source/diff packet
and record that limitation. Fix actionable findings before declaring the batch
accepted. Record local implementation, integration, native and hosted evidence
separately. An unavailable local DB does not close a database gate.

## Current execution sequence — supersedes historical batch ordering below

Keep master task IDs. Complete and review one bounded batch before starting the
next shared-file batch. Only the separate mobile test/tooling lane may run in
parallel. Parent owns models, migrations, shared contracts, lockfiles and integration.

**Execution update (2026-10-18):** Batch A's R12/R13/R16 source path now includes
a durable pre-upload staging marker, owner→marker lock order, irreversible
cleanup_pending state, bounded tokenized Storage sweep, settling grace, and
one-shot recovery-runner integration. Unknown commit outcomes retain the ZIP.
Focused import/recovery/runner tests: 56 passed. Full API suite: 429 passed, 31
skipped. Fifteen local PostgreSQL 17 concurrency/reconciliation tests pass; the
new deletion race confirms the reconciler waits behind the exact deleting backend
and returns UNKNOWN after its commit. Offline Alembic generation and diff checks
pass. Code-reviewer found no Batch A blocker after retracting a misread lock-order
finding; security review keeps R15 bounded upload/ZIP expansion as a high-severity
release blocker. Batch A source repair is implemented, but hosted scheduler, staging
DB role/RLS, Storage behavior, bucket privacy and deployed IAM remain unverified.
Do not mark T03/T06 complete: R14 membership/progress work starts in Batch B.

**Batch B progress update (2026-10-18):** Durable ImportEntry and occurrence
membership, owner-scoped request-key/body-fingerprint idempotency, persisted
admitted/already-reserved key binding, and progress derived from durable rows are
implemented locally. Parent integrated Item completion mirroring and a
`finalize_manifest` boundary: worker callbacks cannot close an Import while the
archive is still adding chunks; the final fenced write derives complete/failed/
indexing after all entries exist. Entry and Import lock order now matches the
import chunk path. A second review caught the admitted-candidate conversion failure
and the race where a duplicate import sees a reservation before its Item exists;
these now record an `item_unrepresentable` terminal failure or a retryable
`reserved_item_pending` membership that links by owner+canonical URL on the
original Item's fenced transition. The first code-review packet correctly found
the prior finalizer still forced `indexing`; exact source was supplied for all
remediations. The reviewer confirmed the finalizer, failure outcome, lock ordering
and delayed-link fix, including the Profile-first serialization that closes the
callback-before-entry interleaving without requiring a separate test. Security
review confirmed exact payload/deletion fences from source packets and raised a conditional
P2: repeated new idempotency keys can create membership-only imports for the same
already-reserved items. No quota is recharged or ingest job minted, but request
rate/source cleanup bounds are still needed. Unit suite: 433 passed, 12 skipped;
local PG17 worker/lease suite: 12 passed. Ruff is not installed; `git diff --check`
passed. This is partial Batch B, not acceptance: same-key concurrent HTTP
idempotency, cross-owner FK probes, collection materialization, import discovery,
bounded retry, and repeated already-reserved request controls remain. T06 stays
partial. R15 remains a public-import release blocker.

**Staging availability recheck (2026-10-18):** Both `DATABASE_URL` and the
misleadingly named `DATABASE_URL_DIRECT` in `apps/api/.env.staging` connected for
read-only metadata queries. Host/port inspection identifies both as session
poolers. With prepared-statement caches disabled, both report Alembic revision
`20261009_0001_rls_hardening` and only `profiles`, `items`, and `imports` among
the checked `task_outbox`, `import_entries`, and `import_staging` tables. An
initial cached metadata query returned SQLSTATE 42P05; the same checks pass with
both asyncpg caches disabled, consistent with the pooler workaround. No staging
write, migration, role probe or cleanup was performed. Do not apply Batch A/B
migrations to staging without the separate migration authorization; verify the
API/staging role and tenant ACLs in a unique isolated schema first.

**Forward progress (2026-10-18):** Batch B now has durable folder mapping,
owner-scoped import discovery/entry status and bounded failed-entry retry. Synthetic
local PG17 worker/outbox/admission concurrency tests pass (17 total); migrations
0006/0007 and cross-owner/check probes were previously applied on a disposable
local database. Batch C now streams compressed uploads to a capped temp file,
rejects hostile ZIP structure/expansion, bounds parser concurrency after a request
times out, and caps the full multipart body before Starlette can spool it. Hostile
archive/upload/request-limit tests pass. Security review found no archive blocker
and asks us to verify the Cloud Run/proxy request-body cap. Native device and cloud
behavior remain unproven. T08 stable item keyset pagination is implemented;
T10 durable deletion checkpoints and tombstone fencing are locally verified,
with hosted and retention-policy gates remaining. SDK 57 Jest setup is installed; six request/
auth tests and TypeScript checks pass locally. Batch C preview/selection,
commit-selected, capacity controls, and D05/D06 gates remain; public import
activation stays disabled until those dependencies are resolved.

**Forward progress (2026-10-20):** Local PostgreSQL 17 is running at its default
port. The owner authorized direct installation of pgvector if missing. The
Homebrew formula would have installed PostgreSQL 18, so it was not used; pgvector
0.8.7 was instead compiled against the installed PG17 `pg_config`. Full Alembic
head now applies in a disposable database, including the new forward migration
that fences known own-user RLS policies against deleted profiles. The isolated
RLS regression passes, as do 17 worker/outbox/admission concurrency checks and
the 471-test API unit suite (12 skips). Mobile Jest has 7 passing tests and
TypeScript checks pass. T08/T09/T14 made bounded local progress, but remain
partial pending collections/counts, hosted grants/policies, and device checks.
T15 is next: finish preview/selection and allowance UX after contract freeze.
Preview/selection, upstream request caps, capacity, and D05/D06 remain gated. Docker Desktop did not become
responsive; no container, staging write, migration deployment, or hosted change
was performed.

**Batch 6/T15 continuation (2026-10-20):** The ZIP import screen no longer fakes
completion. It uses the existing authenticated multipart intake and durable import
status/list APIs, persists a per-account retry identity, recovers status by exact
ID, lets users explicitly choose prior imports after an uncertain POST, polls only
the selected import while foregrounded, and fences stale async results on account
switch. Reselection after an evicted picker-cache URI retains the idempotency key;
a conflicting payload clears that key so a deliberate new request can start. The
unapproved retention-period claim was replaced with an explicit statement that the
period is not finalized. `expo-react-native-expert` implemented the screen and
`test-automator` added screen-level recovery regressions. `code-reviewer` and
`security-auditor` reviewed source excerpts and found no remaining concrete issue.
Backend fingerprint verification confirms same-key/same-archive recovery and a
409 for changed bytes. Mobile Jest **11 passed**, `tsc --noEmit` and diff checks
pass. Native render evidence remains open.
T15 remains partial: preview/selection/admission UX and capacity controls need
backend contracts; D05/D06 remain proposals; native picker/screen-reader behavior
is not verified.

**Batch 6/T16 continuation (2026-10-20):** Ask/detail now has citation-to-card
scroll/highlight, retry-in-place without a duplicate user bubble, account-keyed
conversation history, owner checks around async search/detail work, iOS keyboard
avoidance, safe-area layout, larger body type, 48dp actions, and clearer no-hit,
network, retry, deleted/unavailable and original-link states. Parent added a
render-time owner mismatch guard on item detail to hide old-account data before
the new effect runs, plus ink labels on coral buttons per contrast guidance.
`expo-react-native-expert` owned the two screens; source-packet code/security
reviews reported no concrete blocker but had no direct filesystem reader. Jest
11/11, TypeScript and diff checks pass. T16 remains partial pending native
keyboard, citation-scroll, VoiceOver/TalkBack, large-text, offline and tap-target
verification.

**Batch 6/T17 continuation (2026-10-20):** Settings now reads `/v1/me` and
shows actual profile name/email, then reads `/v1/me/import-allowance` and shows
the API-reported free Instagram and pooled-other remaining amounts. Unconfigured
or unsupported policy is reported as unavailable; no premium or purchase state
is inferred. Existing deletion/sign-out retry remains. `expo-react-native-expert`
owned the Settings screen. `code-reviewer` withdrew a tentative request to show
premium counts after verifying the current endpoint hardcodes FREE and owner
keeps D01/D04 entitlement activation gated; `security-auditor` found no concrete
issue. Mobile Jest 11/11, TypeScript and diff checks pass. T17 remains partial
pending trusted premium entitlement after owner decisions, durable deletion
checkpoints, and native verification.

**T10 deletion checkpoint continuation (2026-10-20):** Account deletion now
commits the soft-delete fence, no-FK step checkpoint, and durable outbox intent
together. The worker resumes media storage, export storage, vectors, profile
cascade, then Auth, validating the current generation and lease token before
each step and after each external success. Postgres transactions close before
provider calls. Auth 404 is idempotent; Profile deletion and transition to Auth
are atomic. A `complete` tombstone remains and auth middleware denies profile
recreation for a still-valid JWT or a lost outbox-completion acknowledgment.
The local PG17 tests prove migrations, client-role denial/service-role grants,
tombstone survival after profile deletion, and a takeover during an external
step (19 tests across isolated suites). API units pass 476 with 12 skips.
Code review found no execution-path blocker; security review identifies the
UUID tombstone retention as a D06 decision. Do not remove it before a bounded
retention policy preserves the stale-token fence. Hosted Auth/Storage failures,
IAM/RLS configuration, orphan sweeper and mobile deletion recovery remain
unverified. No staging or production database was touched.

### Batch A — Source survival and import execution fencing (R12, R13, R16; T03/T06/T07)

**Delegate:** fastapi-developer owns imports.py, recovery.py and new intake failure
regressions. Parent initially owns the worker execution-context contract, then
assigns worker_entry.py/ig_pipeline.py and their tests to the same specialist in a
second sequential subtask. postgres-pro reviews transaction/lock ordering from
source packets. No competing editors on these files.

1. Reproduce R12 using the committed-then-raised scenario in the review probe.
   Stop deleting source on unknown commit outcome. Define durable intake identity,
   staging ownership, transaction state, reconciliation result and safe cleanup
   eligibility before implementing compensating deletion. Save scalar identities
   before rollback so ORM expiry cannot break recovery/logging.
2. Stage and validate outside the Profile lock; recheck account deletion and quota
   in a short final transaction. Source identity must be immutable and bound to
   the accepted payload. A stalled upload must not block worker heartbeats.
3. Pass a target execution context containing owner/target/outbox/generation/token
   into import processing. Check it under consistent locks in every chunk and
   terminal/failure write transaction; reject soft-deleted owners. Heartbeats
   remain liveness signals, not write authorization. Never keep DB locks while
   downloading, parsing or calling providers.
4. Add a bounded reconciliation interface for uncertain intake outcomes and
   orphan staging. Unknown database state means retain/retry. Prove an object
   unreferenced and outside active staging before scoped cleanup. The current
   implementation persists a marker before upload, uses a five-minute settle
   grace for uncertain uploads, and removes the marker atomically with admission.
   Build the state machine using synthetic retention inputs; D06 still gates real
   expiry policy.

**Accept only with:** definite rollback versus committed/lost-ACK versus unknown
outcome tests; cancellation at commit; cleanup retry; deletion/takeover between
chunks; stale failure handler; blocked upload with independent account operation.
Run the SQL interleavings on disposable Postgres or the authorized isolated staging
schema. No real database means source repair may complete, database acceptance may
not. Keep the original source available whenever commit could have succeeded.

**Current evidence:** source tests and the bounded staging sweep are implemented;
local PostgreSQL 17 has verified takeover, deletion fencing, lost-commit
reconciliation, and a synchronized account-delete/reconciliation lock race. No
staging migration or hosted schedule was applied. R15 remains a separate release
blocker and does not authorize loosening archive safety requirements.

### Batch B — Durable membership, progress, discovery and retry (R14; T05/T06)

**Delegate:** postgres-pro designs/reviews schema; parent adds the next Alembic
revision after checking migration history. fastapi-developer owns membership,
collection and completion services/tests once interfaces freeze. Parent integrates
item-worker completion hooks; do not edit those concurrently with a specialist.

1. Freeze import-entry identity, owner consistency, reservation reference, item
   reference, source/folder provenance, duplicate/eligibility result and processing
   outcome. Distinguish raw parsed occurrences, selected unique media, charged
   reservations and imported library items. Lifetime reservation survives item
   deletion; entry membership must not introduce automatic quota refunds.
2. Item insertion, entry association and ingest intent commit atomically under
   Batch A's fence. Reconcile an already-owned item into folder membership without
   minting an extraction job or charging it again under explicit D02 fixtures.
3. Guard completion/failure transitions and derive import counters from persisted
   entries. Preserve totals after chunk-boundary crashes; duplicate completion
   events are no-ops. A mixed ready/failed import reaches a meaningful terminal
   state. All-duplicate, zero-eligible and audio-only cases need honest outcomes.
4. Materialize collections and idempotent audio identity under D03 fixtures.
   Preserve multiple folders for one item and timestamps under the selected rule.
5. Add owner-scoped import list/resume and paginated entry status. Define request
   idempotency key plus payload/version fingerprint: same request returns its
   original accepted import; conflicting body returns 409. A response lost after
   commit must be rediscoverable. Existing reservations cannot mint fresh jobs.
6. Explicit failed-work retry reuses membership/reservation and creates only a
   bounded authorized execution generation. Completed duplicates do no provider
   work. Never reset reservation history as a retry mechanism.

**Acceptance:** crash after first of multiple chunks then replay; 2 queued never
becomes 0; completion repeated twice; same item in two folders; two owners using
same URL; lost response retry; conflicting idempotency key; stale generation; soft
and hard deletion; no extra reservation or paid work on completed/replayed jobs.

### Batch C — Safe preview, admission and capacity (R15; T07/T11)

**Delegate:** fastapi-developer owns parser/upload/preview; test-automator may own
separate hostile-input fixtures after contract freeze. Parent owns limit config.

**Progress:** compressed upload streaming, an ASGI multipart cap, and parser
resource/concurrency ceilings are implemented with synthetic regressions. Source
review found no code blocker; upstream Cloud Run/proxy request caps and strict parser
thread termination remain operational concerns. Continue the unfinished
preview/selection and capacity items below.

Stream compressed bytes under a cap; enforce request limits and actual decompressed
member/aggregate bytes, member count, compression ratio and parser resource bounds.
Reject encrypted/nested/dangerous entries and malformed formats with stable errors.
Avoid blocking the event loop with archive/HTML parsing. Stage privately, expose
paginated preview, and commit a selected version idempotently. Preview performs no
paid extraction. Bind selections to the same source and eligibility policy; recheck
remaining lifetime allowance at commit. Only advertise verified export adapters.
Add per-owner active-work limits, global provider concurrency, bounded retries and
observable pending/dead-letter states before enabling large bulk work. D05/D06 and
operating-spend decisions gate only dependent behavior; use explicit test fixtures.

**Acceptance:** small synthetic compression bombs, misleading headers, partial
uploads, cancellation, stale preview selection, duplicate commit, last-slot race,
restart recovery and queue outage. No 250/1,000-item paid load test is authorized.

### Parallel lane M — Make existing mobile foundations verifiable (T12)

**Delegate:** expo-react-native-expert owns mobile regression tests and shared
API/auth/store fixes only. Parent owns package.json and lockfile changes requested
by the specialist. Inspect the existing package manager and repair the install;
do not upgrade Expo. Establish a compatible test runner, execute existing API
regressions and add account-switch/in-flight response tests. Verify 20s server/25s
client deadlines, multipart uploads, cancellation and auth reentry. Render owner
changes and unavailable-policy states. Record native gates separately. This lane
may overlap Batch A/B only when shared API contracts are frozen and no files overlap.

### Batch D — Complete library, account and deletion (T08–T11)

After B's membership contract, implement stable cursor lists/counts, owned
collection/category mutations, renewable media access and resumable progress.
Extend deletion into a durable storage/vector/DB/auth cleanup workflow with retry
checkpoints and late-result rejection. Exports and privacy controls must report
real durable state; D06 governs archive expiry. Verify owner-role isolation, equal
pagination timestamps, expired URLs, failed external auth deletion and retained
JWTs. Follow historical Batch 5's detailed acceptance matrix.

### Batch E — Connect native journeys and verify them (T13–T19)

Use Impeccable and DESIGN_EXECUTION_BRIEF.md. Implement semantic foundations,
readable body text, safe-area/keyboard/accessibility, then real Library, Import,
Ask/detail and Account actions. Replace fake progress/data with the settled API
states. Connect manual shares/offline recovery and foreground/push transitions.
An unavailable policy/provider must have explicit UI, not fake success. Verify
small/large phones, large text, account switching, screen reader, native shares,
keyboard and notification permissions on actual supported runtimes.

### Batch F — Release evidence (T20/T21)

Run disposable local Alembic upgrades and role/concurrency tests, then authorized
staging metadata/isolated-schema probes. Terraform init/validate uses a scratch
copy with backend disabled; code is not evidence of a running scheduler. Hosted
IAM/worker/recovery activation and release remain separately authorized actions.
Produce one evidence matrix of pass/fail/skipped by platform/environment and clear
owner decisions. No global completion percentage substitutes for acceptance.

### Shared execution rules

- Read credentials into memory only; never print them or put DSNs in commands.
  Do not run the whole integration suite against staging: existing fixtures migrate
  and delete fixed identities. Local full-suite tests must set both DB URLs local.
- Recheck Docker installation/daemon safely when needed. The owner authorized
  `open -a Docker` and normal escalation for daemon access; do not classify sandbox
  permission denial as an absent daemon. Do not prune/reset unrelated Docker state.
- Verify actual endpoint type, not the variable name: the earlier `_DIRECT` value
  was a session pooler. Do not claim direct-host coverage without one configured.
- At most three children and no nested delegation. Use installed profiles and Luna
  where selectable. Reviewers receive exact current excerpts/diffs, not only an
  author's change summary; reviewers do not edit or run shell commands.
- After each batch obtain code-reviewer and security-auditor feedback for these
  sensitive paths, resolve findings, and record actual commands/skips in the master
  ledger. Preserve D01–D06 as proposals until the owner answers; the runbook's
  operator signoff is a suggested evidence process, not a new owner approval rule.

## Historical batches and remaining acceptance detail

The sections below preserve earlier scope. They do not override Batch A–F above.

## Batch 1 — Repair and finish dispatch contracts (R01–R04; T02/T04/T20a)

**Historical repair batch.** Preserve the frozen delivery semantics; only reopen with new evidence.

**Delegate A: fastapi-developer**, exclusive ownership of tasks/outbox.py,
tasks/enqueue.py, tasks/local_dispatcher.py, new local consumer entry point and
tests/unit/test_task_outbox.py plus a new dispatch integration test. Parent owns
any schema change and reviews local lifecycle integration.

1. Make success and failure finalization an atomic ownership-checked DB operation.
   Ignore stale completions without clearing another claim. Keep claims short;
   no DB transaction spans network work. Cover a reclaim between read and mutation.
2. Separate local acceptance from media execution. Prefer an explicit local worker
   process polling durable intents, documented alongside local API startup. If a
   process task is used as a prompt hint, recovery must still work after it dies.
   Never mark a local job durably delivered merely because it entered an in-memory
   queue. Define execution acknowledgement, retries and poison payload handling.
3. Bound prompt-dispatch latency and Cloud Tasks RPC retry/deadline behavior. A
   committed request returns accepted even during queue outage. Timeout of an
   awaiting coroutine does not stop a blocking thread; configure transport timeout
   and retry bounds as well. Preserve named-task identity for lost acknowledgements.
4. Add a bounded runner command for due intents, nonzero failure exit/observable
   failure counts, orderly cancellation and oldest-pending visibility. One bad job
   must not silently discard the rest of a batch. Do not log task payloads/secrets.

**Delegate B: llm-architect**, exclusive ownership of search/pipeline.py and its
unit tests. Fix per-tag remaining-budget consumption; assert total field budget
for R03's two-hit case, exact boundaries, empty fields and multi-hit truncation.
Preserve evidence/citation ordering and existing fallback behavior. Record which
bound covers evidence versus serialized prompt overhead; do not relabel chars as tokens.

**Delegate C after runner interface freezes: terraform-engineer**, exclusive
ownership of the new runner's infra definitions and operations runbook. Configure
a Cloud Run Job + scheduler trigger (or reviewed equivalent), explicit service
identity, DB/queue access, bounded work and overlap behavior. Parent integrates
shared Terraform roots and CI. This T20a slice does not wait for all of T11/T20.
Require an explicit overlap/concurrency limit and a last-success/oldest-pending
health signal, not only scheduler creation. No apply/deploy. If Terraform/provider
validation requires unavailable packages,
record it and leave hosted activation pending; do not claim recovery is deployed.

**Acceptance:** response returns before a blocked synthetic media worker; restart
recovers committed local intent; rollback exposes no job; queue error remains
pending with backoff; AlreadyExists for the same identity succeeds; stale tokens
cannot write either terminal branch; cancelled dispatcher is recoverable; bounded
batch runs without a request. Real PostgreSQL concurrency tests are required for
lease correctness. All direct dispatch branches receive regression coverage.

## Batch 2 — Worker fencing and identity (T03 + early T09)

**Execution result:** source work is implemented and independently reviewed.
The isolated staging worker-claim module passed 7/7 after a test-only unique
generation collision was corrected; post-run metadata confirmed zero temporary
schemas. The full API suite passed 388 tests with 28 skips. Local migration and
deployed Cloud Run identity/queue behavior remain open, so T03 is not release-
complete. See the master ledger for the detailed execution record.

Batch 1b source repair and the real staging lease-interleaving test passed. The
remaining T02/T20 gates are local migrations on disposable pgvector PostgreSQL,
Terraform provider validation, and hosted Job/IAM execution. Docker Desktop launch
was authorized and attempted, but `docker info` timed out; Terraform init in a
scratch copy failed because registry DNS was unavailable. Continue Batch 2 without
claiming those environment gates are closed.

Depends on Batch 1's delivery contract. Delegate the ingestion service/worker
handlers to fastapi-developer; postgres-pro reviews the parent's proposed schema
and transactions. llm-architect may review the paid-work boundaries read-only.
No concurrent editor touches worker_entry.py or ingestion state transitions.
Freeze the parent-owned processing fields, transaction boundaries and shared
payload/acknowledgement contract before dependent worker implementation.

- Introduce explicit generation and execution-attempt/lease fields under a single
  parent-owned Alembic revision. Keep transport delivery state separate from
  worker completion. Document legacy payload behavior and deployment ordering:
  new code must handle pre-existing jobs without enabling stale writes. Do not
  default every missing generation to the current one without assessing replay.
- Pass validated job generation through both HTTP and local dispatch. Atomically
  claim the current generation and return before providers for already-completed
  or stale jobs. Serialize explicit retries; queued duplicates reconcile current
  intent. Coordinate ingest and enhance so they cannot overwrite one another.
- Renew leases during bounded long operations. Fence database completion, import
  counters, vector writes and thumbnail publication against lease/generation and
  account-deletion state. Use attempt-scoped temporary objects plus guarded final
  publication/cleanup where external storage cannot perform a DB compare-and-set.
- Persist safe stage checkpoints, retry classification and terminal failure. A
  crash between provider success and checkpoint may repeat paid work; document
  the limit and cap retries, rather than promising exactly-once billing.
- Bind OIDC identity to the configured task service account and exact intended
  route audience while preserving validated proxy-scheme handling. Fail closed
  on missing config outside explicit local dev. Tests mock token verification:
  correct identity succeeds; another valid Google SA, wrong audience/issuer,
  unverified identity and missing token fail. IAM verification remains separate.
- Establish the minimal durable account-deletion fence now; the full purge saga
  comes in Batch 5. All new claims and late publications must consult that fence.

**Acceptance:** concurrent duplicate deliveries produce one active paid attempt;
completed duplicate invokes zero providers; lease expiry recovers; stale completion
and ingest/enhance overlap cannot overwrite newer work; deleted account cannot be
resurrected; HTTP/local payload semantics agree. Use actual DB interleavings plus
mock providers. Include retry after Cloud Tasks name retention and generation bump.

## Batch 3 — Shared mobile state and request contracts (T12 + T04 deadline)

The source batch used `expo-react-native-expert` with exclusive API/auth ownership;
the parent owned backend search timeout and account-scoped chat/category persistence.
No T13 editor should overlap `app/_layout.tsx` or the shared providers. This is
state/API work, not a redesign.

- One authoritative auth subscription/provider and profile synchronization flow.
  Test repeated consumers and account switching; do not infer a deadlock solely
  from an async callback. Keep async profile updates outside problematic auth
  callbacks when confirmed by the installed client behavior.
- Scope query keys, categories, drafts and pending saves to user ID. Clear or
  segregate them on logout; reject late responses and old-account 401 handlers
  that would corrupt/sign out the new session. Capture session identity per request.
- Extend src/lib/api.ts for multipart without JSON serialization/content-type,
  structured error codes, request-specific deadlines, explicit cancellation and
  stable idempotency keys. Implement a timer fallback on native runtimes lacking
  AbortSignal.timeout; clean event listeners/timers on completion.
- Set a documented server total search deadline including hydration/overhead and
  a slightly larger mobile search timeout. Do not increase all requests without
  bounds. Add fake-clock tests for late-valid answers, user cancellation, timeout,
  old-account responses and expired auth. Preserve drafts on recoverable failures.

**Acceptance:** one session source, no duplicate profile writes; no cross-account
cache/draft/pending request leakage; real multipart request shape; timeout and
cancellation distinguishable. TypeScript alone does not prove these behaviors.

**Execution result:** source work landed for a singleton auth store, owner-scoped
chat/category state, multipart and request API contracts, idempotency headers, and
bounded cancellation. Backend search now caps the whole pipeline at 20s and mobile
search at 25s. Code-reviewer and security-auditor packets found no remaining
source blocker after fixes for cross-account 401 and stale-chat races. Mobile tests
and TypeScript validation could not run because no test runner is configured and
the installed TypeScript package is missing `lib/_tsc.js`; Babel syntax parsing and
`git diff --check` passed. Keep native behavior and reauthentication UX gates open.

## Batch 4 — Import admission, membership and safe parsing (T05–T07, early T11)

Depends on worker contracts. Build backend interfaces before the import UI. Split
this large batch into reviewable 4A/4B/4C sub-batches; never land it as one opaque diff.

**4A schema and allowance — partial source implementation:** parent owns Alembic/model changes; postgres-pro owned the admission service and isolated DB test source. Read `LUNA_IMPLEMENTATION_PLAN.md` for actual test/staging results. The route rejects additive `separate_allowance` pending explicit owner approval; history remains disabled until the reconciliation runbook evidence is retained. Continue with T06 below.
Persist account usage/reservations, server-owned entitlement, import entries and
item attribution. Enforce owner consistency with composite constraints where needed.
Implement the confirmed numeric caps and manual-share exemption now; no new owner
approval is needed for those rules. Pending policies gate only their dependent
production behavior, not cap calculations or durable schema implementation.
Account lock or atomic admission operation enforces total and per-source buckets.
Lifetime usage survives deletion, reinstall and month/ZIP changes. Share saves never
call bulk admission. Replays reference the same reservation; selection changes use
version/conflict checks. Replaying the same admission never charges twice. Define
existing manually saved items and duplicate URLs across separate imports explicitly
under D02 fixtures; do not silently convert its proposed exemption into a confirmed
policy. Keep upgrade/refund/audio rules as explicit policy inputs;
confirm D01–D04 before activation, not before writing isolated fixtures/interfaces.
Inventory existing accounts before historical reconciliation; do not infer lifetime
usage from mutable import counters or silently grant/consume historical allowance.
Follow `runbooks/BULK_IMPORT_HISTORY_RECONCILIATION.md` and retain operator signoff.

**4B preview and safe input:** fastapi-developer owns routes/parser/adapters and
focused tests. Stream with compressed byte limits; cap archive entry count,
per-entry and aggregate expanded bytes, compression ratio and parser resources;
reject dangerous/unsupported members, malformed layouts and symlinks. Do not trust
ZIP headers alone. Stage securely, parse asynchronously, expose paginated preview
and explicit selection/admission with idempotency key/version. Archive preview does
not trigger paid ingestion. Confirm supported export formats with real sanitized
fixtures; unsupported native exports stay unavailable (D05), not silently treated
as supported because single-URL extraction works.

**4C completion and limits (remaining T06 work):** persist each entry's admitted/duplicate/rejected/failed/
completed outcome and collection membership. Small imports now use the same durable staged-source/outbox path as large imports; crash-before-processing is recoverable from the outbox once a consumer runs. Keep this task partial until entry membership, completion attribution and recovery have DB-backed tests. Commit admission + item + outbox
atomically. Derive aggregate counters from durable membership or guarded exactly-once
transitions. Retry after a partially committed chunk must not shrink totals or
charge again. Materialize real collections and attach existing owned items without
duplicating them. Add per-account active-work limits, global provider concurrency,
bounded retries and durable stage-cost records before activating 250/1,000-item
processing. Operating spend thresholds require an explicit configured budget (T11).

**Acceptance matrix:** free IG 149→150 yes / 151 no; pooled other 99→100 yes /101 no;
premium999→1000 yes /1001 no; unused other slots never increase IG cap; two imports
competing for the last slot admit one; replay/month change/delete never reset usage;
manual share remains independent. Test selection conflict, malformed/bomb archive,
cancel/restart, partial chunk commit, retry and terminal partial failure. Assert
membership, usage, outbox and displayed counters agree in real DB transactions.

## Batch 5 — Complete library and account lifecycle (T08–T11)

Sub-batch 5A: fastapi-developer owns owner-scoped listing/detail/counts/category/
collection APIs and stable `(created_at,id)` cursors. Implement list/filter/count
consistency, renewable thumbnail URLs from stored object keys, bounded resumable
progress/events and SSE transaction cleanup. Parent owns shared response contracts.
Return explicit processing and terminal states; no expensive signing work in an
unbounded loop. Validate tenant ownership for mutations and relation joins.

Sub-batch 5B: fastapi-developer owns recoverable item/account deletion and account
preferences/export where visible. Extend the Batch 2 deletion fence into a durable
saga: stop new work, prevent lazy profile recreation, clean storage/vector/DB/auth
with retry checkpoints, then remove or scrub completed/pending outbox payloads
without losing the active deletion operation. Handle late provider results and
retained JWTs. No success claim until the appropriate durable stage completes.
Archive cleanup must follow the confirmed D06 retention policy. Data export, if
visible, is a private durable job with expiring access and no credentials.

Security-auditor reviews actual role tests, not only policies on paper. Verify
anon/authenticated isolation and server-role access for outbox/admission/import
relations. Complete durable rate/cost controls and useful retry/dead-letter views.
**Acceptance:** equal-timestamp pagination loses no rows; category/collection totals
match; signed media renews; repeated delete is safe; external auth deletion failure
is recoverable and cannot re-create the account; late workers cannot resurrect
content; outbox data is cleaned according to the deletion lifecycle.

## Batch 6 — Connect all native screens to real state (T13–T17)

Use the installed Impeccable native guidance and DESIGN_EXECUTION_BRIEF.md. One
expo-react-native-expert owns foundations first: semantic tokens, readable body
text, safe-area/keyboard controls, labeled tabs and common async states (T13).
Then assign screen groups only after shared components/routes/API types stabilize.

1. Home/Library (T14): remove fake graphs/counts/seeded account data; real recent
   items, filters, pagination, collection/category mutations and all navigation.
2. Import (T15): real multipart upload → preview → allowance/selection → explicit
   admission → resumable background progress/partial failures. Remove completion
   timer. Reopen an existing import after restart; truthful privacy/retention copy.
3. Ask/detail (T16): correct citation destination, fallback/no-hit/timeout/offline
   states, retained drafts, original link, processing/retry/deletion/organization;
   describe current enhance behavior accurately.
4. Account/settings (T17): real identity and allowance, persisted preferences,
   OS-permission distinction, working export/deletion or explicit unavailable state.
   No successful toast for an operation that never occurred.

**Acceptance:** each visible control has a verified action; empty accounts have no
fake activity; isolated seeded accounts show exact server data; server errors,
refresh, restart and account switch are tested. Render small/large phones and large
text. Native keyboard, focus, screen reader and device gates require actual device
or emulator evidence. Web snapshots do not close them.

## Batch 7 — Capture, notifications and premium activation (T18/T19/D04)

Share capture depends on Batch 2+3. expo-react-native-expert owns account-scoped
durable offline save queue and native entry points; test-automator owns separate
Android scenarios. Handle cold/warm start, URL normalization, multiple payloads,
logged-out handoff, duplicate shares, process death, expiry and reconnect. Distinguish
queued-on-device from accepted-by-server. Do not transfer one account's queue to
another. iOS extension/App Group/session handling is a separate native work item.

Push: backend sender/receipt jobs and mobile permission/token/tap routing get
separate ownership. Persist event/dedup state; send useful import summaries, not
1,000 item notifications. Handle invalid tokens, logout/unregister, receipts,
permission denial and cold-start ownership checks. Push is a hint to refetch.

Premium: only after D01–D04 confirmation, implement chosen payment/store provider
verification, idempotent webhooks, expiry/refund reconciliation and authoritative
entitlement reads. Never grant premium from user-editable metadata or a client
boolean. If decisions/credentials are absent, keep purchase/activation unavailable
and finish unrelated free-account logic. Do not claim Premium works from fixtures.

## Batch 8 — Integration and release evidence (remaining T20/T21)

Parent/test-automator integrates CI; cloud-architect reviews capacity and IAM;
terraform-engineer edits infra. Use disposable pgvector PostgreSQL and synthetic
auth/providers. Bootstrap auth stubs only in vanilla test Postgres. Verify the
migration chain on an empty DB and upgrade from the previous revision, constraints,
restricted roles, rollback compatibility and worker-generation deploy ordering.
Do not skip the parser's essential synthetic fixtures in CI.

Exercise one complete journey and its failure variants: login → share → ingest →
recall → organize → import preview/admit → terminal progress → deletion. Add two-account
isolation, process crash, queue outage, duplicate delivery, offline/native restart,
expired session, retained token, upload abuse and stale worker cases. Check totals
and external-call counts as well as HTTP status codes.

Review combined API/worker/dispatcher/SSE DB connection budgets, bounded bulk work,
queue retry/deadline settings, exact invoker/secret IAM, pending-job alerts, cost
limits and deployment image ownership. Obtain separate authorization for actual
staging deployment/migration/provider exercises. Record native/cloud evidence with
environment, revision and date; do not infer it from TypeScript or unit tests.

## Decision checkpoints and stopping rules

D01 upgrade/downgrade, D02 charge/refund, D03 saved-audio treatment, D04 payments,
D05 non-Instagram export formats and D06 archive retention remain owner decisions.
Ask a concise question when its dependent behavior is ready to activate; meanwhile
build fixtures, interfaces and independent work. Never substitute a proposed policy
for a confirmed decision. A missing paid-provider credential blocks its live test,
not local unit implementation. A missing DB runtime blocks concurrency acceptance,
not unrelated mobile progress. Do not bypass the missing gate using production.

Batch 1b and Batch 2 source repairs and isolated staging concurrency validation
are complete. Proceed to Batch 3 under disjoint mobile ownership while local
database, Terraform registry, hosted-cloud and native-device gates remain open.
Continue later batches only after shared interfaces and
ownership are settled. Update the master ledger after every bounded batch with
files, specialists actually used, commands, skips, findings resolved and remaining
gates. Keep tasks partial when acceptance is missing. The entire app is complete
only when the chosen release-visible logic and its native/cloud acceptance matrix
are closed, or the owner explicitly removes a feature from release scope.

**Forward progress (2026-10-20):** Local PostgreSQL 17.7 is running on the
default port and reports `vector` available; pgvector is installed for the PG17
toolchain. T10 durable deletion checkpoints and the profile recreation fence pass
local unit and isolated PostgreSQL concurrency/RLS checks, but the retained UUID
tombstone still needs a D06 retention policy. T18 Android-first URL capture and
account-scoped offline delivery are implemented with 23 passing mobile tests,
TypeScript and Expo config checks. A blocked-flush race was fixed; deletion clears
the local queue after server acceptance. Both reviewer packets found no
cross-account defect. Native device acceptance, deletion cleanup crash/race,
AsyncStorage backup policy and backend URL-fetch SSRF defenses remain open.
Batch C/R15 is underway: Alembic 0010 now adds durable owner-scoped preview and
opaque candidate records, with a unique import-to-preview binding; the disposable
PG17 full-head/RLS migration test passes. Route, expiry sweep, selected-only
commit/worker handoff, API concurrency, and mobile preview wiring are still open.
Keep bulk parsing and paid-provider activation behind their admission and
capacity gates.

**R15 continuation (2026-10-20):** Preview schema, bounded upload, candidate
pagination, selected commit, one-hour cleanup and mobile preview/confirmation UX
are now implemented locally. Candidate metadata is removed on commit only after
the selected worker keys are durable; expired uncommitted candidates are removed
by a lock-revalidating bounded sweep, and the staged ZIP follows the existing
staging cleanup grace. The owner delegated the preview-only retention decision
to the developer; this does not resolve completed archive or deletion-tombstone
retention. Current local evidence: API unit suite 500 passed/12 skipped, focused
preview/entry/dispatch tests 31 passed, isolated local PG17 RLS and cleanup race
tests 2 passed, selected commit/outbox/worker-entry regression 2 passed, mobile
Jest 26 passed and TypeScript passes. `code-reviewer` confirmed fixes for
account-switch busy state, delayed-confirmation expiry and account-fenced picker
results. Security packet review found no selected-ID/tenant bypass, while leaving
ZIP bounds, worker SSRF and hosted cleanup timing unverified. Next: run full stored
ZIP worker execution using a synthetic source and test concurrent API quota/commit
behavior, then gate the legacy direct-import route and configure/observe hosted
cleanup. Premium admission is still FREE-only pending trusted entitlement and
D01/D04 policy. Keep T07/T15 partial until native acceptance, SSRF, capacity,
adapter, premium and hosted gates are evidenced.

**R15 validation follow-up (2026-10-20):** The public legacy POST route now
returns 410 without an upload/body schema; regression coverage verifies the
request cannot reach parsing, staging, admission or storage. Malformed IPv6
authorities and empty ports are rejected as unsupported URLs. Local validation
after these changes: API units 515 passed/12 skipped; isolated PostgreSQL 17
worker, outbox, admission, preview-worker and deletion/RLS suites 22 passed;
compileall, offline Alembic head generation and diff check passed. The first DB
rerun caught a stale isolated worker fixture missing the new preview tables; the
fixture now creates the complete schema graph and the rerun passes. Reviewers
found no route/authority bypass, but downstream transport safety still needs a
source audit: yt-dlp and gallery-dl do not use the pinned-DNS media downloader.
The PG17 server already provides pgvector 0.8.7 files; no additional download is
needed. T07/T15 remain partial pending stored-ZIP worker execution, quota/API
concurrency, extractor SSRF, hosted cleanup, and native acceptance.

**R15 worker/egress continuation (2026-10-20):** The stored ZIP now has a local
integration test through the real parser and `process_stored_import`, with
synthetic storage/dispatch and an explicit local PostgreSQL 17 guard. The
guard requires opt-in, a loopback URL and verifies the connected server is
loopback PG17 before creating a UUID schema. `fastapi-developer` added a
separate-proxy fail-closed check for yt-dlp/gallery-dl and sanitized extractor
logs/errors. This does not enforce network egress. Latest checks: focused
extractor/settings tests 33 passed; full API units 522 passed/12 skipped;
isolated local PG worker/outbox/admission/preview/RLS/concurrency suite 23
passed; `git diff --check` passes. The `.venv`'s declared lxml dependency was
incomplete and was repaired from locked lxml 6.1.3 in that environment only.
The code reviewer withdrew the fixture isolation finding after this guard; its
remaining caveat is deliberate tunneling to a remote server bound to loopback, so
the local opt-in still relies on the operator's URL. The security reviewer found
no fixture issue and confirmed proxy-only network egress as a high release blocker.
Cloud review found no VPC/firewall direct-egress denial in current Terraform;
proxy policy, redirect/DNS-rebinding behavior and runtime enforcement remain
unverified. Keep T07/T15 partial and preserve the hosted/native gates.

**Preview commit race regression (2026-10-20):** `test-automator` added a local
PG17 two-session test. It reproduced a duplicate-commit failure: the committed
preview branch rolled back and then read expired ORM attributes, causing
`MissingGreenlet`. Parent now constructs the scalar response before rollback.
Concurrent same-selection commits produce one Import/reservation/outbox/dispatch;
a different selection returns 409. Read-only code/security source-packet reviews
found no concurrency or tenant issue. Both reviewers asked about the FREE tier;
this is intentional because no trusted entitlement source exists and premium is
explicitly gated by D01/D04. T05 stays partial. Focused preview and local PG suites
41 passed; full API units 522 passed/12 skipped. Cross-preview HTTP quota races,
trusted premium entitlement, direct-egress denial, hosted cleanup and device checks
remain open.

**Historical progress (2026-10-18):** T13 source foundation is implemented by
`expo-react-native-expert`: semantic light/Ask tokens, native system body type,
coral-button contrast, accessible busy/disabled button behavior with 48dp minimum,
and quieter accessible badges. `code-reviewer` found no blocker from the exact
source packet; large text, hit area, screen reader and device rendering remain
open. Local PG17 is available at `127.0.0.1:5432`; the worker/outbox/admission
isolated-schema suite passed 17 tests after standard localhost access escalation.
API units: 470 passed/12 skipped; mobile Jest: 6 passed; TypeScript, compileall,
and diff checks pass. T14 Home is now assigned to the Expo specialist; remove the
fake graph, save counts and collections in favor of live recent-item state before
moving on to Library. No staging or production mutation occurred.
