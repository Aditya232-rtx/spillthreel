# Continue implementation with GPT-6 Luna

Select GPT-6 Luna in the execution chat and paste the prompt below. This document
neither changes the model nor starts another chat.

---

Continue SpillTheReel from the current working tree. Read AGENTS.md,
docs/LUNA_IMPLEMENTATION_PLAN.md, docs/LUNA_IMPORT_REVIEW_2026-10-18.md and
docs/LUNA_NEXT_IMPLEMENTATION_PLAN.md. Follow the new Batch A–F order; the older
numbered batches are historical scope. Preserve existing changes and completed
cache/citation work. HANDOFF.md is historical. Do not upgrade Expo to avoid diagnosis.

Continue Batch B from its current implementation; Batch A R12/R13/R16 is already
implemented and locally verified. Read the latest Batch B progress update, review
findings and ledger entry before changing code. Batch B currently has durable
ImportEntry/occurrence records, owner-scoped request-key/body-fingerprint
idempotency, persisted admission key binding, membership-derived progress and
worker completion mirroring. The 433-test API unit suite and 12 local PostgreSQL
17 worker/concurrency tests pass. Code-reviewer accepted the terminal manifest
fix, lock order, unrepresentable-item failure and delayed membership relinking;
security-auditor confirmed the worker payload/deletion fence from source packets.
Do not restart those fixes.

Finish Batch B acceptance. First close security review's conditional resource
amplification concern from repeated new idempotency keys/ZIPs containing only
already-reserved URLs. Preserve archive membership where product behavior needs
it, but bound requests, source retention and durable rows; do not recharge or mint
new ingest work. Add real tests for same-key concurrent/same-fingerprint and
conflicting-fingerprint submissions, cross-owner FK rejection, and duplicate
reservation before Item creation. Then materialize collection membership, expose
owner-scoped import discovery and paginated per-entry terminal/progress state, and
implement bounded failed-work retries that reuse existing membership/reservations.
Update the probe's R14 assertion to the safe durable-membership behavior. Obtain
fresh exact-source code-reviewer and security-auditor feedback and update the ledger.

After reviewed Batch B, start Batch C: address R15 upload body limits, streaming,
ZIP aggregate/member/ratio/CPU bounds, preview selection binding, and provider
capacity/retry controls before activating public bulk imports. Keep staging source
private and preview free of paid extraction. Do not activate D01–D06 proposals.

Preserve source on unknown commit outcome. Reconcile through durable identity and
a fresh connection; database unavailability cannot authorize deletion. Stage
outside the short account-lock transaction. Pass an execution context to import
processing and enforce the token/generation/live-owner fence in every mutation
transaction, including chunks, counters and error handlers. Heartbeat cancellation
and final task completion checks alone do not fence earlier writes. Add real
PostgreSQL takeover/deletion tests and fault-injection for lost commit ACK.

Actually delegate exclusive files to fastapi-developer; obtain postgres-pro review
of schema/transactions. Parent owns shared models, migrations, manifests, payload
contracts and integration. Implement intake then worker integration sequentially
if the same files are involved. Use matching installed profiles, or read their
TOML instructions and pass them to a supported spawn tool. Use Luna where
selectable, at most three children, no nested delegation. Report limitations
honestly. After each bounded batch, obtain code-reviewer and security-auditor
feedback using exact current source packets/diffs; reviewers do not edit or run
shell commands. Do not use change summaries alone as code-review evidence.

Next implement Batch B: durable import-entry/item/collection membership, stable
progress and terminal outcomes, request idempotency/result discovery, bounded
failed-work retries. R14 reproduces queued progress falling from 2 to 0 on replay.
Derive counts from persisted membership/transitions. Only newly admitted keys may
mint new work; rediscovery/retry must not reset reservations. Then Batch C closes
archive/resource limits, preview selection and capacity controls before activation.
The independent mobile test/auth lane may proceed with nonoverlapping ownership.
Continue library/deletion, native journeys and release evidence in Batches D–F.

Preserve full ingestion of accepted imported media; free 150 Instagram + 100
pooled other supported apps once/account; premium 1,000 pooled once/account.
No monthly/ZIP reset and no bulk quota on manual shares. D01–D06 remain proposals;
carry-forward code is not owner approval of upgrade/downgrade policy. Keep premium,
history, payment and retention activation gated until their required decisions and
evidence exist. Continue independent work while those decisions are unresolved.

Staging testing via apps/api/.env.staging and launching installed Docker Desktop
are already authorized within the established boundaries. Parse credentials into
memory, never print them or place DSNs in shell arguments. Recheck current
availability; prior success and later DNS/socket failures are historical results.
Verify endpoint types: DATABASE_URL_DIRECT was previously a session pooler, not
a direct host. Start with read-only schema/connection checks; synthetic writes
only in a verified unique non-exposed schema, with run-scoped cleanup. Never point
the whole existing integration suite at staging. Use disposable local pgvector
Postgres with synthetic auth/providers and both DB URLs local for migrations/full
integration. If needed use open -a Docker and normal sandbox escalation for daemon
access. Do not reset/prune Docker or stop unrelated containers. Validate Terraform
in a scratch copy with backend disabled; no deploy/apply is authorized.

For frontend work use Impeccable and DESIGN_EXECUTION_BRIEF.md, preserve the warm
native brand, and wire actual states/actions. Web and unit tests do not prove
native shares, keyboard, accessibility or push. No production migration, real-data
deletion or paid extraction. After every batch update the master ledger with files,
actual specialists, commands/results/skips, review findings and open DB/device/cloud
gates. Keep partial tasks partial and continue authorized independent work.
