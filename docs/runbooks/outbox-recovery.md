# Outbox recovery runner

The staging recovery runner is a one-shot Cloud Run Job named
`spillthereel-outbox-recovery-staging`. Cloud Scheduler starts it every five
minutes in UTC. The job runs `python -m app.tasks.dispatch_due` from the
existing worker image and calls `app.tasks.outbox.dispatch_due_jobs(limit=50)`.
It dispatches at most 50 due outbox rows per execution. The runner emits only
the structured counters `scanned`, `dispatched`, `failed`, `skipped`, and
`oldest_pending_at`; it exits nonzero when `failed` is nonzero. Do not add task
payloads, exception messages, credentials, or user content to these logs.

The Job runs as the dedicated `spillthereel-dispatcher-staging` service account.
It can enqueue only to the three pipeline queues, read only the `DATABASE_URL`
secret, write logs, and invoke the worker. Supabase service-role and extractor
credentials are not exposed to the recovery Job. Cloud Tasks OIDC tokens use
the dispatcher identity. The Job receives `WORKER_BASE_URL` from the worker
service output. Cloud Scheduler uses its own service account, granted
`roles/run.invoker` on this Job only.

## Bounds and overlapping executions

Each execution has one task, with parallelism one, a 240-second task timeout,
and no Cloud Run task retry. Cloud Scheduler starts a new execution every five
minutes. The four-minute timeout leaves a one-minute gap before the next normal
scheduled start; each batch is capped at 50 rows. Cloud Run Jobs do not expose a
global `max_concurrent_executions` setting, so a delayed/manual duplicate can
still overlap another execution.

Overlapping executions are safe at the outbox boundary: each row must be
claimed with its current delivery lease before it is enqueued, and the named
Cloud Tasks identity makes an already accepted task idempotent. At most one
task runs inside each Job execution; token-checked row leases coordinate cross-
execution overlap. A concurrent runner can observe rows another runner claimed;
these are reported as `skipped`, not failures. Follow-up runs recover rows left
pending by enqueue errors after their backoff expires.

## Health checks

Terraform creates two Cloud Logging metrics from the structured runner output:

- `logging.googleapis.com/user/outbox_recovery_success` counts batches whose
  `failed` field is zero. Use it as the last-success signal; alert when no
  successful point arrives for 15 minutes (three expected runs).
- `logging.googleapis.com/user/outbox_recovery_pending` counts completed
  batches with a non-null `oldest_pending_at`. An empty queue omits that field
  from this metric filter and is healthy. Alert when pending points continue
  across three runs, then inspect the timestamp in the matching Cloud Run Job
  log to determine the oldest pending age.

To inspect a run, open Cloud Logging and filter by resource type
`cloud_run_job`, job name `spillthereel-outbox-recovery-staging`, and the
relevant time window. Check `failed`, then compare `oldest_pending_at` (UTC)
with the current time. When it is null or absent, no undelivered row existed at
the beginning of that batch. If `failed` is nonzero, inspect the safe
`error_code` warning emitted by the dispatcher; never copy task payloads or
exception strings into an incident report.

Cloud Monitoring notification channels are project-owned and are not created
by this Terraform slice. Configure the two metric alerts against an approved
channel after apply. Scheduler creation alone does not prove recovery is
running: verify a successful metric point and inspect `oldest_pending_at` after
an isolated staging failure/recovery exercise before closing the hosted gate.

## Recovery procedure

1. Check the last successful metric point, recent Job executions, and the
   `oldest_pending_at` value in their structured logs.
2. For `failed > 0`, identify the bounded failure class using the safe error
   code, and check Cloud Tasks queue health and database connectivity without
   exporting task payloads.
3. Restore the dependency or permission, then allow the next five-minute
   schedule to retry pending rows. Do not manually clear outbox leases or mark
   rows dispatched.
4. Confirm a later run has `failed = 0`, check that `oldest_pending_at` advances
   or becomes null, and verify that expected named Cloud Tasks exist.

The Terraform definition is not deployed by this change. Cloud Scheduler API
availability, Cloud Run Job and Scheduler IAM, the dispatcher's scoped
self-`actAs` permission for the task OIDC identity, the database secret version, and
staging recovery behavior remain apply-time validation gates.
