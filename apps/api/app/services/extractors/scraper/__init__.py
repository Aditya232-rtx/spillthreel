"""Scraper provider layer for Instagram/TikTok (Part D).

Providers resolve a post URL to direct media URLs + metadata without
downloading bytes themselves. Downloads go through
services.media.download.download_media (Part C).
"""

from __future__ import annotations
