# Bulk-import lifetime history reconciliation

`bulk_import_history_reconciled` is an operational assertion that old usage has
been reconciled. It must stay `false` until the evidence below is collected and
reviewed. Do not enable admission by guessing from mutable counters or by
backfilling every old import row as a successful charge.

## Before changing the gate

1. Choose and record a cutover timestamp and the exact policy version. Obtain
   owner decisions for D01–D03 and identify the trusted entitlement source under
   D04. The current API deliberately rejects `separate_allowance`; the confirmed
   premium ceiling is 1,000 once per account. Counting prior free usage against
   that ceiling is the proposed D01 carry-forward policy, still awaiting the owner's
   upgrade/downgrade decision.
2. Use a read-only inventory against a verified staging copy or a production
   snapshot. Never use the production database as a test fixture. Record the
   source tables, query/version, snapshot identifier, timestamp, and operator.
3. Reconcile per-account successful/admitted unique media from durable import
   membership, reservations, and item attribution where available. Exclude
   retries and duplicates. Do not count manual share-sheet saves. Resolve
   cross-import canonical duplicates consistently. Preserve Instagram and
   pooled-other source attribution for free accounts.
4. Explicitly account for deleted, merged, or otherwise missing accounts. If
   historical records cannot establish a reliable lifetime total, stop and
   obtain an owner-approved remediation rule; do not silently grant a fresh
   allowance or invent identity fingerprinting.
5. Validate that per-account totals and bucket totals are internally
   consistent, that no free bucket exceeds its cap without a documented
   exception, and that a premium account's counted total cannot exceed the
   1,000 ceiling. Retain aggregate reconciliation output and a restricted
   per-account evidence artifact; never place credentials or raw user media in
   this runbook or ordinary logs.
6. Recommended operational evidence process: have a second operator review the method and results. Record both names,
   approval, exceptions, and the exact configuration change in the deployment
   record. Only then may the owner-approved version and reconciliation flag be
   configured together.

## Evidence to retain

Keep the snapshot/cutover reference, query or job revision, source-table
inventory, duplicate and deleted-account handling, aggregate totals, validation
results, exception approvals, operator signoff, and the final policy version in
the restricted operational record. This repository contains the procedure, not
the per-account evidence.

If the evidence cannot support an exact lifetime count, leave bulk admission
disabled and escalate the unresolved historical policy. Do not run writes,
migrations, or reconciliation updates as part of a connectivity/read-only test.
