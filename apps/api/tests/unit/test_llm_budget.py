"""Unit tests for the Gemini cost guard (F-03, TRD §14.3 honest minimal).

No DB, no network, no creds: estimates are pure; the hard-cap check runs
against a stubbed settings object; the 429 mapping is a mocked-search
route test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.api.v1.search as search_module
import app.services.llm.budget as budget_module
import app.services.search.pipeline as search_pipeline_module
from app.auth.middleware import _current_user
from app.db.engine import get_session
from app.db.models import Profile
from app.main import create_app
from app.services.llm.budget import (
    BudgetExceededError,
    check_hard_cap,
    check_search_budget,
    estimate_search_cost_usd,
)
from app.services.memory.base import SearchFilters

_TEST_USER_ID = "user-1"


class TestEstimate:
    def test_bounded_and_monotonic(self) -> None:
        base = estimate_search_cost_usd(10, 1)
        assert base > 0
        assert estimate_search_cost_usd(500, 20) >= estimate_search_cost_usd(10, 1)
        # Over-limit inputs clamp to the caps — never grow unbounded.
        assert estimate_search_cost_usd(10_000, 1_000) == estimate_search_cost_usd(500, 20)

    def test_single_request_cost_is_cents_not_dollars(self) -> None:
        # Sanity: one search costs a fraction of a cent — the per-request
        # hard cap ($1.00 default) is a backstop, not a daily budget.
        assert estimate_search_cost_usd(500, 20) < 0.10


class TestHardCap:
    def test_under_cap_passes(self) -> None:
        check_hard_cap(0.01)  # default $1.00 cap — must not raise

    def test_over_cap_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _TinyCap:
            per_user_daily_gemini_hard_cap_usd = 0.000001

        monkeypatch.setattr(budget_module, "get_settings", lambda: _TinyCap())
        with pytest.raises(BudgetExceededError):
            check_hard_cap(0.01)

    def test_check_search_budget_enforces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _TinyCap:
            per_user_daily_gemini_hard_cap_usd = 0.000001

        monkeypatch.setattr(budget_module, "get_settings", lambda: _TinyCap())
        with pytest.raises(BudgetExceededError):
            check_search_budget("hello", 8, _TEST_USER_ID)

    def test_check_search_budget_returns_estimate(self) -> None:
        estimated = check_search_budget("hello", 8, _TEST_USER_ID)
        assert estimated == estimate_search_cost_usd(len("hello"), 8)


class TestPipelinePreflight:
    async def test_budget_exceeded_bubbles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _TinyCap:
            per_user_daily_gemini_hard_cap_usd = 0.000001

        monkeypatch.setattr(budget_module, "get_settings", lambda: _TinyCap())
        with pytest.raises(BudgetExceededError):
            await search_pipeline_module.search(
                "hello",
                SearchFilters(),
                _TEST_USER_ID,
                8,
                store=AsyncMock(return_value=[]),
            )


class TestSearchRoute429:
    def test_budget_exceeded_maps_to_429(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _override_user() -> Profile:
            return Profile(id=_TEST_USER_ID, display_name="Test User")

        async def _override_session() -> AsyncIterator[Any]:
            yield AsyncMock()

        async def _boom(*args: Any, **kwargs: Any) -> Any:
            raise BudgetExceededError("estimated cost $9.99 exceeds cap")

        monkeypatch.setattr(search_module, "search", _boom)

        app = create_app()
        app.dependency_overrides[_current_user] = _override_user
        app.dependency_overrides[get_session] = _override_session
        client = TestClient(app)

        resp = client.post("/v1/search", json={"query": "hello"})

        assert resp.status_code == 429
        assert resp.json()["detail"] == "gemini budget exceeded"
