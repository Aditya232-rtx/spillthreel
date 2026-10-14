"""Per-request Gemini cost guard (TRD §14.3 — honest minimal).

TRD §14.3 wants a per-user daily budget ($0.50 alarm, $1.00 hard 429).
True daily enforcement needs a persistent per-day spend counter — that is
a new table + migration, explicitly out of scope here (no-migration
rule). There is no usage table/log in the schema today (verified: no
spend/cost/usage columns in models.py or migrations), and an in-memory
counter would reset on every revision — worse than no counter because it
pretends to enforce while leaking across deploys.

So this module is the honest minimal that honors "no migration":

  * a hard per-REQUEST cost ceiling derived from the existing
    `per_user_daily_gemini_hard_cap_usd` setting — a single call must never
    cost more than the daily hard cap on its own (backstop, not a budget);
  * deterministic per-operation USD estimates (documented approximations
    from gemini.py / TRD §7.5) logged on every LLM call for Sentry/cost
    visibility;
  * a pre-flight hook for the user-facing LLM entry point (POST /v1/search)
    that 429s when the estimate breaches the hard cap.

DEFERRED (needs migration): cumulative daily spend — add a
`llm_usage_daily(user_id, day, spend_usd)` table, increment it per call in
this module, and check the sum against `per_user_daily_gemini_budget_usd`
(alarm) / `per_user_daily_gemini_hard_cap_usd` (429) at each entry point
(search, saves→ingest, enhance, backfill-classifier if it ever calls LLM).
"""

from __future__ import annotations

from app.observability.logging import get_logger
from app.settings import get_settings

_logger = get_logger(__name__)


class BudgetExceededError(RuntimeError):
    """Single-request estimate breached the per-request hard cap (→ 429)."""


# Documented approximations (not metered billing - for guard + visibility):
#   * summarize $0.008-$0.015 per short reel at Flash-Lite (gemini.py
#     docstring; TRD §7.5) -> midpoint $0.012.
#   * embed $0.15/1M tokens (GeminiEmbeddingModel docstring) -> a query +
#     top-k answer context is noise next to the hard cap -> $0.00005.
#   * answer Flash-Lite JSON (≤2048 out tokens) -> $0.002 base + per-hit
#     context at $0.0001/hit.
ESTIMATED_SUMMARIZE_USD = 0.012
ESTIMATED_EMBED_USD = 0.00005
ESTIMATED_ANSWER_BASE_USD = 0.002
ESTIMATED_ANSWER_PER_HIT_USD = 0.0001

# Hard per-request input caps (mirror the route-level Pydantic limits so a
# single call's cost stays bounded even before the USD estimate runs).
MAX_QUERY_CHARS = 500
MAX_TOP_K = 20


def estimate_search_cost_usd(query_chars: int, top_k: int) -> float:
    """Estimated USD for one POST /v1/search (embed + answer)."""
    bounded_chars = max(0, min(query_chars, MAX_QUERY_CHARS))
    bounded_k = max(0, min(top_k, MAX_TOP_K))
    # Query length barely moves embed cost at these prices; keep the length
    # term so the estimate stays monotonic and honest.
    embed = ESTIMATED_EMBED_USD * (1.0 + bounded_chars / float(MAX_QUERY_CHARS))
    answer = ESTIMATED_ANSWER_BASE_USD + bounded_k * ESTIMATED_ANSWER_PER_HIT_USD
    return round(embed + answer, 6)


def estimate_summarize_cost_usd(num_frames: int, has_audio: bool) -> float:
    """Estimated USD for one Gemini summarize call (ingest/enhance)."""
    bounded_frames = max(0, min(num_frames, 10))
    cost = ESTIMATED_SUMMARIZE_USD * (0.5 + 0.05 * bounded_frames)
    if has_audio:
        cost += 0.002
    return round(cost, 6)


def check_hard_cap(estimated_usd: float) -> None:
    """Raise BudgetExceededError when one request alone costs over the hard cap."""
    hard_cap = get_settings().per_user_daily_gemini_hard_cap_usd
    if estimated_usd > hard_cap:
        raise BudgetExceededError(
            f"estimated cost ${estimated_usd:.4f} exceeds per-request hard cap ${hard_cap:.2f}"
        )


def log_estimated_cost(operation: str, user_id: str, estimated_usd: float) -> None:
    """Emit a structured cost line for Sentry/log-based spend visibility."""
    _logger.info(
        "llm.estimated_cost",
        operation=operation,
        user_id=user_id,
        estimated_usd=estimated_usd,
    )


def check_search_budget(query: str, top_k: int, user_id: str) -> float:
    """Pre-flight for POST /v1/search: cap inputs, estimate, log, enforce.

    Returns the estimate. Raises BudgetExceededError (route maps to 429) when
    the single-request estimate breaches the hard cap.
    """
    bounded_top_k = max(1, min(top_k, MAX_TOP_K))
    bounded_query = query[:MAX_QUERY_CHARS]
    estimated = estimate_search_cost_usd(len(bounded_query), bounded_top_k)
    log_estimated_cost("search", user_id, estimated)
    check_hard_cap(estimated)
    return estimated
