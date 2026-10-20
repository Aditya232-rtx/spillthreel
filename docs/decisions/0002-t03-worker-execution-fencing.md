# T03 worker execution fencing contract

**Status:** parent-frozen implementation contract, 2026-10-18. Additive schema;
not applied to any database. This contract does not change the confirmed import
allowances or full-ingestion behavior.

## Identity and generation

Each media worker delivery must identify the exact durable intent with
`outbox_id`, `task_type`, `target_type`, `target_id`, and positive `generation`.
The outbox row is authoritative. The dispatcher injects its row ID and generation
into the delivery payload after claiming it; it never trusts a conflicting
payload generation. A worker verifies that the referenced row matches all
identity fields and is the latest generation for that operation/target. The
existing unique logical-job key remains `(task_type,target_type,target_id,
generation)`; generation allocation stays monotonic per operation/target using
retained outbox rows. Explicit item retries serialize allocation by locking
`profiles` then the target `items` row before reading the maximum retained
generation and inserting the new intent in the same transaction.

Missing or mismatched identity, including a legacy queued task without an exact
outbox ID and generation, is acknowledged as a safe no-op with a bounded reason
code. It must never infer “current generation” and call a provider. Before strict
worker rollout, pause dispatch and ensure pending old work is reissued from
durable outbox rows with exact identity; old unidentifiable messages remain
no-op. Provider and database calls do not occur while holding SQL row locks.

## Additive fields

- `task_outbox.execution_status`: `pending`, `completed`, `stale`, or `terminal`;
  `pending` includes queued and currently executing work. Existing transport
  fields (`dispatched_at`, delivery `lease_token`, `lease_until`, and
  `attempt_count`) retain their separate meaning.
- `task_outbox.worker_attempt_count` (nonnegative), `worker_completed_at`, and
  `worker_last_error_code` (bounded class/code only). These fields record worker
  execution; no exception text or user data is persisted here.
- `task_outbox.execution_lease_token` and `execution_lease_until` separately
  fence non-item import, deletion, and category tasks. Transport delivery leases
  remain separate. Pending intent may have no active execution lease or exactly
  one; terminal execution status must have no lease.
- `items.worker_lease_token`, `worker_lease_until`, `worker_task_type`, and
  `worker_generation`. One item-wide lease serializes ingest and enhance. The
  exact outbox row's `execution_status` is the single durable completion fence;
  the outbox row remains retained for the lifetime of its item.

No historical work is marked completed unless current item state proves it. The
new execution status defaults to `pending`; the migration does not fabricate a
completed result. Existing Cloud Tasks without new identity are safely ACK-only;
deployment must pause old producers and reissue any pending work from outbox
before allowing the new worker contract to serve traffic.

## Claim, renew, publish and delete ordering

Claim and finalization use the same row-lock order: `profiles` → `items` → exact
`task_outbox` row. The claim transaction confirms the profile exists and
`deleted_at IS NULL`, item owner matches, exact row identity is valid, generation
is latest, execution is not already completed, and no unexpired item-wide lease
exists. It then creates a random lease token, records the task type/generation,
sets expiry, and increments worker attempts. A completed duplicate and a stale
generation return without providers; a live conflicting lease is safely
acknowledged as already in progress. An expired lease may be reclaimed with a
new token. Non-item work uses the exact outbox row's independent execution lease;
claim, renewal, completion, and release compare its owner token and expiry. Its
long-running operation is cancelled if lease renewal fails.

Renewal and every final DB write require the same item token, task type, generation,
exact outbox row, and a live undeleted profile. Final state/event/counter changes
and outbox execution completion commit atomically; a lost lease cannot publish.
Vector writes must apply that fence in their database statement. Thumbnail/media
objects use attempt-scoped temporary keys; only a guarded DB publication makes a
thumbnail visible, and stale temporary output is cleaned up. A deleted or missing
profile prevents new claims and late publications; the existing soft-delete
`profiles.deleted_at` value is the durable fence until profile purge, and absence
after purge is also fail-closed. Individual item deletion is hard-delete: a
missing item rejects late claims and final writes, while vector deletion removes
any publication that committed before the item delete.

Deletion first writes `profiles.deleted_at` before its purge intent is committed.
Any transaction that also modifies profile/item/outbox state observes the same
lock order. Storage/model/network work stays outside locked transactions. A
provider success followed by process death before the checkpoint may repeat paid
work; the system bounds attempts but does not promise exactly-once external
billing.

## Rollout and tests

The parent owns the model and one Alembic revision. Worker code must use a shared
claim/renew/finalize service in both HTTP and local transports. Tests cover
duplicate deliveries, two concurrent claims, lease expiry/reclaim, both stale
completion branches, ingest/enhance serialization, generation bump, missing and
wrong identity, profile soft-delete race, late vector/thumbnail publication,
terminal status, and exact OIDC service-account plus exact route audience. Actual
DB interleavings run only on disposable local PostgreSQL or the explicitly
authorized isolated staging test schema. Never apply this migration to staging or
production under this test authorization.
