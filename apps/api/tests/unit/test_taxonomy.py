"""Unit tests for the taxonomy heuristic + decide_tier gate.

The truth-table shape lives in TRD §8b — tests are effectively that
table transcribed as parametrized cases.
"""

from __future__ import annotations

import pytest

from app.services.ingest.taxonomy import (
    Tier,
    classify_by_keywords,
    decide_tier,
    is_category_clear,
)


class TestIsCategoryClear:
    def test_long_caption_with_keyword_is_clear(self) -> None:
        assert is_category_clear(
            "This is my quick 20 min chicken recipe with only 5 ingredients!",
            hashtags=None,
        )

    def test_long_caption_without_keyword_is_unclear(self) -> None:
        # Long enough to pass the length gate but no taxonomy keyword.
        assert not is_category_clear(
            "just vibing on a random tuesday and thought i'd share this thought",
            hashtags=None,
        )

    def test_short_caption_ignored_even_with_keyword(self) -> None:
        # Below 40 chars → caption path can't fire. Hashtags path needs 2+ matches.
        assert not is_category_clear("recipe drop!", hashtags=None)

    def test_two_matching_hashtags_are_clear(self) -> None:
        assert is_category_clear(caption=None, hashtags=["fitness", "gym"])

    def test_one_matching_hashtag_is_unclear(self) -> None:
        assert not is_category_clear(caption=None, hashtags=["fitness", "randomtag"])

    def test_hashtag_prefix_hash_is_stripped(self) -> None:
        assert is_category_clear(caption=None, hashtags=["#fitness", "#gym"])

    def test_case_insensitive(self) -> None:
        assert is_category_clear(caption=None, hashtags=["Fitness", "GYM"])

    def test_none_inputs(self) -> None:
        assert not is_category_clear(caption=None, hashtags=None)

    def test_empty_string_caption(self) -> None:
        assert not is_category_clear(caption="", hashtags=None)


class TestDecideTier:
    @pytest.mark.parametrize(
        "age_days, caption, hashtags, expected",
        [
            # ≤30 days, clear → text-indexed (cheap path)
            (5, "chicken recipe with 5 ingredients under 20 minutes yay", None, Tier.TEXT_INDEXED),
            # ≤30 days, unclear → full multimodal (worth the spend)
            (5, None, None, Tier.FULL_MULTIMODAL),
            (5, "vibes only", None, Tier.FULL_MULTIMODAL),
            # >30 days → text-indexed regardless (cost-guarded)
            (60, None, None, Tier.TEXT_INDEXED),
            (365, "clearly a recipe with ingredients", None, Tier.TEXT_INDEXED),
            # Edge: exactly 30 days is still "recent" per the >30 gate.
            (30, None, None, Tier.FULL_MULTIMODAL),
        ],
    )
    def test_truth_table(
        self,
        age_days: int,
        caption: str | None,
        hashtags: list[str] | None,
        expected: Tier,
    ) -> None:
        assert decide_tier(caption=caption, hashtags=hashtags, age_days=age_days) == expected


class TestClassifyByKeywords:
    def test_no_signal_returns_empty(self) -> None:
        assert classify_by_keywords(caption=None, hashtags=None) == []
        assert classify_by_keywords(caption="a random thought", hashtags=[]) == []

    def test_single_keyword_maps_to_category(self) -> None:
        result = classify_by_keywords("chicken recipe with 5 ingredients", hashtags=None)
        assert result == ["recipes"]

    def test_hashtags_outvote_caption(self) -> None:
        # Caption hits "recipe" once (weight 1). Hashtags hit "gym" +
        # "fitness" (weight 2 each = 4). Workouts should win.
        result = classify_by_keywords("recipe of the day", hashtags=["gym", "fitness"])
        assert result[0] == "workouts"

    def test_cap_at_two_categories(self) -> None:
        # Multiple strong signals across categories — return at most 2.
        result = classify_by_keywords(
            "book about my recipe for gym workouts on my travel trip",
            hashtags=["fitness", "travel", "book"],
        )
        assert len(result) <= 2
