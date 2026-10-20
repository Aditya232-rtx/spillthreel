"""Unit tests for the RAG search pipeline (POST /v1/search).

Fully mocked retriever/answerer/card-loader — no live creds, no DB.
Covers the contract's guard rails:
  * guard strips citations pointing outside the retriever set
  * zero-hits (incl. all-below-threshold) → exact no-match + empty cards
  * out-of-range [n] in the text → extractive fallback + meta.fallback
"""

from __future__ import annotations

import asyncio

import pytest

from app.services.llm.answer import (
    NO_MATCH_TEXT,
    AnswerCitation,
    AnswerHit,
    AnswerResult,
)
from app.services.memory.base import MemoryHit, SearchFilters
from app.services.search.pipeline import (
    _MAX_EVIDENCE_CHARS,
    CardFields,
    SearchResponse,
    SearchTimeoutError,
    _build_answer_hits,
    _filter_weak_lexical_matches,
    search,
)


class FakeStore:
    """Mocked retriever — returns canned MemoryHits."""

    def __init__(self, hits: list[MemoryHit]) -> None:
        self._hits = hits

    async def search(
        self, user_id: str, query: str, filters: SearchFilters, top_k: int = 8
    ) -> list[MemoryHit]:
        return self._hits[:top_k]


class FakeAnswerer:
    """Mocked answerer — returns a canned AnswerResult."""

    model_name = "fake-test-model"

    def __init__(self, result: AnswerResult) -> None:
        self._result = result
        self.calls = 0
        self.received: list[AnswerHit] = []

    async def generate(self, query: str, hits: list[AnswerHit]) -> AnswerResult:
        self.calls += 1
        self.received = hits
        return self._result


class FakeLoader:
    """Mocked card hydration — stands in for the items table."""

    def __init__(self, fields: dict[str, CardFields]) -> None:
        self._fields = fields

    async def load(self, user_id: str, item_ids: list[str]) -> dict[str, CardFields]:
        return {i: self._fields[i] for i in item_ids if i in self._fields}


def _fields_for(*item_ids: str) -> dict[str, CardFields]:
    return {
        item_id: CardFields(
            title=f"Title {item_id}",
            creator="creator",
            platform="instagram",
            thumbnail_url=None,
            source_url=f"https://example.com/s/{item_id}",
        )
        for item_id in item_ids
    }


def _filters() -> SearchFilters:
    return SearchFilters(only_fully_indexed=True)


def _hits(*item_ids: str, score: float = 0.9) -> list[MemoryHit]:
    return [MemoryHit(item_id=i, score=score, matched_chunks=[]) for i in item_ids]


def _evidence_char_count(hit: AnswerHit) -> int:
    return sum(
        len(value)
        for value in (
            hit.title,
            hit.summary,
            hit.transcript,
            hit.on_screen_text,
            hit.caption,
            *hit.hashtags,
        )
        if value is not None
    )


def test_evidence_character_budget_is_shared_across_items_and_hashtags() -> None:
    first = CardFields(
        title="t" * 100,
        platform="instagram",
        summary="s" * 2_400,
        transcript="r" * 2_400,
        on_screen_text="o" * 2_400,
        caption="c" * 2_400,
        hashtags=["a" * 100] * 22,
    )
    second = CardFields(
        title="u",
        platform="instagram",
        hashtags=["b" * 100] * 30,
    )

    result = _build_answer_hits(
        [
            (MemoryHit(item_id="first", score=0.9, matched_chunks=[]), first),
            (MemoryHit(item_id="second", score=0.8, matched_chunks=[]), second),
        ]
    )

    assert [hit.index for hit in result] == [1, 2]
    assert sum(_evidence_char_count(hit) for hit in result) == _MAX_EVIDENCE_CHARS
    assert result[0].hashtags == ["a" * 100] * 22
    assert result[1].title == "u"
    assert result[1].hashtags == ["b" * 99]


def test_evidence_at_exact_character_boundary_is_preserved() -> None:
    fields = CardFields(
        title="t" * 300,
        platform="instagram",
        summary="s" * 2_400,
        transcript="r" * 2_400,
        on_screen_text="o" * 2_400,
        caption="c" * 2_400,
        hashtags=["h" * 100] * 21,
    )

    (result,) = _build_answer_hits(
        [(MemoryHit(item_id="exact", score=0.9, matched_chunks=[]), fields)]
    )

    assert _evidence_char_count(result) == _MAX_EVIDENCE_CHARS
    assert len(result.hashtags) == 21
    assert result.index == 1


def test_remaining_field_budget_is_consumed_before_tags_in_source_order() -> None:
    fields = CardFields(
        title="t" * 300,
        platform="instagram",
        summary="s" * 2_400,
        transcript="r" * 2_400,
        on_screen_text="o" * 2_400,
        caption="c" * 2_400,
        hashtags=["a" * 120] * 30,
    )

    (result,) = _build_answer_hits(
        [(MemoryHit(item_id="boundary", score=0.9, matched_chunks=[]), fields)]
    )

    assert result.title == "t" * 300
    assert result.summary == "s" * 2_400
    assert result.transcript == "r" * 2_400
    assert result.on_screen_text == "o" * 2_400
    assert result.caption == "c" * 2_400
    assert result.hashtags == ["a" * 120] * 17 + ["a" * 60]
    assert _evidence_char_count(result) == _MAX_EVIDENCE_CHARS


def test_empty_evidence_does_not_spend_budget_or_shift_item_indexes() -> None:
    empty = CardFields(
        title=" \n ",
        platform="instagram",
        summary=" \t ",
        hashtags=["  ", ""],
    )
    populated = CardFields(
        title="A save",
        platform="instagram",
        caption="A short caption.",
        hashtags=["food"],
    )

    result = _build_answer_hits(
        [
            (MemoryHit(item_id="empty", score=0.9, matched_chunks=[]), empty),
            (MemoryHit(item_id="populated", score=0.8, matched_chunks=[]), populated),
        ]
    )

    assert [hit.index for hit in result] == [1, 2]
    assert _evidence_char_count(result[0]) == 0
    assert result[0].hashtags == []
    assert result[1].title == "A save"
    assert result[1].caption == "A short caption."
    assert result[1].hashtags == ["food"]


async def test_bad_citation_falls_back_instead_of_preserving_model_text() -> None:
    store = FakeStore(_hits("a", "b"))
    answer = AnswerResult(
        text="Both saves cover pasta [1] [2].",
        citations=[
            AnswerCitation(item_id="a", index=1),
            AnswerCitation(item_id="ghost", index=2),  # ∉ retriever set
        ],
    )
    answerer = FakeAnswerer(answer)
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader(_fields_for("a", "b")),
    )
    assert [c.item_id for c in result.citations] == ["a", "b"]
    assert result.meta.fallback is True
    assert "Both saves cover pasta" not in result.text
    assert len(result.cards) == 2
    assert result.cards[0].deep_link == "app://item/a"
    assert result.cards[0].score == 0.9
    assert result.cards[0].url == "https://example.com/s/a"


async def test_zero_hits_returns_no_match() -> None:
    answerer = FakeAnswerer(AnswerResult(text="should never surface", citations=[]))
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=FakeStore([]),  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader({}),
    )
    assert result.text == NO_MATCH_TEXT
    assert result.cards == []
    assert result.citations == []
    assert result.meta.fallback is False
    assert result.meta.hits == 0
    assert answerer.calls == 0


async def test_total_search_deadline_includes_card_hydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SlowLoader(FakeLoader):
        async def load(self, user_id: str, item_ids: list[str]) -> dict[str, CardFields]:
            await asyncio.sleep(0.05)
            return await super().load(user_id, item_ids)

    monkeypatch.setattr("app.services.search.pipeline.SEARCH_TOTAL_TIMEOUT_S", 0.005)
    with pytest.raises(SearchTimeoutError, match="search deadline exceeded"):
        await search(
            "pasta",
            _filters(),
            "user-1",
            store=FakeStore(_hits("a")),  # type: ignore[arg-type]
            answerer=FakeAnswerer(AnswerResult(text="[1]", citations=[])),
            card_loader=SlowLoader(_fields_for("a")),
        )


async def test_below_threshold_counts_as_zero_hits() -> None:
    store = FakeStore(_hits("a", "b", score=0.2))  # under SCORE_THRESHOLD (0.55)
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=FakeAnswerer(AnswerResult(text="should never surface", citations=[])),
        card_loader=FakeLoader(_fields_for("a", "b")),
    )
    assert result.text == NO_MATCH_TEXT
    assert result.cards == []
    assert result.citations == []


async def test_recipe_query_excludes_visual_text_match_from_cards_and_evidence() -> None:
    """A lettering match in a carousel must not surface for a recipe query."""
    recipe_id = "recipe"
    carousel_id = "graphic-design-carousel"
    store = FakeStore(
        [
            MemoryHit(
                item_id=recipe_id,
                score=0.7272177789776804,
                matched_chunks=[],
            ),
            MemoryHit(
                item_id=carousel_id,
                score=0.692007934907179,
                matched_chunks=[],
            ),
        ]
    )
    fields = {
        recipe_id: CardFields(
            title="Creamy White Sauce Pasta Recipe with Tadka",
            platform="instagram",
            summary="A creamy pasta recipe with a quick tadka finish.",
        ),
        carousel_id: CardFields(
            title="Six Graphic Design Styles to Experiment With This Month",
            platform="instagram",
            summary="A graphic design carousel featuring the word paste in its lettering.",
            on_screen_text="PASTE",
        ),
    }
    answerer = FakeAnswerer(
        AnswerResult(
            text="The pasta recipe is the relevant save [1]. The lettering carousel is [2].",
            citations=[
                AnswerCitation(item_id=recipe_id, index=1),
                AnswerCitation(item_id=carousel_id, index=2),
            ],
        )
    )

    result = await search(
        "What was that paste recipe",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader(fields),
    )

    assert [card.item_id for card in result.cards] == [recipe_id]
    assert [citation.item_id for citation in result.citations] == [recipe_id]
    assert [hit.item_id for hit in answerer.received] == [recipe_id]
    assert all("graphic-design" not in (hit.title or "") for hit in answerer.received)


def test_relevance_tiebreaker_keeps_distant_semantic_match() -> None:
    candidates = [
        (
            MemoryHit(item_id="recipe", score=0.727, matched_chunks=[]),
            CardFields(title="Pasta recipe", platform="instagram"),
        ),
        (
            MemoryHit(item_id="other", score=0.676, matched_chunks=[]),
            CardFields(
                title="Paste lettering",
                summary="A visual design study.",
                platform="instagram",
            ),
        ),
    ]

    result = _filter_weak_lexical_matches("paste recipe", candidates)

    assert [hit.item_id for hit, _ in result] == ["recipe", "other"]


def test_relevance_tiebreaker_skips_multilingual_evidence() -> None:
    candidates = [
        (
            MemoryHit(item_id="recipe", score=0.727, matched_chunks=[]),
            CardFields(title="Pasta recipe", platform="instagram"),
        ),
        (
            MemoryHit(item_id="other", score=0.692, matched_chunks=[]),
            CardFields(
                title="Rápida receta de pasta",
                summary="Una receta fácil y deliciosa.",
                platform="instagram",
            ),
        ),
    ]

    result = _filter_weak_lexical_matches("paste recipe", candidates)

    assert [hit.item_id for hit, _ in result] == ["recipe", "other"]


async def test_out_of_range_citation_triggers_fallback() -> None:
    store = FakeStore(_hits("a", "b"))
    answer = AnswerResult(
        text="The third save says otherwise [3].",  # only 2 cards
        citations=[AnswerCitation(item_id="a", index=1)],
    )
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=FakeAnswerer(answer),
        card_loader=FakeLoader(_fields_for("a", "b")),
    )
    assert result.meta.fallback is True
    assert result.text == "Found 2 saves: [1] Title a; [2] Title b."
    assert [(c.item_id, c.index) for c in result.citations] == [("a", 1), ("b", 2)]


async def test_valid_answer_passes_through() -> None:
    store = FakeStore(_hits("a", "b"))
    answer = AnswerResult(
        text="Both saves cover pasta [1] [2].",
        citations=[
            AnswerCitation(item_id="a", index=1, span="pasta"),
            AnswerCitation(item_id="b", index=2),
        ],
    )
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        top_k=8,
        store=store,  # type: ignore[arg-type]
        answerer=FakeAnswerer(answer),
        card_loader=FakeLoader(_fields_for("a", "b")),
    )
    assert result.text == "Both saves cover pasta [1] [2]."
    assert result.meta.fallback is False
    assert result.meta.model == "fake-test-model"
    assert result.meta.hits == 2
    assert result.meta.latency_ms >= 0
    assert [c.item_id for c in result.cards] == ["a", "b"]
    assert [(c.item_id, c.index) for c in result.citations] == [("a", 1), ("b", 2)]
    assert result.citations[0].span is None  # model quote is absent from hydrated evidence


async def test_citation_span_is_kept_only_when_present_in_source_evidence() -> None:
    store = FakeStore(_hits("a"))
    fields = _fields_for("a")
    fields["a"].summary = "Pasta with lemon and herbs."
    answer = AnswerResult(
        text="A bright pasta dish [1].",
        citations=[AnswerCitation(item_id="a", index=1, span="Pasta with lemon")],
    )
    result = await search(
        "pasta",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=FakeAnswerer(answer),
        card_loader=FakeLoader(fields),
    )
    assert result.meta.fallback is False
    assert result.citations[0].span == "Pasta with lemon"


async def test_summary_and_transcript_are_passed_as_bounded_evidence() -> None:
    store = FakeStore(_hits("a"))
    answer = AnswerResult(
        text="The clip explains sourdough [1].",
        citations=[AnswerCitation(item_id="a", index=1)],
    )
    loader = FakeLoader(
        {
            "a": CardFields(
                title="Bread",
                creator=None,
                platform="instagram",
                summary="A guide to sourdough starter.",
                transcript="Feed the starter with flour and water.",
            )
        }
    )
    answerer = FakeAnswerer(answer)
    await search(
        "sourdough",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=loader,
    )
    (hit,) = answerer.received
    assert hit.summary == "A guide to sourdough starter."
    assert hit.transcript == "Feed the starter with flour and water."
    assert hit.index == 1


async def test_deleted_hydration_gap_reindexes_remaining_items_contiguously() -> None:
    store = FakeStore(_hits("deleted", "b", "c"))
    answerer = FakeAnswerer(
        AnswerResult(
            text="The clips discuss pasta [1] and bread [2].",
            citations=[
                AnswerCitation(item_id="b", index=1),
                AnswerCitation(item_id="c", index=2),
            ],
        )
    )
    result = await search(
        "food",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader(_fields_for("b", "c")),
    )
    assert [(hit.item_id, hit.index) for hit in answerer.received] == [("b", 1), ("c", 2)]
    assert [(citation.item_id, citation.index) for citation in result.citations] == [
        ("b", 1),
        ("c", 2),
    ]
    assert result.meta.fallback is False


async def test_missing_citation_for_text_marker_falls_back() -> None:
    answerer = FakeAnswerer(
        AnswerResult(
            text="This save mentions pasta [1].",
            citations=[],
        )
    )
    result = await search(
        "pasta",
        _filters(),
        "user-1",
        store=FakeStore(_hits("a")),  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader(_fields_for("a")),
    )
    assert result.meta.fallback is True
    assert result.text == "Found 1 saves: [1] Title a."


async def test_wrong_id_for_valid_index_falls_back() -> None:
    answerer = FakeAnswerer(
        AnswerResult(
            text="This save mentions pasta [1].",
            citations=[AnswerCitation(item_id="ghost", index=1)],
        )
    )
    result = await search(
        "pasta",
        _filters(),
        "user-1",
        store=FakeStore(_hits("a")),  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader(_fields_for("a")),
    )
    assert result.meta.fallback is True
    assert result.text == "Found 1 saves: [1] Title a."


async def test_empty_evidence_uses_fallback_without_calling_answerer() -> None:
    answerer = FakeAnswerer(AnswerResult(text="unsupported [1]", citations=[]))
    result = await search(
        "anything",
        _filters(),
        "user-1",
        store=FakeStore(_hits("a")),  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader({"a": CardFields(title="", platform="instagram")}),
    )
    assert answerer.calls == 0
    assert result.meta.fallback is True
    assert result.text == "Found 1 saves: [1] Untitled save."


async def test_no_active_cards_after_hydration_skips_answerer() -> None:
    answerer = FakeAnswerer(AnswerResult(text="should not surface", citations=[]))
    result = await search(
        "anything",
        _filters(),
        "user-1",
        store=FakeStore(_hits("deleted")),  # type: ignore[arg-type]
        answerer=answerer,
        card_loader=FakeLoader({}),
    )
    assert result.text == NO_MATCH_TEXT
    assert result.cards == []
    assert result.citations == []
    assert answerer.calls == 0


async def test_cards_carry_source_url_with_deep_link_primary() -> None:
    store = FakeStore(_hits("a"))
    answer = AnswerResult(
        text="Pasta save [1].",
        citations=[AnswerCitation(item_id="a", index=1)],
    )
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=FakeAnswerer(answer),
        card_loader=FakeLoader(_fields_for("a")),
    )
    (card,) = result.cards
    assert card.deep_link == "app://item/a"  # primary opener, unchanged
    assert card.url == "https://example.com/s/a"  # https fallback


async def test_card_url_none_when_loader_has_no_source() -> None:
    store = FakeStore(_hits("a"))
    answer = AnswerResult(
        text="Pasta save [1].",
        citations=[AnswerCitation(item_id="a", index=1)],
    )
    bare = {"a": CardFields(title="Title a", creator=None, platform="instagram")}
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=FakeAnswerer(answer),
        card_loader=FakeLoader(bare),
    )
    (card,) = result.cards
    assert card.url is None
    assert card.deep_link == "app://item/a"
