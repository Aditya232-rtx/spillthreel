"""ScrapeCreators provider — plain sync REST (Part D).

Vendor truth (read 2026-10-11):
* Base ``https://api.scrapecreators.com``, auth header ``x-api-key``.
  Docs: https://docs.scrapecreators.com/introduction
* Instagram: ``GET /v1/instagram/post?url=<post/reel URL>``.
  Docs: https://docs.scrapecreators.com/v1/instagram/post
  Response ``{success, credits_*, data: {xdt_shortcode_media: {...}}}``
  with ``video_url`` / ``display_url`` / ``thumbnail_src`` /
  ``video_duration`` / ``owner{username, full_name}`` /
  ``edge_media_to_caption.edges[0].node.text``. Carousel sidecar-children
  key is UNCERTAIN (docs say "carousel sidecar children when applicable"
  without naming the key) — parser tries several candidates defensively.
* TikTok: ``GET /v2/tiktok/video?url=<video/photo URL>``.
  Docs: https://docs.scrapecreators.com/v2/tiktok/video
  Response ``{success, aweme_detail: {desc, author{unique_id, nickname},
  video{download_no_watermark_addr.url_list, play_addr.url_list,
  has_watermark, duration, cover.url_list}, music, image_post_info}}``.
  Docs prescribe no-watermark preference: use
  ``download_no_watermark_addr.url_list[0]`` when present, else
  ``play_addr.url_list[0]`` when ``has_watermark`` is false. ``duration``
  units are UNCERTAIN (example ``173675`` reads as ms) — parsed
  defensively (ms→s heuristic documented in code). Photo/slideshow
  ``image_post_info`` shape is UNCERTAIN — parsed best-effort.
* TikTok availability caveat (docs /tiktok-post-availability): a single
  404 ``not_found`` does NOT prove global deletion — caller keeps the
  last known record and retries with a region. We still map 404 to
  SourceGoneError per the Part D taxonomy (pipeline marks source_gone),
  but flag this ambiguity in the Part D report.

We never send ``download_media=true`` (10-credit Supabase-URL mode) —
we need raw CDN URLs for our own Part C downloader.
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

_PROVIDER_NAME = "scrapecreators"
_DEFAULT_BASE_URL = "https://api.scrapecreators.com"
_CONNECT_TIMEOUT_S = 5.0
_READ_TIMEOUT_S = 60.0

_GONE_MARKERS = (
    "not found",
    "not_found",
    "deleted",
    "removed",
    "private",
    "unavailable",
    "no longer",
    "could not be retrieved",
)
_QUOTA_MARKERS = (
    "credit",
    "quota",
    "out of credits",
    "insufficient",
    "unauthorized",
    "unauthenticated",
    "forbidden",
    "api key",
    "apikey",
    "x-api-key",
    "402",
    "401",
    "403",
)


def _base_url() -> str:
    raw = os.environ.get("SCRAPECREATORS_BASE_URL", "").strip()
    if raw:
        return raw.rstrip("/")
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "scrapecreators_base_url", None)
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip("/")
    except Exception:
        pass
    return _DEFAULT_BASE_URL


def _api_key() -> str | None:
    raw = os.environ.get("SCRAPECREATORS_API_KEY", "").strip()
    if raw:
        return raw
    try:
        from app.settings import get_settings  # deferred: integrator owns field

        value = getattr(get_settings(), "scrapecreators_api_key", None)
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


def _endpoint_for(platform: Platform) -> str:
    if platform == "instagram":
        return "/v1/instagram/post"
    if platform == "tiktok":
        return "/v2/tiktok/video"
    raise ExtractorError(f"scrapecreators: unsupported platform {platform!r}")


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
        return ProviderQuotaError(f"scrapecreators quota/auth: {message[:200]}")
    if any(m in lowered for m in _GONE_MARKERS):
        return SourceGoneError(f"scrapecreators source gone: {message[:200]}")
    return None


def _extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in ("error", "message", "msg", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = value.get("message")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return ""


class ScrapeCreatorsProvider:
    """ScraperProvider impl for ScrapeCreators (sync REST)."""

    name: str = _PROVIDER_NAME

    async def fetch(self, platform: Platform, url: str) -> ScrapedPost:
        endpoint = _endpoint_for(platform)
        key = _api_key()
        if not key:
            raise ExtractorError("scrapecreators api key not configured")
        started = time.monotonic()
        status = "error"
        try:
            async with get_scraper_semaphore():
                return await self._fetch_with_retry(platform, endpoint, url, key)
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

    async def _fetch_with_retry(
        self, platform: Platform, endpoint: str, url: str, key: str
    ) -> ScrapedPost:
        redacted = _redact(url)
        target = f"{_base_url()}{endpoint}"
        params: dict[str, str] = {"url": url}
        headers = {"x-api-key": key, "Accept": "application/json"}
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
                    f"scrapecreators network failed for {redacted}: {type(e).__name__}"
                ) from e
            except httpx.HTTPError as e:
                raise ExtractorError(
                    f"scrapecreators request failed for {redacted}: {type(e).__name__}"
                ) from e

            if resp.status_code == 429 and attempt == 0:
                await asyncio.sleep(_parse_retry_after(resp.headers.get("retry-after")))
                last_error = ExtractorError("429 rate limited")
                continue
            if resp.status_code in (401, 402, 403):
                raise ProviderQuotaError(
                    f"scrapecreators quota/auth (http {resp.status_code}) for {redacted}"
                )
            if resp.status_code == 404:
                raise SourceGoneError(f"scrapecreators 404 for {redacted}")
            if 500 <= resp.status_code <= 599 and attempt == 0:
                last_error = ExtractorError(f"http {resp.status_code}")
                await asyncio.sleep(random.uniform(0.2, 0.8))
                continue
            if resp.status_code < 200 or resp.status_code >= 300:
                message = ""
                try:
                    message = _extract_error_message(resp.json())
                except Exception:
                    message = ""
                if message:
                    mapped = _classify_payload_error(message)
                    if mapped is not None:
                        # Re-prefix with redacted URL for debuggability.
                        raise type(mapped)(f"{mapped} [{redacted}]") from None
                if resp.status_code == 400 and message:
                    lowered = message.lower()
                    if any(m in lowered for m in _GONE_MARKERS):
                        raise SourceGoneError(f"scrapecreators source gone for {redacted}")
                raise ExtractorError(
                    f"scrapecreators http {resp.status_code} for {redacted}"
                    + (f": {message[:160]}" if message else "")
                )
            try:
                payload: Any = resp.json()
            except ValueError as e:
                raise ExtractorError(f"scrapecreators invalid JSON for {redacted}") from e
            if isinstance(payload, dict) and payload.get("success") is False:
                message = _extract_error_message(payload) or "request not successful"
                mapped = _classify_payload_error(message)
                if mapped is not None:
                    raise type(mapped)(f"{mapped} [{redacted}]") from None
                raise ExtractorError(f"scrapecreators unsuccessful for {redacted}: {message[:160]}")
            post = (
                _parse_instagram(payload, redacted)
                if platform == "instagram"
                else _parse_tiktok(payload, redacted)
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
        raise ExtractorError(f"scrapecreators retry exhausted for {redacted}: {last_error}")


def _first_str(obj: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_url_list(value: Any) -> str | None:
    if isinstance(value, dict):
        items = value.get("url_list")
        if isinstance(items, list):
            for entry in items:
                if isinstance(entry, str) and entry.strip().startswith("http"):
                    return entry.strip()
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, str) and entry.strip().startswith("http"):
                return entry.strip()
    if isinstance(value, str) and value.strip().startswith("http"):
        return value.strip()
    return None


def _coerce_duration_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        if num < 0:
            return None
        # TikTok `video.duration` example (173675) reads as milliseconds;
        # values > ~10_000s are implausible for a short, so treat large
        # magnitudes as ms. UNCERTAIN — heuristic, documented.
        if num > 10000:
            return num / 1000.0
        return num
    return None


def _parse_instagram(payload: dict[str, Any], redacted: str) -> ScrapedPost:
    data = payload.get("data")
    media: Any = None
    if isinstance(data, dict):
        media = data.get("xdt_shortcode_media") or data.get("shortcode_media")
    if not isinstance(media, dict):
        raise ExtractorError(f"scrapecreators missing xdt_shortcode_media for {redacted}")

    typename = str(media.get("__typename") or "")
    is_video = media.get("is_video") is True
    product_type = str(media.get("product_type") or "")
    video_url = _first_str(media, "video_url")
    display_url = _first_str(media, "display_url", "thumbnail_src")
    duration = _coerce_duration_seconds(media.get("video_duration"))

    caption: str | None = None
    edge_caption = media.get("edge_media_to_caption")
    if isinstance(edge_caption, dict):
        edges = edge_caption.get("edges")
        if isinstance(edges, list) and edges:
            node = edges[0].get("node") if isinstance(edges[0], dict) else None
            if isinstance(node, dict):
                text = node.get("text")
                if isinstance(text, str) and text.strip():
                    caption = text.strip()

    owner_username: str | None = None
    owner_name: str | None = None
    owner = media.get("owner")
    if isinstance(owner, dict):
        owner_username = _first_str(owner, "username")
        owner_name = _first_str(owner, "full_name", "fullName")

    # Carousel children: exact key UNCERTAIN. Try documented-adjacent
    # candidates defensively; each child may carry display/video URLs.
    image_urls: list[str] = []
    carousel_video: str | None = None
    for key in (
        "edge_sidecar_to_children",
        "edge_sidecar_children",
        "sidecar_children",
        "carousel_media",
        "children",
        "child_posts",
    ):
        node = media.get(key)
        child_edges: Any = node.get("edges") if isinstance(node, dict) else node
        if not isinstance(child_edges, list):
            continue
        for entry in child_edges:
            child = entry.get("node") if isinstance(entry, dict) else None
            if not isinstance(child, dict):
                if isinstance(entry, dict):
                    child = entry
                else:
                    continue
            child_video = _first_str(child, "video_url")
            if child_video and carousel_video is None:
                carousel_video = child_video
            for img_key in ("display_url", "thumbnail_src", "image_url", "url"):
                img = child.get(img_key)
                if isinstance(img, str) and img.strip().startswith("http"):
                    if img.strip() not in image_urls:
                        image_urls.append(img.strip())
                    break
        if image_urls or carousel_video:
            break

    is_carousel = (
        "Sidecar" in typename
        or "carousel" in product_type.lower()
        or bool(image_urls and (video_url or carousel_video))
        or (len(image_urls) > 1)
    )
    if is_carousel:
        if video_url is None:
            video_url = carousel_video
        if display_url and display_url not in image_urls:
            image_urls.insert(0, display_url)
        return ScrapedPost(
            media_type="carousel",
            video_url=video_url,
            image_urls=image_urls,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=display_url,
        )
    if is_video or video_url:
        if not video_url:
            raise ExtractorError(f"scrapecreators video missing video_url for {redacted}")
        return ScrapedPost(
            media_type="video",
            video_url=video_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=display_url,
        )
    if display_url:
        return ScrapedPost(
            media_type="image",
            image_urls=[display_url],
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=display_url,
        )
    if image_urls:
        return ScrapedPost(
            media_type="image",
            image_urls=image_urls,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=image_urls[0],
        )
    raise ExtractorError(f"scrapecreators no media urls for {redacted}")


def _parse_tiktok(payload: dict[str, Any], redacted: str) -> ScrapedPost:
    detail = payload.get("aweme_detail")
    if not isinstance(detail, dict):
        # Some trims nest under data.aweme_detail — accept defensively.
        data = payload.get("data")
        if isinstance(data, dict) and isinstance(data.get("aweme_detail"), dict):
            detail = data["aweme_detail"]
    if not isinstance(detail, dict):
        message = _extract_error_message(payload)
        if message:
            mapped = _classify_payload_error(message)
            if mapped is not None:
                raise type(mapped)(f"{mapped} [{redacted}]") from None
        raise ExtractorError(f"scrapecreators missing aweme_detail for {redacted}")

    caption = _first_str(detail, "desc")
    author = detail.get("author")
    owner_username: str | None = None
    owner_name: str | None = None
    if isinstance(author, dict):
        owner_username = _first_str(author, "unique_id", "uniqueId", "name")
        owner_name = _first_str(author, "nickname", "nickName")

    video = detail.get("video")
    video_url: str | None = None
    thumbnail_url: str | None = None
    duration: float | None = None
    if isinstance(video, dict):
        has_watermark = video.get("has_watermark")
        no_wm = _first_url_list(video.get("download_no_watermark_addr"))
        play = _first_url_list(video.get("play_addr"))
        download = _first_url_list(video.get("download_addr"))
        if no_wm:
            video_url = no_wm
        elif has_watermark is False and play:
            video_url = play
        else:
            video_url = play or download
        thumbnail_url = _first_url_list(video.get("cover")) or _first_url_list(
            video.get("origin_cover")
        )
        duration = _coerce_duration_seconds(video.get("duration"))

    audio_url: str | None = None
    music = detail.get("music")
    if isinstance(music, dict):
        audio_url = _first_url_list(music.get("play_url")) or _first_str(music, "play_url")

    # Photo/slideshow: image_post_info shape UNCERTAIN — collect any
    # http strings under known-adjacent keys defensively.
    image_urls: list[str] = []
    post_info = detail.get("image_post_info")
    if isinstance(post_info, dict):
        for key in ("images", "image_post", "slides", "image_list"):
            items = post_info.get(key)
            if isinstance(items, list):
                for entry in items:
                    found = _first_url_list(entry)
                    if found and found not in image_urls:
                        image_urls.append(found)
    for key in ("images", "mediaUrls", "slideshowImageLinks", "image_urls"):
        items = detail.get(key)
        if isinstance(items, list):
            for entry in items:
                if isinstance(entry, dict):
                    found = _first_url_list(entry.get("downloadLink")) or _first_url_list(
                        entry.get("tiktokLink")
                    )
                    if found and found not in image_urls:
                        image_urls.append(found)
                elif isinstance(entry, str) and entry.strip().startswith("http"):
                    if entry.strip() not in image_urls:
                        image_urls.append(entry.strip())

    if video_url:
        return ScrapedPost(
            media_type="video",
            video_url=video_url,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=thumbnail_url,
        )
    if image_urls:
        if len(image_urls) > 1:
            return ScrapedPost(
                media_type="carousel",
                image_urls=image_urls,
                audio_url=audio_url,
                caption=caption,
                owner_username=owner_username,
                owner_name=owner_name,
                duration_seconds=duration,
                thumbnail_url=thumbnail_url or image_urls[0],
            )
        return ScrapedPost(
            media_type="image",
            image_urls=image_urls,
            audio_url=audio_url,
            caption=caption,
            owner_username=owner_username,
            owner_name=owner_name,
            duration_seconds=duration,
            thumbnail_url=thumbnail_url or image_urls[0],
        )
    raise ExtractorError(f"scrapecreators no playable media for {redacted}")
