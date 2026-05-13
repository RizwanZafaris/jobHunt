"""
agents/g8/synthesizer.py — G8 Node 5: Synthesizer.

Aggregates outputs from offer_parser, market_analyzer,
negotiation_strategist, and risk_detector into a final recommendation
with full markdown rationale.

PERSONA-AS-CRITIC (non-negotiable):
    Before emitting, this node inspects company_persona.banned_keywords
    and company_persona.success_patterns. If the negotiation_script
    contains any banned term, the confidence is downgraded by 0.15
    AND the recommendation is bumped one notch toward "consider".
    The full critique is logged in persona_critique JSONB on the row.

Outputs:
    - recommendation       ∈ {accept, negotiate, decline, consider}
    - confidence           ∈ [0, 1]
    - risk_adjusted_total_comp = total_y1 * (1 - risk_score * 0.4)
    - sleep_on_it_score    ∈ [0, 1]  (high = pause longer)
    - rationale_md         full markdown
    - persona_critique     {banned_keyword_hits, success_pattern_hits, downgraded}

Cost target: ~$0.15.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Final

from agents.g8_state import OfferEvaluationState, make_g8_turn

logger = logging.getLogger(__name__)

_MODEL: Final[str] = "claude-opus-4-5"
_PROVIDER: Final[str] = "anthropic"

_SYSTEM_PROMPT: Final[str] = """You are a career-decision synthesizer. Given:
  - parsed offer compensation breakdown
  - market bands (p25/p50/p75/p90)
  - negotiation strategy (anchor, walkaway, script)
  - risk factors + risk_score
  - any persona critique from the upstream layer

Produce a final structured recommendation:

  recommendation:   one of [accept, negotiate, decline, consider]
                    'consider' = needs more data / sleep on it
  confidence:       float [0, 1], your calibrated confidence
                    in the recommendation
  sleep_on_it_score: float [0, 1], how strongly the candidate should
                    pause (1 = strong "do not decide tonight")
  rationale_md:     a full markdown rationale, 250-500 tokens. Cite the
                    market percentile, risk factors by kind, and
                    negotiation anchor. Use bullet points.

Return strict JSON with those 4 keys. No prose outside the JSON.
"""


def _persona_critic(
    script: str,
    company_persona: dict | None,
) -> dict:
    """Check the negotiation script for banned keywords + success patterns.

    Returns a critique dict regardless of whether any hits exist; downstream
    rolls this into persona_critique JSONB.
    """
    persona = company_persona or {}
    banned = persona.get("banned_keywords", []) or []
    success = persona.get("success_patterns", []) or []
    s = (script or "").lower()

    banned_hits = [b for b in banned if isinstance(b, str) and b.lower() in s]
    success_hits = [p for p in success if isinstance(p, str) and p.lower() in s]
    downgraded = bool(banned_hits)

    return {
        "banned_keyword_hits": banned_hits,
        "success_pattern_hits": success_hits,
        "downgraded": downgraded,
    }


async def synthesizer_node(state: OfferEvaluationState) -> dict:
    """Generate the final recommendation + apply persona-as-critic gate."""
    parsed = state.get("parsed_offer") or {}
    bands = state.get("market_bands") or {}
    risk_score = float(state.get("risk_score") or 0.0)
    total_y1 = parsed.get("total_comp_year_1") or 0
    script = state.get("negotiation_script", "")
    persona = state.get("company_persona") or {}

    # Pre-LLM persona-as-critic on the script we already have.
    critique = _persona_critic(script, persona)

    user_payload = json.dumps(
        {
            "parsed_offer": parsed,
            "market_bands": bands,
            "negotiation_anchor": state.get("negotiation_anchor"),
            "negotiation_walkaway": state.get("negotiation_walkaway"),
            "negotiation_script": script,
            "risk_factors": state.get("risk_factors", []),
            "risk_score": risk_score,
            "persona_critique": critique,
            "archetype_strategy": state.get("archetype_strategy"),
        },
        default=str,
    )[:8000]

    try:
        from agents.llm_router import get_router
    except Exception as exc:
        logger.error("[g8.synthesizer] llm_router unavailable: %s", exc)
        return _heuristic_fallback(state, critique, error=f"router unavailable: {exc}")

    t0 = time.monotonic()
    try:
        result = await get_router().ask(
            provider=_PROVIDER,
            model=_MODEL,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_payload}],
            max_tokens=1500,
            temperature=0.3,
            cache_system=True,
            graph="G8",
            node="synthesizer",
            user_id=state.get("user_id"),
        )
    except Exception as exc:
        logger.error("[g8.synthesizer] LLM failed: %s", exc)
        return _heuristic_fallback(state, critique, error=f"llm error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    text = (getattr(result, "text", "") or "").strip()

    try:
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        synth = json.loads(text)
    except Exception as exc:
        logger.warning("[g8.synthesizer] JSON parse failed: %s text=%r", exc, text[:200])
        return _heuristic_fallback(state, critique, error=f"parse error: {exc}", cost=cost, latency_ms=latency_ms)

    recommendation = synth.get("recommendation") or "consider"
    confidence = float(synth.get("confidence", 0.5) or 0.5)
    sleep_on_it = float(synth.get("sleep_on_it_score", 0.5) or 0.5)
    rationale = synth.get("rationale_md") or ""

    # PERSONA-AS-CRITIC downgrade gate (deterministic, NOT LLM-decided)
    if critique["downgraded"]:
        confidence = max(0.0, confidence - 0.15)
        if recommendation == "accept":
            recommendation = "negotiate"
        elif recommendation == "negotiate":
            recommendation = "consider"
        # decline / consider stay where they are

    # Risk-adjusted total comp (risk discount caps at 40%)
    rat_comp = round(total_y1 * (1.0 - min(risk_score, 1.0) * 0.4), 2) if total_y1 else None

    return {
        "recommendation": recommendation,
        "confidence": max(0.0, min(confidence, 1.0)),
        "risk_adjusted_total_comp": rat_comp,
        "sleep_on_it_score": max(0.0, min(sleep_on_it, 1.0)),
        "rationale_md": rationale,
        "persona_critique": critique,
        "total_cost_usd": cost,
        "total_latency_ms": latency_ms,
        "transcript": [
            make_g8_turn(
                "synthesizer",
                provider=_PROVIDER,
                model=_MODEL,
                input_summary=user_payload[:200],
                output={
                    "recommendation": recommendation,
                    "confidence": confidence,
                    "downgraded": critique["downgraded"],
                },
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        ],
    }


def _heuristic_fallback(
    state: OfferEvaluationState,
    critique: dict,
    *,
    error: str | None = None,
    cost: float = 0.0,
    latency_ms: int = 0,
) -> dict:
    """Deterministic recommendation when the LLM fails.

    Compares offer vs market p50:
      total_y1 >= p75   → accept (or negotiate if persona-downgraded)
      total_y1 >= p50   → negotiate
      total_y1 >= p25   → consider
      else              → decline
    """
    parsed = state.get("parsed_offer") or {}
    bands = state.get("market_bands") or {}
    risk_score = float(state.get("risk_score") or 0.0)
    total_y1 = float(parsed.get("total_comp_year_1") or 0.0)
    p25 = float(bands.get("p25") or 0.0)
    p50 = float(bands.get("p50") or 0.0)
    p75 = float(bands.get("p75") or 0.0)

    if total_y1 >= p75 and p75 > 0:
        rec = "accept"
    elif total_y1 >= p50 and p50 > 0:
        rec = "negotiate"
    elif total_y1 >= p25 and p25 > 0:
        rec = "consider"
    else:
        rec = "decline"

    if critique.get("downgraded"):
        if rec == "accept":
            rec = "negotiate"
        elif rec == "negotiate":
            rec = "consider"

    confidence = 0.35  # heuristic-only; lower than LLM
    rat_comp = round(total_y1 * (1.0 - min(risk_score, 1.0) * 0.4), 2) if total_y1 else None
    rationale = (
        "**Heuristic fallback** — LLM unavailable. "
        f"Offer total y1 ${total_y1:,.0f} vs market p50 ${p50:,.0f} (p75 ${p75:,.0f}). "
        f"Risk score {risk_score:.2f}. "
        f"Persona critique flagged: {len(critique.get('banned_keyword_hits', []))} banned keyword hits."
    )

    return {
        "recommendation": rec,
        "confidence": confidence,
        "risk_adjusted_total_comp": rat_comp,
        "sleep_on_it_score": 0.6,
        "rationale_md": rationale,
        "persona_critique": critique,
        "total_cost_usd": cost,
        "total_latency_ms": latency_ms,
        "transcript": [
            make_g8_turn(
                "synthesizer",
                provider=_PROVIDER,
                model=_MODEL,
                error=error or "heuristic_fallback",
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        ],
    }
