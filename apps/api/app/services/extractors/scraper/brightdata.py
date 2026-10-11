"""Bright Data provider — sync /scrape first, trigger → poll → download fallback.

Vendor truth (read 2026-10-11):
* Base ``https://api.brightdata.com``, auth header
  ``Authorization: Bearer <API key>`` (key minted at
  https://brightdata.com/cp/setting/users).
  Docs: https://docs.brightdata.com/api-reference/authentication
* Flow choice: sync ``POST /datasets/v3/scrape`` FIRST (body
  ``{"input": [{"url": <post URL>}], "limit_per_input": null}``,
  ``?dataset_id=...&notify=false&include_errors=true``) — returns the
  record directly on 200 and answers 202 + ``snapshot_id`` past its
  1-minute timeout (then the same poll loop runs). Observed live:
  an account where ``/trigger`` answers 400 "Customer is not active"
  while ``/scrape`` 200s, so trigger CANNOT be the primary path.
  Docs: https://docs.brightdata.com/api-reference/scrapers/synchronous-requests
* Fallback: ``POST /datasets/v3/trigger`` → poll → download INSIDE
  ``fetch()`` with a bounded poll budget (``_POLL_BUDGET_S`` 90s,
  ``_POLL_INTERVAL_S`` 3s), used when /scrape is throttled (429),
  errors 5xx, or answers an unmapped shape.
  Docs: https://docs.brightdata.com/api-reference/scrapers/asynchronous-requests
* Poll-budget exhaustion maps to transient ExtractorError (registry falls
  through), NEVER to SourceGoneError.
* Trigger ``POST /datasets/v3/trigger?dataset_id=...&include_errors=true
  &format=json`` body ``{"input": [{"url": <post URL>]}`` → 200
  ``{"snapshot_id": "..."}``.
  Docs: https://docs.brightdata.com/api-reference/scrapers/asynchronous-requests
* Poll ``GET /datasets/v3/progress/{snapshot_id}`` → ``{status:
  starting|running|ready|failed|canceled}``.
  Docs: https://docs.brightdata.com/api-reference/scrapers/management-apis/monitor-progress
* Download ``GET /datasets/v3/snapshot/{snapshot_id}?format=json`` → JSON
  array of records.
  Docs: https://docs.brightdata.com/api-reference/scrapers/delivery-apis/download-snapshot
* Dataset IDs (documented, env-overridable — never secrets):
  - Instagram Posts (``/p/``): ``gd_lk5ns7kz21pck8jpis``.
    Docs: https://docs.brightdata.com/api-reference/scrapers/social-media-apis/instagram-posts-collect-by-url
    and https://docs.brightdata.com/datasets/scrapers/instagram/send-first-request
  - Instagram Reels (``/reel/``): ``gd_lyclm20il4r5helnj``.
    Docs: https://docs.brightdata.com/api-reference/scrapers/social-media-apis/instagram-reels-collect-by-url
  - TikTok Posts: ``gd_lu702nij2f790tmv9h``.
    Docs: https://docs.brightdata.com/api-reference/scrapers/social-media-apis/tiktok-posts-collect-by-url
    and https://docs.brightdata.com/datasets/scrapers/tiktok/send-first-request
* Record fields (from the same collect-by-URL pages):
  - IG posts: ``{url, user_posted, description, photos[], videos,
    post_content[{type, url}], thumbnail, content_type, profile_url,
    audio_url, ...}``.
  - IG reels: ``{url, user_posted, description, video_url, thumbnail,
    length, profile_url, audio_url, ...}``.
  - TikTok posts: ``{description, video_url, profile_username,
    profile_url, video_duration, hashtags, ...}``. Slideshow/carousel
    image keys are UNCERTAIN (docs show video posts only) — parser tries
    several candidates defensively and raises transient when no playable
    media is found.
* Error taxonomy mapping (docs:
  https://docs.brightdata.com/products/scrapers/error-codes):
  - HTTP 401 (missing/invalid credentials) → ProviderQuotaError. 402/403
    are treated the same per the Part D taxonomy (credits/auth), though
    the vendor table only documents 401/404/429/500 for trigger/scrape.
  - HTTP 404 on trigger (``Collector not found`` / ``dataset does not
    exist``) is a CONFIG error (bad dataset_id), NOT a deleted post —
    mapped to transient ExtractorError. This differs from ScrapeCreators
    where 404 means the post is gone. Deleted/private posts surface as
    RECORD-level ``error_code: dead_page`` (HTTP 200 + include_errors)
    or an empty ``[]`` download — those map to SourceGoneError (like the
    Apify empty-dataset rule).
  - Record ``error_code`` branching follows the vendor guidance to branch
    on ``error_code``, not free text: ``dead_page`` → SourceGoneError.
  - Poll ``failed``/``canceled`` and poll-budget exhaustion → transient
    ExtractorError (fallthrough), never SourceGone.
  - No zone/customer ID is needed: those belong to Native Access (proxy
    ``username:password``), not the Web Scraper Bearer API.
    Docs: https://docs.brightdata.com/api-reference/authentication
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.observability.logging import get_logger
from app.services.extractors.base import ExtractorError, Platform
from app.services.extractors.scraper.base import (
    ProviderQuotaError,
    ScrapedPost,
    SourceGoneError,
    get_cost_per_1k_usd,
    get_scraper_semaphore,
)

_logger = get_logger(__name__)

_PROVIDER_NAME = "brightdata"
_DEFAULT_BASE_URL = "https://api.brightdata.com"
_CONNECT_TIMEOUT_S = 5.0
_READ_TIMEOUT_S = 60.0

# Documented dataset defaults (NOT secrets — dataset IDs are public;
# see module docstring for doc links). Env-overridable.
_DEFAULT_IG_POST_DATASET = "gd_lk5ns7kz21pck8jpis"
_DEFAULT_IG_REEL_DATASET = "gd_lyclm20il4r5helnj"
_DEFAULT_TIKTOK_DATASET = "gd_lu702nij2f790tmv9h"

# Bounded poll budget for trigger → poll → download inside fetch().
_POLL_BUDGET_S = 90.0
_POLL_INTERVAL_S = 3.0

_GONE_MARKERS = (
    "dead_page",
    "dead page",
    "not found",
    "not_found",
    "deleted",
    "removed",
    "private",
    "unavailable",
    "no longer",
    "no data",
    "empty",
)
_QUOTA_MARKERS = (
    "credit",
    "quota",
    "out of credits",
    "insufficient",
    "unauthorized",
    "unauthenticated",
    "forbidden",
    "invalid credentials",
    "credentials are missing",
    "402",
    "401",
    "403",
)


def _base_url() -> str:
    raw = os.environ.get("BRIGHTDATA_BASE_URL", "").strip()
    if raw:
        return raw.rstrip("/")
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "brightdata_base_url", None)
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip("/")
    except Exception:
        pass
    return _DEFAULT_BASE_URL


def _api_token() -> str | None:
    raw = os.environ.get("BRIGHTDATA_API_TOKEN", "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "brightdata_api_token", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


def _dataset_for(platform: Platform, url: str) -> str | None:
    if platform == "instagram":
        # Reels live under /reel/ and need the reels collector; plain
        # posts (/p/) use the posts collector. Documented URL patterns —
        # see quick-reference tables in the send-first-request guides.
        attr = (
            "brightdata_ig_reel_dataset"
            if "/reel/" in url.lower()
            else "brightdata_ig_post_dataset"
        )
        default = _DEFAULT_IG_REEL_DATASET if "/reel/" in url.lower() else _DEFAULT_IG_POST_DATASET
        env_name = (
            "BRIGHTDATA_IG_REEL_DATASET"
            if "/reel/" in url.lower()
            else "BRIGHTDATA_IG_POST_DATASET"
        )
    elif platform == "tiktok":
        attr = "brightdata_tiktok_dataset"
        default = _DEFAULT_TIKTOK_DATASET
        env_name = "BRIGHTDATA_TIKTOK_DATASET"
    else:
        return None
    raw = os.environ.get(env_name, "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return default


def _proxy() -> str | None:
    raw = os.environ.get("OUTBOUND_PROXY_URL", "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "outbound_proxy_url", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


def _redact(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable-url>"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _build_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=_CONNECT_TIMEOUT_S,
        read=_READ_TIMEOUT_S,
        write=_READ_TIMEOUT_S,
        pool=_READ_TIMEOUT_S,
    )
    proxy = _proxy()
    if proxy:
        return httpx.AsyncClient(timeout=timeout, follow_redirects=False, proxy=proxy)
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False)


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return 1.0
    try:
        parsed = float(value.strip().split(",")[0])
    except ValueError:
        return 1.0
    if parsed < 0:
        return 1.0
    return min(parsed, 30.0)


def _classify_payload_error(message: str) -> ExtractorError | None:
    lowered = message.lower()
    if any(m in lowered for m in _QUOTA_MARKERS):
        return ProviderQuotaError(f"brightdata quota/auth: {message[:200]}")
    if any(m in lowered for m in _GONE_MARKERS):
        return SourceGoneError(f"brightdata source gone: {message[:200]}")
    return None


def _extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("error", "message", "msg", "detail", "error_message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = value.get("message")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    return ""


class BrightDataProvider:
    """ScraperProvider impl for Bright Data (trigger → poll → download)."""

    name: str = _PROVIDER_NAME

    async def fetch(self, platform: Platform, url: str) -> ScrapedPost:
        dataset = _dataset_for(platform, url)
        token = _api_token()
        if not token:
            raise ExtractorError("brightdata api token not configured")
        if not dataset:
            raise ExtractorError(f"brightdata dataset not configured for {platform}")
        started = time.monotonic()
        status = "error"
        try:
            async with get_scraper_semaphore():
                return await self._fetch_with_retry(platform, dataset, url, token)
        except ProviderQuotaError as e:
            status = "quota"
            _logger.error(
                "scraper.call",
                provider=self.name,
                platform=platform,
                status=status,
                latency_ms=int((time.monotonic() - started) * 1000),
                est_cost_per_1k_usd=get_cost_per_1k_usd(),
                err=str(e)[:200],
            )
            raise
        except SourceGoneError:
            status = "gone"
            raise
        except ExtractorError as e:
            status = "transient"
            _logger.warning(
                "scraper.call",
                provider=self.name,
                platform=platform,
                status=status,
                latency_ms=int((time.monotonic() - started) * 1000),
                est_cost_per_1k_usd=get_cost_per_1k_usd(),
                err=str(e)[:200],
            )
            raise
        finally:
            if status in ("gone",):
                _logger.info(
                    "scraper.call",
                    provider=self.name,
                    platform=platform,
                    status=status,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    est_cost_per_1k_usd=get_cost_per_1k_usd(),
                )

    async def _finish_ok(self, platform: Platform, records: Any, redacted: str) -> ScrapedPost:
        post = (
            _parse_instagram_records(records, redacted)
            if platform == "instagram"
            else _parse_tiktok_records(records, redacted)
        )
        _logger.info(
            "scraper.call",
            provider=self.name,
            platform=platform,
            status="ok",
            latency_ms=0,
            est_cost_per_1k_usd=get_cost_per_1k_usd(),
        )
        return post

    async def _fetch_with_retry(
        self, platform: Platform, dataset: str, url: str, token: str
    ) -> ScrapedPost:
        redacted = _redact(url)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        # Sync /scrape first (see module docstring): 200 → records, 202 →
        # poll+download. Throttle/server/unmapped answers fall through to
        # the trigger flow below, which has the full error taxonomy.
        scrape_target = f"{_base_url()}/datasets/v3/scrape"
        scrape_params = {
            "dataset_id": dataset,
            "notify": "false",
            "include_errors": "true",
        }
        scrape_body: dict[str, Any] = {"input": [{"url": url}], "limit_per_input": None}
        for attempt in (0, 1):
            try:
                async with _build_client() as client:
                    resp = await client.post(
                        scrape_target, params=scrape_params, headers=headers, json=scrape_body
                    )
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.2, 0.8))
                    continue
                raise ExtractorError(
                    f"brightdata network failed for {redacted}: {type(e).__name__}"
                ) from e
            except httpx.HTTPError as e:
                raise ExtractorError(
                    f"brightdata request failed for {redacted}: {type(e).__name__}"
                ) from e

            if resp.status_code == 429:
                if attempt == 0:
                    await asyncio.sleep(_parse_retry_after(resp.headers.get("retry-after")))
                    continue
                break  # throttled twice → trigger flow decides
            if resp.status_code in (401, 402, 403):
                raise ProviderQuotaError(
                    f"brightdata quota/auth (http {resp.status_code}) for {redacted}"
                )
            if resp.status_code == 404:
                raise ExtractorError(f"brightdata dataset not found for {redacted}")
            if 200 <= resp.status_code < 300:
                try:
                    scrape_payload: Any = resp.json()
                except ValueError as e:
                    raise ExtractorError(f"brightdata invalid scrape JSON for {redacted}") from e
                if isinstance(scrape_payload, dict):
                    sid = scrape_payload.get("snapshot_id")
                    if isinstance(sid, str) and sid.strip():
                        # Async answer past the sync timeout — reuse poll+download.
                        await self._wait_ready(sid.strip(), redacted, headers)
                        records = await self._download_snapshot(sid.strip(), redacted, headers)
                        return await self._finish_ok(platform, records, redacted)
                    return await self._finish_ok(platform, [scrape_payload], redacted)
                if isinstance(scrape_payload, list):
                    return await self._finish_ok(platform, scrape_payload, redacted)
                break  # unexpected shape → trigger flow decides
            if 500 <= resp.status_code <= 599:
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.2, 0.8))
                    continue
                break
            break  # unmapped status → trigger flow decides
        body: dict[str, Any] = {"input": [{"url": url}]}
        trigger_target = f"{_base_url()}/datasets/v3/trigger"
        trigger_params = {
            "dataset_id": dataset,
            "include_errors": "true",
            "format": "json",
        }
        snapshot_id: str | None = None
        last_error: Exception | None = None
        for attempt in (0, 1):
            try:
                async with _build_client() as client:
                    resp = await client.post(
                        trigger_target, params=trigger_params, headers=headers, json=body
                    )
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
                last_error = e
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.2, 0.8))
                    continue
                raise ExtractorError(
                    f"brightdata network failed for {redacted}: {type(e).__name__}"
                ) from e
            except httpx.HTTPError as e:
                raise ExtractorError(
                    f"brightdata request failed for {redacted}: {type(e).__name__}"
                ) from e

            if resp.status_code == 429 and attempt == 0:
                await asyncio.sleep(_parse_retry_after(resp.headers.get("retry-after")))
                last_error = ExtractorError("429 rate limited")
                continue
            if resp.status_code in (401, 402, 403):
                raise ProviderQuotaError(
                    f"brightdata quota/auth (http {resp.status_code}) for {redacted}"
                )
            if resp.status_code == 404:
                # Vendor 404 = bad dataset_id ("Collector not found" /
                # "dataset does not exist"), NOT a deleted post.
                raise ExtractorError(f"brightdata dataset not found for {redacted}")
            if 500 <= resp.status_code <= 599 and attempt == 0:
                last_error = ExtractorError(f"http {resp.status_code}")
                await asyncio.sleep(random.uniform(0.2, 0.8))
                continue
            if resp.status_code < 200 or resp.status_code >= 300:
                message = ""
                try:
                    message = _extract_error_message(resp.json())
                except Exception:
                    try:
                        message = resp.text.strip()[:200]
                    except Exception:
                        message = ""
                if message:
                    mapped = _classify_payload_error(message)
                    if mapped is not None:
                        raise type(mapped)(f"{mapped} [{redacted}]") from None
                raise ExtractorError(
                    f"brightdata trigger http {resp.status_code} for {redacted}"
                    + (f": {message[:160]}" if message else "")
                )
            try:
                trigger_payload: Any = resp.json()
            except ValueError as e:
                raise ExtractorError(f"brightdata invalid trigger JSON for {redacted}") from e
            if isinstance(trigger_payload, dict):
                sid = trigger_payload.get("snapshot_id")
                if isinstance(sid, str) and sid.strip():
                    snapshot_id = sid.strip()
                    break
            raise ExtractorError(f"brightdata trigger missing snapshot_id for {redacted}")
        if snapshot_id is None:
            assert last_error is not None
            raise ExtractorError(f"brightdata retry exhausted for {redacted}: {last_error}")

        await self._wait_ready(snapshot_id, redacted, headers)
        records = await self._download_snapshot(snapshot_id, redacted, headers)
        return await self._finish_ok(platform, records, redacted)

    async def _wait_ready(self, snapshot_id: str, redacted: str, headers: dict[str, str]) -> None:
        deadline = time.monotonic() + _POLL_BUDGET_S
        target = f"{_base_url()}/datasets/v3/progress/{snapshot_id}"
        while True:
            try:
                async with _build_client() as client:
                    resp = await client.get(target, headers=headers)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
                # Poll blips are transient — keep polling within budget.
                pass
            except httpx.HTTPError as e:
                raise ExtractorError(
                    f"brightdata progress failed for {redacted}: {type(e).__name__}"
                ) from e
            else:
                if resp.status_code in (401, 402, 403):
                    raise ProviderQuotaError(
                        f"brightdata quota/auth (http {resp.status_code}) for {redacted}"
                    )
                if resp.status_code == 429:
                    wait = _parse_retry_after(resp.headers.get("retry-after"))
                    if time.monotonic() + wait > deadline:
                        raise ExtractorError(
                            f"brightdata snapshot not ready for {redacted} (poll budget exhausted)"
                        )
                    await asyncio.sleep(wait)
                    continue
                if 500 <= resp.status_code <= 599:
                    # Transient progress blip — keep polling within budget.
                    pass
                elif resp.status_code == 404:
                    raise ExtractorError(f"brightdata snapshot not found for {redacted}")
                elif 200 <= resp.status_code < 300:
                    try:
                        payload: Any = resp.json()
                    except ValueError:
                        pass
                    else:
                        status_value = payload.get("status") if isinstance(payload, dict) else None
                        status = (
                            status_value.strip().lower() if isinstance(status_value, str) else ""
                        )
                        if status == "ready":
                            return
                        if status in ("failed", "canceled"):
                            # Ambiguous (could be empty snapshot or infra
                            # failure) — transient so the registry falls
                            # through; record-level dead_page is the only
                            # SourceGone signal. Never poll-timeout as gone.
                            message = (
                                _extract_error_message(payload) if isinstance(payload, dict) else ""
                            )
                            raise ExtractorError(
                                f"brightdata snapshot {status} for {redacted}"
                                + (f": {message[:160]}" if message else "")
                            )
                        # starting / running / unknown → keep polling.
                else:
                    raise ExtractorError(
                        f"brightdata progress http {resp.status_code} for {redacted}"
                    )
            if time.monotonic() >= deadline:
                raise ExtractorError(
                    f"brightdata snapshot not ready for {redacted} (poll budget exhausted)"
                )
            remaining = deadline - time.monotonic()
            await asyncio.sleep(min(_POLL_INTERVAL_S, max(0.1, remaining)))

    async def _download_snapshot(
        self, snapshot_id: str, redacted: str, headers: dict[str, str]
    ) -> list[Any]:
        target = f"{_base_url()}/datasets/v3/snapshot/{snapshot_id}"
        params = {"format": "json"}
        last_error: Exception | None = None
        for attempt in (0, 1):
            try:
                async with _build_client() as client:
                    resp = await client.get(target, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
                last_error = e
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.2, 0.8))
                    continue
                raise ExtractorError(
                    f"brightdata network failed for {redacted}: {type(e).__name__}"
                ) from e
            except httpx.HTTPError as e:
                raise ExtractorError(
                    f"brightdata request failed for {redacted}: {type(e).__name__}"
                ) from e

            if resp.status_code == 429 and attempt == 0:
                await asyncio.sleep(_parse_retry_after(resp.headers.get("retry-after")))
                last_error = ExtractorError("429 rate limited")
                continue
            if resp.status_code in (401, 402, 403):
                raise ProviderQuotaError(
                    f"brightdata quota/auth (http {resp.status_code}) for {redacted}"
                )
            if resp.status_code in (404, 409):
                raise ExtractorError(
                    f"brightdata snapshot unavailable (http {resp.status_code}) for {redacted}"
                )
            if 500 <= resp.status_code <= 599 and attempt == 0:
                last_error = ExtractorError(f"http {resp.status_code}")
                await asyncio.sleep(random.uniform(0.2, 0.8))
                continue
            if resp.status_code < 200 or resp.status_code >= 300:
                raise ExtractorError(f"brightdata snapshot http {resp.status_code} for {redacted}")
            try:
                payload: Any = resp.json()
            except ValueError as e:
                raise ExtractorError(f"brightdata invalid snapshot JSON for {redacted}") from e
            if isinstance(payload, list):
                return payload
            if isinstance(payload, dict):
                # Tolerate wrapped shapes defensively.
                for key in ("data", "items", "results", "records"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        return value
                message = _extract_error_message(payload)
                if message:
                    mapped = _classify_payload_error(message)
                    if mapped is not None:
                        raise type(mapped)(f"{mapped} [{redacted}]") from None
                raise ExtractorError(f"brightdata unexpected snapshot shape for {redacted}")
            raise ExtractorError(f"brightdata unexpected snapshot shape for {redacted}")
        assert last_error is not None
        raise ExtractorError(f"brightdata retry exhausted for {redacted}: {last_error}")


def _first_str(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_http(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().startswith("http"):
        return value.strip()
    if isinstance(value, dict):
        for sub_key in ("url", "downloadLink", "tiktokLink"):
            sub = value.get(sub_key)
            if isinstance(sub, str) and sub.strip().startswith("http"):
                return sub.strip()
        return None
    if isinstance(value, list):
        for entry in value:
            found = _first_http(entry)
            if found:
                return found
    return None


def _collect_http_list(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str) and value.strip().startswith("http"):
        found.append(value.strip())
    elif isinstance(value, dict):
        single = _first_http(value)
        if single and single not in found:
            found.append(single)
    elif isinstance(value, list):
        for entry in value:
            single = _first_http(entry)
            if single and single not in found:
                found.append(single)
    return found


def _coerce_duration(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        return num if num >= 0 else None
    if isinstance(value, str) and value.strip():
        try:
            num = float(value.strip())
        except ValueError:
            return None
        return num if num >= 0 else None
    return None


def _raise_if_record_error(item: dict[str, Any], redacted: str) -> None:
    code = item.get("error_code")
    message = item.get("error")
    if not isinstance(code, str) or not code.strip():
        # Some failures only carry free text — check gone markers
        # defensively when error_code is absent.
        if isinstance(message, str) and message.strip():
            lowered = message.strip().lower()
            if any(m in lowered for m in _GONE_MARKERS):
                raise SourceGoneError(f"brightdata source gone for {redacted} [{message[:160]}]")
        return
    text = f"{code.strip()}: {message}" if isinstance(message, str) else code.strip()
    lowered_code = code.strip().lower()
    if lowered_code == "dead_page" or any(m in lowered_code for m in _GONE_MARKERS):
        raise SourceGoneError(f"brightdata source gone for {redacted} [{text[:160]}]")
    if isinstance(message, str) and any(m in message.lower() for m in _GONE_MARKERS):
        raise SourceGoneError(f"brightdata source gone for {redacted} [{text[:160]}]")
    raise ExtractorError(f"brightdata record error for {redacted} [{text[:160]}]")


def _parse_instagram_records(payload: Any, redacted: str) -> ScrapedPost:
    items = payload if isinstance(payload, list) else []
    if not items:
        raise SourceGoneError(f"brightdata empty snapshot for {redacted}")
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if "error_code" in raw or "error" in raw:
            _raise_if_record_error(raw, redacted)
            continue
        return _parse_instagram_record(raw, redacted)
    raise ExtractorError(f"brightdata no usable instagram record for {redacted}")


def _parse_instagram_record(item: dict[str, Any], redacted: str) -> ScrapedPost:
    caption = _first_str(item, "description")
    owner_username = _first_str(item, "user_posted")
    owner_name: str | None = None
    owner_url = _first_str(item, "profile_url")
    thumbnail = _first_str(item, "thumbnail")

    image_urls: list[str] = []
    for key in ("photos", "images"):
        for found in _collect_http_list(item.get(key)):
            if found not in image_urls:
                image_urls.append(found)
    carousel_video: str | None = None
    post_content = item.get("post_content")
    if isinstance(post_content, list):
        for entry in post_content:
            if not isinstance(entry, dict):
                continue
            entry_type = str(entry.get("type") or "").lower()
            entry_url = _first_str(entry, "url")
            if entry_url is None or not entry_url.startswith("http"):
                continue
            if entry_type == "video":
                if carousel_video is None:
                    carousel_video = entry_url
            elif entry_url not in image_urls:
                image_urls.append(entry_url)

    video_url = _first_str(item, "video_url") or _first_http(item.get("videos"))
    if video_url is None:
        video_url = carousel_video
    audio_url = _first_str(item, "audio_url")
    duration = _coerce_duration(item.get("length"))
    if duration is None:
        duration = _coerce_duration(item.get("videos_duration"))

    content_type = str(item.get("content_type") or "").lower()
    photos_number = item.get("photos_number")
    photo_count = int(photos_number) if isinstance(photos_number, (int, float)) else len(image_urls)
    is_carousel = (
        "carousel" in content_type
        or photo_count > 1
        or len(image_urls) > 1
        or (bool(image_urls) and video_url is not None)
    )
    # NOTE: thumbnail stays OUT of image_urls (Apify house rule) — it is
    # only thumbnail_url. Inserting it would add a duplicate download.
    if is_carousel:
        return ScrapedPost(
            media_type="carousel",
            video_url=video_url,
            image_urls=image_urls,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            owner_url=owner_url,
            duration_seconds=duration,
            thumbnail_url=thumbnail or (image_urls[0] if image_urls else None),
        )
    if video_url:
        return ScrapedPost(
            media_type="video",
            video_url=video_url,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            owner_url=owner_url,
            duration_seconds=duration,
            thumbnail_url=thumbnail,
        )
    if image_urls:
        return ScrapedPost(
            media_type="image",
            image_urls=image_urls,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            owner_url=owner_url,
            duration_seconds=duration,
            thumbnail_url=thumbnail or image_urls[0],
        )
    if thumbnail:
        return ScrapedPost(
            media_type="image",
            image_urls=[thumbnail],
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            owner_url=owner_url,
            duration_seconds=duration,
            thumbnail_url=thumbnail,
        )
    raise ExtractorError(f"brightdata instagram record has no media for {redacted}")


def _parse_tiktok_records(payload: Any, redacted: str) -> ScrapedPost:
    items = payload if isinstance(payload, list) else []
    if not items:
        raise SourceGoneError(f"brightdata empty snapshot for {redacted}")
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if "error_code" in raw or "error" in raw:
            _raise_if_record_error(raw, redacted)
            continue
        return _parse_tiktok_record(raw, redacted)
    raise ExtractorError(f"brightdata no usable tiktok record for {redacted}")


def _parse_tiktok_record(item: dict[str, Any], redacted: str) -> ScrapedPost:
    caption = _first_str(item, "description", "desc", "text")
    owner_username = _first_str(item, "profile_username", "author", "username")
    owner_name: str | None = None
    owner_url = _first_str(item, "profile_url")
    duration = _coerce_duration(item.get("video_duration"))
    if duration is None:
        duration = _coerce_duration(item.get("duration"))
    thumbnail = _first_str(item, "thumbnail", "cover_url", "coverUrl")
    video_url = _first_str(item, "video_url")
    if video_url is None:
        for key in ("videoUrl", "playAddr", "downloadAddr", "downloadUrl"):
            candidate = _first_http(item.get(key))
            if candidate:
                video_url = candidate
                break
    audio_url = _first_str(item, "audio_url")
    if audio_url is None:
        music = item.get("music")
        if isinstance(music, dict):
            audio_url = _first_http(music.get("play_url"))

    # Slideshow/carousel image keys are UNCERTAIN in the TikTok posts
    # dataset (docs show video posts only) — collect defensively.
    image_urls: list[str] = []
    for key in ("photos", "images", "image_urls", "slideshow_images", "slideshowImageLinks"):
        for found in _collect_http_list(item.get(key)):
            if found not in image_urls:
                image_urls.append(found)
    if video_url:
        return ScrapedPost(
            media_type="video",
            video_url=video_url,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            owner_url=owner_url,
            duration_seconds=duration,
            thumbnail_url=thumbnail,
        )
    if image_urls:
        return ScrapedPost(
            media_type="carousel" if len(image_urls) > 1 else "image",
            image_urls=image_urls,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            owner_url=owner_url,
            duration_seconds=duration,
            thumbnail_url=thumbnail or image_urls[0],
        )
    raise ExtractorError(f"brightdata tiktok record has no playable media for {redacted}")
