"""
agents/g8_state.py — Shared LangGraph state for the G8 Offer Evaluation Graph.

One TypedDict (`OfferEvaluationState`) flows through every node. Each node
reads the fields it needs and returns a dict patch. LangGraph merges
patches into the running state.

Reducer notes (mirror G7/G9/G11 patterns):
  - `transcript`         uses operator.add  (append-only audit log)
  - `total_cost_usd`     uses operator.add
  - `total_latency_ms`   uses operator.add
  - `cites`              uses operator.add  (cite:knowledge_id breadcrumbs
                          accumulate across all nodes)
  - All other fields are last-write-wins.

Lifecycle (per roadmap §6.1):
    run_g8(application_id, offer_text)
      ↓
    offer_parser           (pure code, $0)
      ↓
    market_analyzer        (Perplexity Sonar + Gemini, ~$0.05)
      ↓
    negotiation_strategist (Claude Opus 4.5, ~$0.20)
      ↓
    risk_detector          (DeepSeek-Reasoner R1, ~$0.10)
      ↓
    synthesizer            (Claude Opus 4.5, ~$0.15)
      ↓
    persist                (pure code, $0)
      ↓
    END

Cost target: ~$0.40/offer (per roadmap). One bad offer decision costs
$50K-$200K, so the LLM spend is rounding error against the upside.

Engineering principles
----------------------
1. persona-as-critic  — synthesizer checks company_persona.banned_keywords +
                        success_patterns before locking in the recommendation.
2. cite:knowledge_id  — every Sonar citation + RAG hit ID is appended to
                        state.cites and persisted in offer_evaluations.cites.
3. Identity-lock      — negotiation_script must anchor on market_p75 from the
                        market_analyzer, NOT a fabricated number. The synth
                        node verifies the anchor is within [p25, p90+10%].
4. user_id NOT NULL   — RLS on offer_evaluations.
5. Cost telemetry     — agent_call_log entries with graph='G8' written
                        automatically by agents/llm_router.py::ask().
6. Prompt caching     — cache_system=True on all Opus calls (negotiation +
                        synth share their system prompts across re-runs).
7. HITL               — recommendation is informational; the user records
                        their actual decision via PATCH /offer/decision.
"""
from __future__ import annotations
from operator import add
from typing import Annotated, Any, Optional, TypedDict


class G8TranscriptTurn(TypedDict, total=False):
    """One row appended to OfferEvaluationState.transcript per node invocation.

    Same shape as G6/G7/G9/G11 turns so the cost alerter aggregator can
    pick it up without special-casing.
    """
    node: str
    iteration: int
    provider: str
    model: str
    input_summary: str
    output: Any
    cost_usd: float
    latency_ms: int
    error: Optional[str]


class ParsedOffer(TypedDict, total=False):
    """Output of offer_parser_node. Every monetary field is in USD."""
    # Compensation components
    base_salary_usd: Optional[float]
    target_bonus_usd: Optional[float]
    equity_grant_usd: Optional[float]      # 4-year amortized (grant / 4)
    sign_on_bonus_usd: Optional[float]
    other_benefits_usd: Optional[float]
    total_comp_year_1: Optional[float]     # base + bonus + (1/4 equity) + signon + benefits
    total_comp_4yr_avg: Optional[float]
    # Source (pre-conversion)
    raw_currency: str                      # the currency the offer was written in
    conversion_rate_to_usd: float          # 1.0 if already USD
    # Metadata
    level: Optional[str]
    title: Optional[str]
    location: Optional[str]
    start_date: Optional[str]              # ISO date
    company: Optional[str]                 # extracted from offer text or app row
    # Parse confidence + provenance
    parse_warnings: list[str]              # e.g. "no equity grant detected"
    raw_text_excerpt: str                  # first 400 chars for debugging


class MarketBands(TypedDict, total=False):
    """Output of market_analyzer_node. p25-p90 are in USD."""
    p25: Optional[float]
    p50: Optional[float]
    p75: Optional[float]
    p90: Optional[float]
    confidence: str                        # "high" | "medium" | "low"
    source: str                            # "perplexity_sonar+gemini" | "comp_cache_fallback"


class CitationRef(TypedDict, total=False):
    """Each entry in OfferEvaluationState.cites — the cite:knowledge_id breadcrumb."""
    kind: str          # "sonar_url" | "comp_cache" | "company_knowledge" | "story_bank"
    id: str            # url | uuid
    title: Optional[str]
    snippet: Optional[str]
    used_by_node: str  # which node first surfaced this cite


class RiskFactor(TypedDict, total=False):
    """Output of risk_detector_node. Each factor is an enumerated risk."""
    kind: str         # "financial_health" | "role_risk" | "vesting_risk" | "geo_risk" | "other"
    severity: float   # [0, 1] — own-factor weight
    description: str
    evidence: Optional[str]  # snippet from company_knowledge or DeepSeek's CoT
    cite_ids: list[str]      # back-pointers into state.cites


class OfferEvaluationState(TypedDict, total=False):
    # ─── Inputs (hydrated by g8_io.run_g8_for_offer) ─────────────────────
    user_id: str                          # uuid; RLS owner
    application_id: str                   # uuid; FK to public.applications
    job_id: Optional[int]                 # FK to public.jobs (optional — some offers come without a job row)
    offer_text: str                       # the raw email / PDF text from the user
    application_row: Optional[dict]       # full applications row (company, role)
    job_row: Optional[dict]               # parent jobs row (JD, comp band)
    company_persona: Optional[dict]       # for persona-as-critic in synthesizer
    company_knowledge: list[dict]         # last 90d for risk_detector RAG
    archetype: Optional[str]              # "IC" | "Manager" | "Staff" — drives negotiation framing
    force: bool                           # bypass wallet guard (closed-app override)

    # ─── offer_parser output ──────────────────────────────────────────────
    parsed_offer: ParsedOffer
    total_comp_year_1: Optional[float]    # surfaced at top-level for readability

    # ─── market_analyzer output ───────────────────────────────────────────
    market_bands: MarketBands
    market_summary: str
    market_citations: list[dict]          # [{url, title, published_at, snippet}]

    # ─── negotiation_strategist output ────────────────────────────────────
    negotiation_anchor: Optional[float]
    negotiation_walkaway: Optional[float]
    negotiation_script: str               # 200-400 token script
    archetype_strategy: str               # IC vs Manager vs Staff framing

    # ─── risk_detector output ─────────────────────────────────────────────
    risk_factors: list[RiskFactor]
    risk_score: float                     # [0, 1] — aggregate weighted score

    # ─── synthesizer output ───────────────────────────────────────────────
    recommendation: str                   # "accept" | "negotiate" | "decline" | "consider"
    confidence: float                     # [0, 1]
    risk_adjusted_total_comp: Optional[float]
    sleep_on_it_score: float              # [0, 1] — emotional vs rational tension
    rationale_md: str                     # full markdown rationale
    persona_critique: Optional[dict]      # {banned_keyword_hits, success_pattern_hits, downgraded}

    # ─── persist output ───────────────────────────────────────────────────
    offer_evaluation_id: Optional[str]    # uuid of the persisted row

    # ─── Telemetry ────────────────────────────────────────────────────────
    cites: Annotated[list[CitationRef], add]    # cite:knowledge_id breadcrumbs
    transcript: Annotated[list[G8TranscriptTurn], add]
    total_cost_usd: Annotated[float, add]
    total_latency_ms: Annotated[int, add]
    error: Optional[str]


def make_g8_turn(
    node: str,
    *,
    iteration: int = 0,
    provider: str = "",
    model: str = "",
    input_summary: str = "",
    output: Any = None,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
    error: Optional[str] = None,
) -> G8TranscriptTurn:
    """Convenience constructor; matches make_g7_turn / make_g11_turn."""
    if isinstance(input_summary, str) and len(input_summary) > 500:
        input_summary = input_summary[:497] + "..."
    return G8TranscriptTurn(
        node=node,
        iteration=iteration,
        provider=provider,
        model=model,
        input_summary=input_summary or "",
        output=output,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        error=error,
    )
