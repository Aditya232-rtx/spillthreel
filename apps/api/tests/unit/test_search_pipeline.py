"""Unit tests for the RAG search pipeline (POST /v1/search).

Fully mocked retriever/answerer/card-loader — no live creds, no DB.
Covers the contract's guard rails:
  * guard strips citations pointing outside the retriever set
  * zero-hits (incl. all-below-threshold) → exact no-match + empty cards
  * out-of-range [n] in the text → extractive fallback + meta.fallback
"""

from __future__ import annotations

from app.services.llm.answer import (
    NO_MATCH_TEXT,
    AnswerCitation,
    AnswerHit,
    AnswerResult,
)
from app.services.memory.base import MemoryHit, SearchFilters
from app.services.search.pipeline import (
    CardFields,
    SearchResponse,
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

    async def generate(self, query: str, hits: list[AnswerHit]) -> AnswerResult:
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


async def test_guard_strips_bad_citations() -> None:
    store = FakeStore(_hits("a", "b"))
    answer = AnswerResult(
        text="Both saves cover pasta [1] [2].",
        citations=[
            AnswerCitation(item_id="a", index=1),
            AnswerCitation(item_id="ghost", index=2),  # ∉ retriever set
        ],
    )
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=store,  # type: ignore[arg-type]
        answerer=FakeAnswerer(answer),
        card_loader=FakeLoader(_fields_for("a", "b")),
    )
    assert [c.item_id for c in result.citations] == ["a"]
    assert result.meta.fallback is False
    assert len(result.cards) == 2
    assert result.cards[0].deep_link == "app://item/a"
    assert result.cards[0].score == 0.9
    assert result.cards[0].url == "https://example.com/s/a"


async def test_zero_hits_returns_no_match() -> None:
    result: SearchResponse = await search(
        "pasta",
        _filters(),
        "user-1",
        store=FakeStore([]),  # type: ignore[arg-type]
        answerer=FakeAnswerer(AnswerResult(text="should never surface", citations=[])),
        card_loader=FakeLoader({}),
    )
    assert result.text == NO_MATCH_TEXT
    assert result.cards == []
    assert result.citations == []
    assert result.meta.fallback is False
    assert result.meta.hits == 0


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
