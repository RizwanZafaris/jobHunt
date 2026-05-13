"""
agents/g8/negotiation_strategist.py — G8 Node 3: Negotiation Strategist.

Anchored on `market_bands.p75` from the upstream market_analyzer node.
Produces a 200-400 token negotiation script + walkaway threshold +
archetype-specific framing.

Identity lock: the anchor MUST be inside [p25, p90 * 1.10]. The
synthesizer downstream verifies this; if violated, the synth downgrades
the recommendation.

Uses Anthropic Opus 4.5 with `cache_system=True` so the system prompt
stays cache-warm across re-runs.

Cost target: ~$0.20.
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

_SYSTEM_PROMPT: Final[str] = """You are a negotiation strategist for senior tech roles.
Given a parsed offer + market bands, produce a concise actionable plan:

  - anchor: the target counter (USD total comp). Must be inside the
    market band range [p25, p90 * 1.10]. Prefer p75.
  - walkaway: the floor below which the candidate should decline.
    Typically p25.
  - script: a 200-400 token email/conversation outline the candidate
    can adapt. Open with gratitude, anchor on market data, request
    specific lifts (base / equity / signon), close with timeline.
  - archetype_strategy: tailor framing to one of:
      IC      — emphasize technical impact + market comparables.
      Manager — emphasize scope of report + org-level outcomes.
      Staff   — emphasize cross-team influence + technical depth.

Return strict JSON with these 4 keys. No prose outside the JSON.
"""


def _select_archetype(level: str | None, title: str | None) -> str:
    """Best-effort archetype detection from level / title strings."""
    t = (title or "").lower()
    l = (level or "").lower()
    if "manager" in t or "director" in t or "head" in t or "vp" in t:
        return "Manager"
    if "staff" in l or "principal" in l or "staff" in t or "principal" in t:
        return "Staff"
    return "IC"


async def negotiation_strategist_node(state: OfferEvaluationState) -> dict:
    """Generate negotiation script + anchor + walkaway."""
    bands = state.get("market_bands") or {}
    parsed = state.get("parsed_offer") or {}
    p25 = bands.get("p25")
    p50 = bands.get("p50")
    p75 = bands.get("p75")
    p90 = bands.get("p90")
    total_y1 = parsed.get("total_comp_year_1") or 0
    archetype = state.get("archetype") or _select_archetype(parsed.get("level"), parsed.get("title"))

    try:
        from agents.llm_router import get_router
    except Exception as exc:
        logger.error("[g8.negotiation_strategist] llm_router unavailable: %s", exc)
        return _empty_result(error=f"router unavailable: {exc}")

    user_payload = json.dumps(
        {
            "current_offer_total_y1_usd": total_y1,
            "market_p25": p25,
            "market_p50": p50,
            "market_p75": p75,
            "market_p90": p90,
            "title": parsed.get("title"),
            "level": parsed.get("level"),
            "company": parsed.get("company"),
            "location": parsed.get("location"),
            "archetype": archetype,
        },
        default=str,
    )

    t0 = time.monotonic()
    try:
        result = await get_router().ask(
            provider=_PROVIDER,
            model=_MODEL,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_payload}],
            max_tokens=800,
            temperature=0.4,
            cache_system=True,
            graph="G8",
            node="negotiation_strategist",
            user_id=state.get("user_id"),
        )
    except Exception as exc:
        logger.error("[g8.negotiation_strategist] LLM failed: %s", exc)
        return _empty_result(error=f"llm error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    text = (getattr(result, "text", "") or "").strip()

    try:
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        plan = json.loads(text)
    except Exception as exc:
        logger.warning("[g8.negotiation_strategist] JSON parse failed: %s text=%r", exc, text[:200])
        plan = {"anchor": p75, "walkaway": p25, "script": text, "archetype_strategy": archetype}

    anchor = plan.get("anchor")
    walkaway = plan.get("walkaway")
    script = plan.get("script") or ""
    archetype_framing = plan.get("archetype_strategy") or archetype

    # Identity lock guard: anchor must be inside [p25, p90 * 1.10]
    if anchor is not None and p25 is not None and p90 is not None:
        max_ok = p90 * 1.10
        if not (p25 <= anchor <= max_ok):
            logger.warning(
                "[g8.negotiation_strategist] anchor %s outside [%s, %s] — clipping to p75",
                anchor, p25, max_ok,
            )
            anchor = p75

    return {
        "negotiation_anchor": anchor,
        "negotiation_walkaway": walkaway,
        "negotiation_script": script,
        "archetype_strategy": archetype_framing,
        "total_cost_usd": cost,
        "total_latency_ms": latency_ms,
        "transcript": [
            make_g8_turn(
                "negotiation_strategist",
                provider=_PROVIDER,
                model=_MODEL,
                input_summary=user_payload[:200],
                output={"anchor": anchor, "walkaway": walkaway, "script_len": len(script)},
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        ],
    }


def _empty_result(error: str | None = None) -> dict:
    """Return a zeroed-out patch (for failure cases) so the graph continues."""
    return {
        "negotiation_anchor": None,
        "negotiation_walkaway": None,
        "negotiation_script": "",
        "archetype_strategy": "IC",
        "transcript": [
            make_g8_turn(
                "negotiation_strategist",
                provider=_PROVIDER,
                model=_MODEL,
                error=error,
            )
        ],
    }
