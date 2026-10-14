"""Account deletion cascade — worker-side hard delete for DELETE /v1/me.

Flow (PRD F5.7): DELETE /v1/me soft-flags `profiles.deleted_at` (403-lock)
then enqueues `delete_user`. The worker runs this module:

  1. Supabase Storage prefixes for the user (media + exports buckets)
  2. pgvector embeddings namespace (explicit delete; the FK cascade covers
     it too — belt-and-suspenders like DELETE /v1/items/{id} does)
  3. `profiles` row (FK ON DELETE CASCADE wipes every user-scoped table)
  4. Supabase Auth Admin `deleteUser` (removes the `auth.users` row)

Cascade coverage (verified in app/db/models.py + 0001_initial — every
user-scoped table keys on `profiles.id` ON DELETE CASCADE): items,
categories, item_embeddings, collections, imports, saved_audio,
push_tokens. Second-order via those parents: item_categories (via
items/categories), collection_items (via collections/items),
ingestion_events (via items). No explicit per-table deletes needed.

Idempotent: a missing profile, a 404 auth user, or an empty storage
prefix all count as success so Cloud Tasks retries never wedge on a
partially completed purge. No schema change — pure row/object deletes.
"""

from __future__ import annotations

import httpx
from sqlalchemy import delete

from app.db.models import Profile
from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

_AUTH_ADMIN_TIMEOUT_SECONDS = 30.0


def _auth_admin_headers() -> dict[str, str]:
    """Service-role headers for the Auth Admin API.

    Mirrors `supabase_storage._service_auth_headers` (same secret, same
    dual-header shape) without importing the media module at load time.
    """
    key = get_settings().supabase_secret_key
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }


async def delete_auth_user(user_id: str) -> None:
    """Delete the `auth.users` row via the Supabase Auth Admin API.

    Silent on 404 (already gone — retry-safe). Raises RuntimeError on any
    other non-2xx so Cloud Tasks retries transient failures.
    """
    settings = get_settings()
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/admin/users/{user_id}"
    async with httpx.AsyncClient(timeout=_AUTH_ADMIN_TIMEOUT_SECONDS) as client:
        response = await client.delete(url, headers=_auth_admin_headers())
    if response.status_code == 404:
        _logger.info("users.auth_delete_missing", user_id=user_id)
        return
    if response.status_code >= 400:
        raise RuntimeError(
            f"auth admin delete failed: {response.status_code} {response.text[:200]}"
        )
    _logger.info("users.auth_deleted", user_id=user_id)


async def delete_user_account(user_id: str) -> dict[str, str]:
    """Purge every surface for `user_id`: storage → vectors → DB → auth.

    Deferred imports keep worker-only weight off the api container's import
    graph until the purge actually runs (same pattern as local_dispatcher).
    """
    from app.db.engine import get_pg_pool, session_scope
    from app.services.media import supabase_storage as storage
    from app.services.memory.pgvector_store import PgVectorStore

    settings = get_settings()

    # 1. Storage prefixes (media thumbnails + GDPR export dumps).
    for bucket in (settings.supabase_bucket_media, settings.supabase_bucket_exports):
        await storage.delete_prefix(bucket, f"{user_id}/")

    # 2. Embeddings namespace (explicit; FK cascade covers it too).
    await PgVectorStore(await get_pg_pool()).delete_namespace(user_id)

    # 3. Profile row — FK ON DELETE CASCADE wipes all user-scoped tables.
    async with session_scope() as session:
        await session.execute(delete(Profile).where(Profile.id == user_id))
        await session.commit()

    # 4. Auth user row (the PK everything ultimately hangs off).
    await delete_auth_user(user_id)

    _logger.warning("users.deleted", user_id=user_id)
    return {"status": "ok", "user_id": user_id}
