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
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)

_UPLOAD_TIMEOUT_SECONDS = 30.0
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_DEFAULT_SIGNED_URL_TTL_SECONDS = 3600  # 1h — matches auth token lifetime


class StorageError(RuntimeError):
    """Any non-2xx from Supabase Storage."""


def _is_signed_object_path(path: str) -> bool:
    prefix = "/storage/v1/object/sign/"
    if not path.startswith(prefix):
        return False
    remainder = path[len(prefix) :]
    if (
        not remainder
        or "\\" in path
        or "%2f" in path.lower()
        or "%5c" in path.lower()
    ):
        return False
    return all(unquote(segment) not in {".", ".."} for segment in remainder.split("/"))


def canonicalize_signed_url(value: str | None) -> str | None:
    """Repair legacy Supabase signed URLs that omitted ``/storage/v1``.

    Older pipeline versions stored ``https://<project>/object/sign/...`` even
    though Storage serves the route beneath ``/storage/v1``. Only rewrite
    URLs on this project's origin and the exact signed-object route; leave
    other thumbnail providers untouched.
    """
    if not value:
        return value
    settings = get_settings()
    project = urlsplit(settings.supabase_url)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != project.netloc:
        return value
    if _is_signed_object_path(parsed.path):
        return value
    if parsed.path.startswith("/object/sign/"):
        normalized_path = "/storage/v1" + parsed.path
        if _is_signed_object_path(normalized_path):
            return urlunsplit(
                (parsed.scheme, parsed.netloc, normalized_path, parsed.query, parsed.fragment)
            )
    return value


def _storage_base_url() -> str:
    settings = get_settings()
    return settings.supabase_url.rstrip("/") + "/storage/v1"


def _service_auth_headers() -> dict[str, str]:
    settings = get_settings()
    key = settings.supabase_secret_key
    if not key:
        raise RuntimeError("supabase_storage_service_key_missing")
    # New-format secret keys go on the apikey header (Supabase rejects them
    # on Authorization: Bearer as a fail-loudly guardrail per the migration
    # docs). But Storage still accepts Bearer for backward compat and the
    # extra Authorization header helps when hitting older instances.
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }


async def upload_object(bucket: str, object_key: str, source: Path, content_type: str) -> str:
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
            f"upload to {bucket}/{object_key} failed: {response.status_code} {response.text[:200]}"
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
            f"sign for {bucket}/{object_key} failed: {response.status_code} {response.text[:200]}"
        )
    body = response.json()
    signed_path = body.get("signedURL") or body.get("signedUrl")
    if not isinstance(signed_path, str) or not signed_path:
        raise StorageError(f"sign endpoint returned no URL: {body}")
    # Supabase Storage commonly returns `/object/sign/…?token=…`; its
    # response path omits the API mount prefix used by public requests.
    settings = get_settings()
    project = urlsplit(settings.supabase_url)
    parsed = urlsplit(signed_path)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or parsed.netloc != project.netloc:
            raise StorageError("sign endpoint returned an unexpected URL origin")
        path = parsed.path
    else:
        path = parsed.path
    if path.startswith("/object/sign/"):
        path = "/storage/v1" + path
    if not _is_signed_object_path(path):
        raise StorageError("sign endpoint returned an unexpected URL path")
    return urlunsplit((project.scheme, project.netloc, path, parsed.query, parsed.fragment))


async def download_object(bucket: str, object_key: str, dest: Path) -> Path:
    """Download `{bucket}/{object_key}` to `dest`, streaming to disk.

    Used by the import worker to fetch staged export ZIPs. Raises
    StorageError on any non-2xx so callers can retry transient failures.
    """
    url = f"{_storage_base_url()}/object/{bucket}/{object_key}"
    async with (
        httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS) as client,
        client.stream("GET", url, headers=_service_auth_headers()) as response,
    ):
        if response.status_code >= 400:
            body = await response.aread()
            raise StorageError(
                f"download {bucket}/{object_key} failed: {response.status_code} {body[:200]!r}"
            )
        with dest.open("wb") as fp:
            async for chunk in response.aiter_bytes():
                fp.write(chunk)
    _logger.info(
        "storage.downloaded",
        bucket=bucket,
        key=object_key,
        size=dest.stat().st_size,
    )
    return dest


async def delete_object(bucket: str, object_key: str) -> None:
    """Delete a single object. Silent on 404."""
    url = f"{_storage_base_url()}/object/{bucket}/{object_key}"
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS) as client:
        response = await client.delete(url, headers=_service_auth_headers())
    if response.status_code == 404:
        return
    if response.status_code >= 400:
        raise StorageError(
            f"delete {bucket}/{object_key} failed: {response.status_code} {response.text[:200]}"
        )


async def list_objects(bucket: str, prefix: str, limit: int = 1000, offset: int = 0) -> list[str]:
    """List object keys under `prefix` (service-role, bypasses RLS).

    Wraps POST /storage/v1/object/list/{bucket}. Returns keys relative to
    the bucket root. Unknown shapes return [] rather than raising — a
    half-provisioned bucket must not wedge account deletion.
    """
    url = f"{_storage_base_url()}/object/list/{bucket}"
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url,
            headers=_service_auth_headers(),
            json={"prefix": prefix, "limit": limit, "offset": offset},
        )
    if response.status_code >= 400:
        raise StorageError(
            f"list {bucket}/{prefix} failed: {response.status_code} {response.text[:200]}"
        )
    try:
        body = response.json()
    except ValueError as e:
        raise StorageError(f"list {bucket}/{prefix} returned invalid JSON: {e}") from e
    if not isinstance(body, list):
        return []
    keys: list[str] = []
    clean_prefix = prefix.rstrip("/")
    for entry in body:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name.startswith(clean_prefix) or name.startswith(prefix):
            keys.append(name)
        else:
            keys.append(f"{clean_prefix}/{name.lstrip('/')}" if clean_prefix else name)
    return keys


async def delete_prefix(bucket: str, prefix: str) -> int:
    """Delete every object under `prefix`. Returns the deleted count.

    Silent when the prefix is already empty. Used by account deletion to
    purge the `{user_id}/` prefix in the media + exports buckets.
    """
    total = 0
    while True:
        keys = await list_objects(bucket, prefix, limit=1000, offset=0)
        if not keys:
            break
        for key in keys:
            await delete_object(bucket, key)
            total += 1
    _logger.info("storage.prefix_deleted", bucket=bucket, prefix=prefix, count=total)
    return total
