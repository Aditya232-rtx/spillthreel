# D07 — Uncommitted import preview retention

**Status:** Decided by developer under owner delegation, 2026-10-20  
**Scope:** Preview candidates and their staged ZIP before explicit commit.

The owner answered “you are the developer decide the best option” when asked
how long uncommitted preview ZIPs and candidate metadata should remain. The
selected policy is a **one-hour preview lifetime**. Candidate metadata is purged
on expiry. After preview expiry and the existing staging lease/grace, the
private staged ZIP is removed by the staging cleanup sweep. When the owner
commits a selection, candidate metadata is deleted atomically after the durable
Import and outbox intent include the selected reservation keys; the staged ZIP
continues through the normal worker source lifecycle.

This decision minimizes storage of source URLs and collection metadata while
giving users time to review an archive. It resolves only preview retention. It
does not decide retention for completed media, exports, deletion tombstones,
account backups, or unrelated D01–D06 assumptions. The mobile UI must communicate
the one-hour preview expiry and provide a fresh-preview action when it expires.

Configuration: `IMPORT_PREVIEW_RETENTION_POLICY_VERSION=preview-retention-1h-v1`
and `IMPORT_PREVIEW_TTL_SECONDS=3600`.

Acceptance still requires a configured, monitored hosted cleanup caller and
verification that object deletion completes within the staging policy window.
