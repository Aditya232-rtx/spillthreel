"""Apify provider — sync run-and-get-dataset-items (Part D).

Vendor truth (read 2026-10-11):
* Endpoint ``POST https://api.apify.com/v2/actors/:actorId/run-sync-get-dataset-items``.
  The POST JSON body becomes the Actor INPUT; the response body is the
  default-dataset items array. Run must finish within ~300s (HTTP 408
  otherwise).
  Docs: https://docs.apify.com/api/v2/actor-run-sync-get-dataset-items-post
  Auth: API token via ``Authorization: Bearer`` header (preferred so the
  token never lands in access logs) — the ``?token=`` query form in the
  docs is equivalent.
* Instagram actor ``apify/instagram-scraper`` (official, most popular IG
  actor). Docs: https://apify.com/apify/instagram-scraper
  Single-post input uses ``directUrls`` (standard Apify URL input —
  exact per-post field combination UNCERTAIN, kept minimal:
  ``{"directUrls": [url], "resultsLimit": 1}``).
  Item shape: ``{type, videoUrl, displayUrl, images, caption,
  ownerUsername, ownerFullName, videoDuration, childPosts, ...}``.
* TikTok actor ``clockworks/tiktok-scraper`` (most popular TikTok actor).
  Docs: https://apify.com/clockworks/tiktok-scraper
  Single-video input uses ``postURLs`` (documented in the Actor README).
  Item shape: ``{text, authorMeta{name, nickName}, videoMeta{duration,
  coverUrl}, mediaUrls, musicMeta{playUrl}, webVideoUrl, isSlideshow,
  slideshowImageLinks, ...}``. UNCERTAIN: the default run (downloads
  off) may not include a direct mp4 CDN URL — parser tries several
  keys and raises transient ExtractorError when none is found so the
  registry can fall through to yt-dlp.
* Error items carry ``error`` + ``errorCode`` (e.g.
  ``POST_NOT_FOUND_OR_PRIVATE``, ``PROFILE_PRIVATE``, ``NOT_FOUND``) —
  documented in the TikTok actor README. Those map to SourceGoneError;
  anything else is transient.

Actor IDs come from settings/env ``APIFY_IG_ACTOR`` /
``APIFY_TIKTOK_ACTOR`` — never hardcoded. Proposed documented defaults
are in the Part D settings report (integrator owns settings.py).
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

_PROVIDER_NAME = "apify"
_DEFAULT_BASE_URL = "https://api.apify.com"
_CONNECT_TIMEOUT_S = 5.0
_READ_TIMEOUT_S = 60.0

_GONE_CODES = frozenset(
    {
        "NOT_FOUND",
        "POST_NOT_FOUND_OR_PRIVATE",
        "POST_SENSITIVE",
        "PROFILE_PRIVATE",
        "PROFILE_EMPTY",
        "INVALID_URLS",
    }
)


def _base_url() -> str:
    raw = os.environ.get("APIFY_BASE_URL", "").strip()
    if raw:
        return raw.rstrip("/")
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "apify_base_url", None)
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip("/")
    except Exception:
        pass
    return _DEFAULT_BASE_URL


def _api_token() -> str | None:
    raw = os.environ.get("APIFY_API_TOKEN", "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "apify_api_token", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


def _actor_for(platform: Platform) -> str | None:
    env_name = "APIFY_IG_ACTOR" if platform == "instagram" else "APIFY_TIKTOK_ACTOR"
    raw = os.environ.get(env_name, "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        attr = "apify_ig_actor" if platform == "instagram" else "apify_tiktok_actor"
        value = getattr(get_settings(), attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        pass
    return None


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


def _actor_input(platform: Platform, url: str) -> dict[str, Any]:
    if platform == "instagram":
        # Minimal single-URL input; exact field combo UNCERTAIN (see module
        # docstring). directUrls + resultsLimit is the documented Apify
        # convention for URL-driven runs.
        return {"directUrls": [url], "resultsLimit": 1}
    if platform == "tiktok":
        return {"postURLs": [url]}
    raise ExtractorError(f"apify: unsupported platform {platform!r}")


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


class ApifyProvider:
    """ScraperProvider impl for Apify Store actors."""

    name: str = _PROVIDER_NAME

    async def fetch(self, platform: Platform, url: str) -> ScrapedPost:
        actor = _actor_for(platform)
        token = _api_token()
        if not token:
            raise ExtractorError("apify token not configured")
        if not actor:
            raise ExtractorError(f"apify actor not configured for {platform}")
        started = time.monotonic()
        status = "error"
        try:
            async with get_scraper_semaphore():
                return await self._fetch_with_retry(platform, actor, url, token)
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
            if status == "gone":
                _logger.info(
                    "scraper.call",
                    provider=self.name,
                    platform=platform,
                    status=status,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    est_cost_per_1k_usd=get_cost_per_1k_usd(),
                )

    async def _fetch_with_retry(
        self, platform: Platform, actor: str, url: str, token: str
    ) -> ScrapedPost:
        redacted = _redact(url)
        # Actor IDs use owner/name form (e.g. apify/instagram-scraper) but the
        # API path segment requires tilde encoding: apify~instagram-scraper.
        actor_path = actor.replace("/", "~")
        target = f"{_base_url()}/v2/actors/{actor_path}/run-sync-get-dataset-items"
        params = {"format": "json", "clean": "true", "limit": "5"}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = _actor_input(platform, url)
        last_error: Exception | None = None
        for attempt in (0, 1):
            try:
                async with _build_client() as client:
                    resp = await client.post(target, params=params, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
                last_error = e
                if attempt == 0:
                    await asyncio.sleep(random.uniform(0.2, 0.8))
                    continue
                raise ExtractorError(
                    f"apify network failed for {redacted}: {type(e).__name__}"
                ) from e
            except httpx.HTTPError as e:
                raise ExtractorError(
                    f"apify request failed for {redacted}: {type(e).__name__}"
                ) from e

            if resp.status_code == 429 and attempt == 0:
                await asyncio.sleep(_parse_retry_after(resp.headers.get("retry-after")))
                last_error = ExtractorError("429 rate limited")
                continue
            if resp.status_code in (401, 402, 403):
                raise ProviderQuotaError(
                    f"apify quota/auth (http {resp.status_code}) for {redacted}"
                )
            if resp.status_code == 404:
                raise ExtractorError(
                    f"apify actor/config not found for {redacted} (check actor ID)"
                )
            if resp.status_code == 408:
                raise ExtractorError(f"apify run timed out for {redacted}")
            if 500 <= resp.status_code <= 599 and attempt == 0:
                last_error = ExtractorError(f"http {resp.status_code}")
                await asyncio.sleep(random.uniform(0.2, 0.8))
                continue
            if resp.status_code < 200 or resp.status_code >= 300:
                raise ExtractorError(f"apify http {resp.status_code} for {redacted}")
            try:
                payload: Any = resp.json()
            except ValueError as e:
                raise ExtractorError(f"apify invalid JSON for {redacted}") from e
            post = (
                _parse_instagram_items(payload, redacted)
                if platform == "instagram"
                else _parse_tiktok_items(payload, redacted)
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
        assert last_error is not None
        raise ExtractorError(f"apify retry exhausted for {redacted}: {last_error}")


def _items_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def _raise_if_error_item(item: dict[str, Any], redacted: str) -> None:
    code = item.get("errorCode")
    message = item.get("error")
    if not isinstance(code, str) or not code.strip():
        return
    text = f"{code}: {message}" if isinstance(message, str) else code
    if code.strip() in _GONE_CODES:
        raise SourceGoneError(f"apify source gone for {redacted} [{text[:160]}]")
    raise ExtractorError(f"apify actor error for {redacted} [{text[:160]}]")


def _first_http(value: Any) -> str | None:
    if isinstance(value, str) and value.strip().startswith("http"):
        return value.strip()
    if isinstance(value, dict):
        for sub_key in ("downloadLink", "tiktokLink", "url"):
            sub = value.get(sub_key)
            if isinstance(sub, str) and sub.strip().startswith("http"):
                return sub.strip()
        return None
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, str) and entry.strip().startswith("http"):
                return entry.strip()
            if isinstance(entry, dict):
                for sub_key in ("downloadLink", "tiktokLink", "url"):
                    sub = entry.get(sub_key)
                    if isinstance(sub, str) and sub.strip().startswith("http"):
                        return sub.strip()
    return None


def _first_str(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_duration(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        return num if num >= 0 else None
    return None


def _parse_instagram_items(payload: Any, redacted: str) -> ScrapedPost:
    items = _items_list(payload)
    if not items:
        raise SourceGoneError(f"apify empty dataset for {redacted}")
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if "errorCode" in raw:
            _raise_if_error_item(raw, redacted)
            continue
        return _parse_instagram_item(raw, redacted)
    raise ExtractorError(f"apify no usable instagram item for {redacted}")


def _parse_instagram_item(item: dict[str, Any], redacted: str) -> ScrapedPost:
    caption = _first_str(item, "caption")
    owner_username = _first_str(item, "ownerUsername", "owner_username", "username")
    owner_name = _first_str(item, "ownerFullName", "owner_full_name", "fullName")
    duration = _coerce_duration(item.get("videoDuration"))
    thumbnail = _first_str(item, "displayUrl", "display_url")
    video_url = _first_str(item, "videoUrl", "video_url")
    audio_url = _first_str(item, "audioUrl", "audio_url")
    image_urls: list[str] = []
    for key in ("images", "carouselImages", "carousel_images", "displayUrl"):
        found_list: list[str] = []
        value = item.get(key)
        if isinstance(value, str) and value.strip().startswith("http"):
            found_list = [value.strip()]
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str) and entry.strip().startswith("http"):
                    found_list.append(entry.strip())
        for entry in found_list:
            if entry not in image_urls:
                image_urls.append(entry)
    child_posts = item.get("childPosts")
    if isinstance(child_posts, list):
        for child in child_posts:
            if not isinstance(child, dict):
                continue
            child_video = _first_str(child, "videoUrl", "video_url")
            if child_video and video_url is None:
                video_url = child_video
            for img_key in ("displayUrl", "display_url", "imageUrl", "url"):
                img = child.get(img_key)
                if isinstance(img, str) and img.strip().startswith("http"):
                    if img.strip() not in image_urls:
                        image_urls.append(img.strip())
                    break
    item_type = str(item.get("type") or "")
    product_type = str(item.get("productType") or "")
    child_count = len(child_posts) if isinstance(child_posts, list) else 0
    is_carousel = (
        item_type.lower() == "sidecar"
        or "carousel" in product_type.lower()
        or (len(image_urls) > 1)
        or (child_count > 1)
    )
    if is_carousel:
        return ScrapedPost(
            media_type="carousel",
            video_url=video_url,
            image_urls=image_urls,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
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
            duration_seconds=duration,
            thumbnail_url=thumbnail or image_urls[0],
        )
    raise ExtractorError(f"apify instagram item has no media for {redacted}")


def _parse_tiktok_items(payload: Any, redacted: str) -> ScrapedPost:
    items = _items_list(payload)
    if not items:
        raise SourceGoneError(f"apify empty dataset for {redacted}")
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if "errorCode" in raw:
            _raise_if_error_item(raw, redacted)
            continue
        return _parse_tiktok_item(raw, redacted)
    raise ExtractorError(f"apify no usable tiktok item for {redacted}")


def _parse_tiktok_item(item: dict[str, Any], redacted: str) -> ScrapedPost:
    caption = _first_str(item, "text", "desc")
    owner_username: str | None = None
    owner_name: str | None = None
    author = item.get("authorMeta")
    if isinstance(author, dict):
        owner_username = _first_str(author, "name", "unique_id")
        owner_name = _first_str(author, "nickName", "nickname")
    duration: float | None = None
    thumbnail: str | None = None
    video_meta = item.get("videoMeta")
    if isinstance(video_meta, dict):
        duration = _coerce_duration(video_meta.get("duration"))
        thumbnail = _first_str(video_meta, "coverUrl", "originalCoverUrl", "cover_url")
    audio_url: str | None = None
    music = item.get("musicMeta")
    if isinstance(music, dict):
        audio_url = _first_http(music.get("playUrl"))
    # Direct video file URL is UNCERTAIN in default runs — try several keys.
    video_url: str | None = None
    for key in ("videoUrl", "video_url", "playAddr", "downloadAddr", "downloadUrl"):
        candidate = _first_http(item.get(key))
        if candidate:
            video_url = candidate
            break
    if video_url is None:
        media_urls = _first_http(item.get("mediaUrls"))
        if media_urls:
            video_url = media_urls
    image_urls: list[str] = []
    slides = item.get("slideshowImageLinks")
    if isinstance(slides, list):
        for entry in slides:
            found = _first_http(entry)
            if found and found not in image_urls:
                image_urls.append(found)
    for key in ("images", "imageUrls", "slideshowImages"):
        value = item.get(key)
        if isinstance(value, list):
            for entry in value:
                found = _first_http(entry)
                if found and found not in image_urls:
                    image_urls.append(found)
    is_slideshow = item.get("isSlideshow") is True
    if video_url:
        return ScrapedPost(
            media_type="video",
            video_url=video_url,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=thumbnail,
        )
    if image_urls or is_slideshow:
        if not image_urls and thumbnail:
            image_urls = [thumbnail]
        if not image_urls:
            raise ExtractorError(f"apify tiktok slideshow has no images for {redacted}")
        return ScrapedPost(
            media_type="carousel" if len(image_urls) > 1 else "image",
            image_urls=image_urls,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=thumbnail or image_urls[0],
        )
    raise ExtractorError(f"apify tiktok item has no playable media for {redacted}")
