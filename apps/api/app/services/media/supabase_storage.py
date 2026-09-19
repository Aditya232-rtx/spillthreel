"""Supabase Storage client — thumbnail uploads + signed URL generation.

All backend writes use the service-role key (bypasses RLS by design).
Reads on the mobile side use the publishable key with the object owner
scoped by the `{user_id}/…` prefix — Storage RLS policies enforce this
even though the token is public.

We do not use supabase-py's high-level client for writes because it
doesn't stream — it materializes the whole payload in memory. Uploading
via the REST endpoint with httpx + a file handle keeps peak memory
proportional to the socket buffer, not the file size.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

_UPLOAD_TIMEOUT_SECONDS = 30.0
_DEFAULT_SIGNED_URL_TTL_SECONDS = 3600  # 1h — matches auth token lifetime


class StorageError(RuntimeError):
    """Any non-2xx from Supabase Storage."""


def _storage_base_url() -> str:
    settings = get_settings()
    return settings.supabase_url.rstrip("/") + "/storage/v1"


def _service_auth_headers() -> dict[str, str]:
    settings = get_settings()
    key = settings.supabase_secret_key
    # New-format secret keys go on the apikey header (Supabase rejects them
    # on Authorization: Bearer as a fail-loudly guardrail per the migration
    # docs). But Storage still accepts Bearer for backward compat and the
    # extra Authorization header helps when hitting older instances.
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }


async def upload_object(
    bucket: str, object_key: str, source: Path, content_type: str
) -> str:
    """Upload a local file to `{bucket}/{object_key}`.

    Returns the fully-qualified object path (`{bucket}/{object_key}`)
    that we store on `items.thumbnail_url` alongside a fresh signed
    URL for the client to fetch it.
    """
    url = f"{_storage_base_url()}/object/{bucket}/{object_key}"
    headers = _service_auth_headers() | {
        "Content-Type": content_type,
        # `x-upsert=true` makes reprocessing an item idempotent — we
        # overwrite any prior thumbnail rather than 409'ing.
        "x-upsert": "true",
    }
    with source.open("rb") as fp:
        async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, content=fp.read())
    if response.status_code >= 400:
        raise StorageError(
            f"upload to {bucket}/{object_key} failed: "
            f"{response.status_code} {response.text[:200]}"
        )
    _logger.info(
        "storage.uploaded",
        bucket=bucket,
        key=object_key,
        size=source.stat().st_size,
    )
    return f"{bucket}/{object_key}"


async def signed_url(
    bucket: str, object_key: str, ttl_seconds: int = _DEFAULT_SIGNED_URL_TTL_SECONDS
) -> str:
    """Mint a time-limited signed URL for `{bucket}/{object_key}`."""
    url = f"{_storage_base_url()}/object/sign/{bucket}/{object_key}"
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url, headers=_service_auth_headers(), json={"expiresIn": ttl_seconds}
        )
    if response.status_code >= 400:
        raise StorageError(
            f"sign for {bucket}/{object_key} failed: "
            f"{response.status_code} {response.text[:200]}"
        )
    body = response.json()
    signed_path = body.get("signedURL") or body.get("signedUrl")
    if not signed_path:
        raise StorageError(f"sign endpoint returned no URL: {body}")
    # signed_path is `/storage/v1/object/sign/…?token=…` — needs the
    # project URL prepended.
    settings = get_settings()
    return settings.supabase_url.rstrip("/") + signed_path


async def delete_object(bucket: str, object_key: str) -> None:
    """Delete a single object. Silent on 404."""
    url = f"{_storage_base_url()}/object/{bucket}/{object_key}"
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS) as client:
        response = await client.delete(url, headers=_service_auth_headers())
    if response.status_code == 404:
        return
    if response.status_code >= 400:
        raise StorageError(
            f"delete {bucket}/{object_key} failed: "
            f"{response.status_code} {response.text[:200]}"
        )
