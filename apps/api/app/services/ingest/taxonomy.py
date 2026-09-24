"""Canonical taxonomy + category-clarity heuristic.

Used by:
  * The ingest-time classifier (services/ingest/pipeline.py, once
    wired) — maps Gemini's `topics` list onto our canonical set.
  * The IG bulk-import decide_tier() gate (F8.4 + F8.5) — decides
    whether an imported item is clear enough from caption+hashtags to
    text-index, or needs full multimodal processing.

Config values (character thresholds, hashtag-match counts) live here as
module constants and are documented in TRD §8b. When we ship a live
`taxonomy_config.yaml` overlay, this module loads it and merges — the
constants below become defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

# ---------------------------------------------------------------------------
# Canonical taxonomy
# ---------------------------------------------------------------------------

# The fixed set of auto-inferred categories. User-created categories
# extend this at runtime (see trd.md §8b), but the ingest classifier's
# training-time prompt is anchored to just these twelve so the model
# doesn't have to guess about new labels.
CANONICAL_CATEGORIES: tuple[str, ...] = (
    "recipes",
    "workouts",
    "travel",
    "fashion",
    "reading",
    "music",
    "tech",
    "comedy",
    "product",
    "diy",
    "finance",
    "other",
)


@dataclass(frozen=True)
class CategoryRule:
    """Keyword + hashtag lexicon that lets us decide-tier without an LLM.

    Kept minimal — the actual classification at ingest-time is Gemini's
    job; this is a fast pre-filter for the bulk-import path where paying
    Gemini per item is too expensive for a first pass.
    """
    id: str
    keywords: frozenset[str] = field(default_factory=frozenset)
    hashtags: frozenset[str] = field(default_factory=frozenset)


# Lowercase everywhere — matching is case-insensitive.
CATEGORY_RULES: tuple[CategoryRule, ...] = (
    CategoryRule(
        id="recipes",
        keywords=frozenset({
            "recipe", "cook", "bake", "ingredient", "kitchen", "meal",
            "breakfast", "lunch", "dinner", "chef", "protein", "calorie",
            "vegetarian", "vegan",
        }),
        hashtags=frozenset({
            "recipe", "recipes", "cooking", "food", "foodie", "homecook",
            "veganrecipes", "healthyrecipes",
        }),
    ),
    CategoryRule(
        id="workouts",
        keywords=frozenset({
            "workout", "exercise", "reps", "sets", "gym", "cardio",
            "strength", "hiit", "yoga", "pilates", "stretch", "mobility",
        }),
        hashtags=frozenset({
            "workout", "gym", "fitness", "fit", "gymbro", "fitspo",
            "homeworkout", "yoga",
        }),
    ),
    CategoryRule(
        id="travel",
        keywords=frozenset({
            "travel", "trip", "flight", "hotel", "hostel", "visa",
            "backpack", "itinerary", "destination", "beach", "mountain",
        }),
        hashtags=frozenset({
            "travel", "travelgram", "wanderlust", "backpacking",
            "solotravel", "budgettravel",
        }),
    ),
    CategoryRule(
        id="fashion",
        keywords=frozenset({
            "outfit", "ootd", "fit", "style", "wearing", "thrift",
            "streetwear", "haul", "wardrobe",
        }),
        hashtags=frozenset({
            "fashion", "style", "outfit", "ootd", "streetwear",
            "thrifted", "menswear", "womenswear",
        }),
    ),
    CategoryRule(
        id="reading",
        keywords=frozenset({
            "book", "author", "novel", "chapter", "reading list",
            "bookish", "highlighter", "tbr",
        }),
        hashtags=frozenset({
            "book", "books", "bookstagram", "reading", "tbr", "booktok",
        }),
    ),
    CategoryRule(
        id="music",
        keywords=frozenset({
            "song", "album", "artist", "cover", "playlist", "bpm",
            "acoustic", "chord",
        }),
        hashtags=frozenset({
            "music", "song", "cover", "acoustic", "playlist", "newmusic",
        }),
    ),
    CategoryRule(
        id="tech",
        keywords=frozenset({
            "code", "coding", "programmer", "developer", "javascript",
            "python", "react", "startup", "product launch", "ai model",
        }),
        hashtags=frozenset({
            "tech", "coding", "programming", "developer", "webdev",
            "javascript", "python", "ai",
        }),
    ),
    CategoryRule(
        id="comedy",
        keywords=frozenset({
            "joke", "prank", "skit", "meme", "funny", "hilarious",
            "roast", "standup",
        }),
        hashtags=frozenset({
            "funny", "comedy", "meme", "memes", "prank", "skit",
        }),
    ),
    CategoryRule(
        id="product",
        keywords=frozenset({
            "review", "unboxing", "gadget", "recommend", "amazon find",
            "must have", "worth it", "affiliate",
        }),
        hashtags=frozenset({
            "review", "unboxing", "amazonfinds", "producthunt",
            "musthave",
        }),
    ),
    CategoryRule(
        id="diy",
        keywords=frozenset({
            "diy", "handmade", "tutorial", "howto", "craft", "sewing",
            "woodwork", "hack",
        }),
        hashtags=frozenset({
            "diy", "handmade", "crafts", "tutorial", "howto",
        }),
    ),
    CategoryRule(
        id="finance",
        keywords=frozenset({
            "invest", "stock", "portfolio", "budget", "saving", "sip",
            "mutual fund", "credit card", "loan",
        }),
        hashtags=frozenset({
            "finance", "investing", "stocks", "personalfinance",
            "moneytips",
        }),
    ),
)


# Fast lookup index — flattens every keyword/hashtag across all rules
# so `is_category_clear()` can do O(len(caption_tokens)) hits.
_KEYWORD_INDEX: dict[str, str] = {
    kw: rule.id for rule in CATEGORY_RULES for kw in rule.keywords
}
_HASHTAG_INDEX: dict[str, str] = {
    tag: rule.id for rule in CATEGORY_RULES for tag in rule.hashtags
}


# ---------------------------------------------------------------------------
# Tiered processing decision (PRD F8.4, TRD §8.3)
# ---------------------------------------------------------------------------

class Tier(str, Enum):
    """Processing tier chosen for a bulk-imported item."""
    TEXT_INDEXED = "text_indexed"      # caption + hashtags → embedding, no video pull
    FULL_MULTIMODAL = "full_multimodal"  # download + Gemini pass


# TRD §8.5 thresholds — tunable via taxonomy_config.yaml post-launch.
CLARITY_MIN_CAPTION_LENGTH = 40
CLARITY_MIN_HASHTAG_MATCHES = 2


def is_category_clear(
    caption: str | None,
    hashtags: Iterable[str] | None,
) -> bool:
    """Fast, LLM-free "does this obviously fit a canonical category?" check.

    Rules from TRD §8b.3 + §8.5:
      1. Caption length ≥ 40 chars AND contains ≥ 1 taxonomy keyword.
      2. OR ≥ 2 hashtags match canonical taxonomy synonyms.
    Case-insensitive throughout.
    """
    caption_lower = (caption or "").lower()
    if len(caption_lower) >= CLARITY_MIN_CAPTION_LENGTH:
        # Substring match is fine here — we're not building a full NLP
        # tokenizer. False positives (e.g. "unrecipe") are vanishingly
        # rare; false negatives just push the item into full-multimodal
        # which is safe (correct behavior, higher cost).
        if any(kw in caption_lower for kw in _KEYWORD_INDEX):
            return True

    if hashtags:
        matches = 0
        for tag in hashtags:
            if not tag:
                continue
            normalized = tag.lstrip("#").lower()
            if normalized in _HASHTAG_INDEX:
                matches += 1
                if matches >= CLARITY_MIN_HASHTAG_MATCHES:
                    return True

    return False


def decide_tier(
    caption: str | None,
    hashtags: Iterable[str] | None,
    age_days: int,
) -> Tier:
    """Choose processing tier for a bulk-imported item (PRD F8.4).

    Truth table:
      * age ≤ 30 days AND clear    → TEXT_INDEXED (cheap, fine)
      * age ≤ 30 days AND unclear  → FULL_MULTIMODAL (worth the spend)
      * age > 30 days              → TEXT_INDEXED (always — cost-guarded)
    """
    if age_days > 30:
        return Tier.TEXT_INDEXED
    if is_category_clear(caption, hashtags):
        return Tier.TEXT_INDEXED
    return Tier.FULL_MULTIMODAL


def classify_by_keywords(
    caption: str | None,
    hashtags: Iterable[str] | None,
) -> list[str]:
    """Cheap deterministic pre-classification for text-indexed items.

    Returns 0..N canonical category ids inferred from caption + hashtags.
    NOT a replacement for Gemini's classifier — used at bulk-import time
    when we deliberately skip Gemini for cost. Downstream re-runs of the
    LLM classifier (e.g. via user-tap Enhance) will overwrite these
    assignments with confidence-scored ones.
    """
    caption_lower = (caption or "").lower()
    votes: dict[str, int] = {}

    for kw, cat_id in _KEYWORD_INDEX.items():
        if kw in caption_lower:
            votes[cat_id] = votes.get(cat_id, 0) + 1

    if hashtags:
        for tag in hashtags:
            if not tag:
                continue
            normalized = tag.lstrip("#").lower()
            if normalized in _HASHTAG_INDEX:
                cat_id = _HASHTAG_INDEX[normalized]
                # Hashtags weigh more than caption keywords — they're
                # already-normalized user intent.
                votes[cat_id] = votes.get(cat_id, 0) + 2

    if not votes:
        return []

    # Return up to 2 top categories to match the ingest-time cap from
    # TRD §8b.3 (max 2 assignments per item).
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    return [cat_id for cat_id, _ in ranked[:2]]
