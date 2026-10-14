"""Category backfill — classify existing items into a new user category.

Triggered by POST /v1/categories, which enqueues `category_backfill` with
`{category_id, user_id}` (TRD §8b.4). Honest minimal, no LLM: reuses the
deterministic taxonomy service (`classify_by_keywords`) for canonical
names and a direct token/substring match for arbitrary user labels, then
inserts `item_categories` rows with `assigned_by='classifier'`.

Idempotent: existing assignments are skipped up front and the insert uses
ON CONFLICT DO NOTHING, so re-delivery after completion is a no-op. No
schema change — pure row reads + inserts.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import Category, Item, ItemCategory
from app.observability.logging import get_logger
from app.services.ingest.taxonomy import CANONICAL_CATEGORIES, classify_by_keywords

_logger = get_logger(__name__)

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokens(name: str) -> list[str]:
    """Lowercase alphanumeric tokens, len ≥ 3 (drops noise like 'my')."""
    return [tok for tok in _TOKEN_SPLIT_RE.split(name.lower()) if len(tok) >= 3]


def item_matches_category(
    category_name: str,
    *,
    title: str | None,
    caption: str | None,
    transcript: str | None,
    on_screen_text: str | None,
    hashtags: list[str] | None,
) -> bool:
    """Does this item's text signal the category? Case-insensitive.

    * Canonical taxonomy names (e.g. a user category called "RECIPES"):
      reuse `classify_by_keywords` — the same deterministic pre-filter the
      bulk-import path uses. Match when the lowered category name is among
      the classifier's votes.
    * Arbitrary user labels (e.g. "MEAL PREP"): the keyword lexicon can't
      know them, so match when the full lowered name — or any of its
      ≥3-char tokens — appears in the item text (title/caption/transcript/
      on-screen text) or as a hashtag.
    """
    normalized = category_name.strip().lower()
    if not normalized:
        return False
    if normalized in CANONICAL_CATEGORIES:
        return normalized in classify_by_keywords(caption, hashtags)
    combined = "\n".join(
        part for part in (title, caption, transcript, on_screen_text) if part
    ).lower()
    tags = [(tag or "").lstrip("#").lower() for tag in (hashtags or [])]
    if normalized in combined:
        return True
    return any(token in combined or token in tags for token in _tokens(normalized))


async def backfill_category(category_id: str, user_id: str) -> dict[str, str | int]:
    """Scan the user's items and tag matches with the new category.

    Deferred session import keeps worker-only weight off the api import
    graph (same pattern as the ingest pipeline helpers).
    """
    from app.db.engine import session_scope

    async with session_scope() as session:
        category = (
            await session.execute(
                select(Category).where(Category.id == category_id, Category.user_id == user_id)
            )
        ).scalar_one_or_none()
        if category is None:
            _logger.warning(
                "categories.backfill_missing",
                category_id=category_id,
                user_id=user_id,
            )
            return {"status": "missing", "category_id": category_id, "scanned": 0, "matched": 0}

        items = (await session.execute(select(Item).where(Item.user_id == user_id))).scalars().all()
        existing_rows = (
            (
                await session.execute(
                    select(ItemCategory.item_id).where(ItemCategory.category_id == category_id)
                )
            )
            .scalars()
            .all()
        )
        already = set(existing_rows)

        pending: list[dict[str, str | float | None]] = []
        for item in items:
            if item.id in already:
                continue
            if item_matches_category(
                category.name,
                title=item.title,
                caption=item.caption,
                transcript=item.transcript,
                on_screen_text=item.on_screen_text,
                hashtags=item.hashtags,
            ):
                pending.append(
                    {
                        "item_id": item.id,
                        "category_id": category_id,
                        "assigned_by": "classifier",
                        "confidence": None,
                    }
                )

        matched = 0
        if pending:
            await session.execute(pg_insert(ItemCategory).values(pending).on_conflict_do_nothing())
            await session.commit()
            matched = len(pending)

        _logger.info(
            "categories.backfill_done",
            category_id=category_id,
            user_id=user_id,
            scanned=len(items),
            matched=matched,
        )
        return {
            "status": "ok",
            "category_id": category_id,
            "scanned": len(items),
            "matched": matched,
        }
