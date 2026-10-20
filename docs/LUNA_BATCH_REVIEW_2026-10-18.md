# Review of Luna's first implementation batch — 2026-10-18

## Verdict and scope

Keep the implementation, but do not treat T02 or T04 as fully accepted. The atomic
intent writes, independent cache settings, evidence hydration, citation pairing,
source-span checks and response-log cleanup are useful improvements. The next
batch must repair the issues below before adding more producers or claiming
recovery/idempotency. This review covers the current uncommitted working tree,
including newly added files; it is not a review of a committed release.

No application code was changed during this review. The parent inspected code and
ran validation. A read-only `code-reviewer` specialist reviewed a supplied source
packet because its runtime had no permitted filesystem reader; it did not inspect
the entire repository independently or run commands. It independently confirmed
R01 and R02 and the missing dispatcher tests.

## Findings requiring implementation

| ID | Priority | Evidence and consequence | Required closure |
|---|---|---|---|
| R01 | P1 | `apps/api/app/tasks/outbox.py:160` and `:176`: success/error paths read an unlocked row, compare lease token in Python, then flush an update by ID. A new claim can commit after the read and before the update, allowing the old dispatcher to clear the newer token or replace its backoff/delivery state. | Atomic conditional update on ID + token (and relevant delivery state), or short row lock around comparison and mutation; affected-row check; deterministic concurrent DB test for both success and error finalization. |
| R02 | P1 for local execution | `apps/api/app/tasks/enqueue.py:48` and `:81` await `dispatch_local`, which runs ingestion itself. Save/retry/import call sites await post-commit dispatch before returning. Previously the local branch scheduled the handler and returned. Slow extraction now holds the request open; import chunks process child ingestion serially. The 60-second transport lease can expire during media work. | Durable local consumer independent of request lifetime; prompt dispatch must not await full media processing. Test an accepted save returning while a stub worker remains blocked and recovery after restarting the local consumer. |
| R03 | P2 | `apps/api/app/services/search/pipeline.py:237` slices every hashtag using the same remaining budget without decrementing it. A synthetic two-hit input returned 14,871 evidence characters under the declared 12,000 cap. | Spend the remainder per tag, enforce the total invariant and add boundary tests across items and field combinations. A character cap does not establish a model token cap. |
| R04 | P1 acceptance gap | `dispatch_due_jobs` has no repository caller or scheduled runner. Only prompt delivery exists. A committed intent with enqueue failure remains pending indefinitely without another trigger. | Executable local/staging runner contract plus scheduler/Job definitions and bounded RPC deadlines, metrics and failure behavior; split this small T20 prerequisite out now rather than waiting for all T20 dependencies. Deploy only with separate authorization. |
| R05 | P1 acceptance gap | `worker_entry.py:166` accepts item ID/user ID but no generation, and `:177` invokes ingestion by item ID only. Local dispatch also drops generation. The outbox generation is currently only queue identity; it does not fence paid work or late writes. Existing ingestion can continue after its already-indexed branch. | T03 must consume persisted generation, claim a worker lease and fence all side effects. Keep outbox delivery lease and worker processing lease distinct. |
| R06 | P2 acceptance gap | Mobile API client has a fixed 15-second timeout; backend retrieval and answer allow 8 + 12 seconds, before hydration/overhead. A valid answer can arrive after mobile has already abandoned it. | T12/T04 deadline contract, an actual bounded server request deadline, request-specific mobile timeout/cancellation behavior, and late-valid-response regression. |

### Reproduction detail

R01 is a source-confirmed concurrency window, not an executed database exploit:
A reads its token just before lease expiry; B claims and commits a new token after
expiry; A then flushes its old row changes. The final UPDATE must enforce ownership
in the database at the instant of mutation. Named Cloud Tasks deduplication does
not make an incorrect lease mutation safe; local transport has no such dedup.

R03 was executed without a database or provider. Call `_build_answer_hits` with:
first hit title length 100, each of summary/transcript/on-screen/caption length
2,400, and 22 hashtags of length 100 (11,900 characters). Second hit has a one-character
title and 30 hashtags of length 100. The current branch gives all 30 tags the same
99-character allowance. Measured sum of evidence fields = 14,871, expected <=12,000.

## Important pre-existing gaps to carry forward

- Import retry resets local `queued` and overwrites `full_queued`, rather than
  deriving progress from durable import membership. T06 must test a retry after a
  partially committed chunk; repairing dispatch alone does not finish imports.
- Worker verification accepts a service-account suffix instead of the configured
  identity and accepts broad base-path audiences. Exposure depends on deployed
  invoker IAM. Fix the verifier locally early; confirm IAM separately (T09/T20).
- Uploads buffer the full file before checking size; ZIP expansion/count/CPU bounds
  remain incomplete (T07). Do not enable large imports before this is fixed.
- Deletion has no durable account fence across auth/storage/database operations;
  the new outbox also retains user identifiers and archive keys (T10).
- Lifetime allowance, trusted premium entitlement, import preview/admission and
  real import progress remain unimplemented. Full ingestion is deliberate.
- Mobile Home/Profile still use illustrative data; import completion is simulated;
  shared auth/cache isolation, share capture and push delivery are incomplete.
- Cache adapter tests verify cache options, not deployed pooler compatibility.
  Migration SQL generation does not validate transaction isolation, constraints or RLS.

## Validation rerun in this review

| Check | Result |
|---|---|
| API `pytest tests/unit -q --disable-warnings` | 356 passed, 12 skipped, 16 warnings; 4.28 seconds |
| API `ruff check .` / `ruff format --check .` | Pass; 113 formatted files |
| API `mypy app/` | Pass; 76 source files |
| Mobile `./node_modules/.bin/tsc --noEmit` | Pass |
| Synthetic evidence-budget check | Reproduced R03: 14,871 > 12,000 |
| New outbox test coverage inspection | Only two direct tests: task identity and no caller-transaction commit; no dispatcher failure/lease execution tests |
| Database, pooler, Cloud Tasks, device, paid-model checks | Not run in this review; no live resources modified |

Passing mocked unit tests are a baseline, not closure of R01–R06. Continue through
`LUNA_NEXT_IMPLEMENTATION_PLAN.md`; its first batch explicitly reopens T02/T04.

The same read-only specialist reviewed the next-plan sequencing packet. Clarified
that confirmed numeric caps are implemented immediately while only dependent
unconfirmed policies remain gated; added explicit payload/ack and mobile contract
freezes plus scheduler overlap/health acceptance. Duplicate replay is idempotent;
D02 treatment of existing library items remains a proposed policy.
