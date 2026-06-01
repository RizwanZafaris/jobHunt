"""
agents/g8/risk_detector.py — G8 Node 4: Risk Detector.

Reads `state.company_knowledge` (last 90d of RAG-stored news / Glassdoor
signals) and the parsed offer. Calls DeepSeek-Reasoner R1 (cheap, strong
chain-of-thought) to enumerate risk factors:

  - financial_health  (layoffs, revenue decline, funding crunch)
  - role_risk         (newly created roles, poorly defined remit)
  - vesting_risk      (cliff, refresher pattern, illiquid equity)
  - geo_risk          (relocation strain, visa risk, RTO mandates)
  - other             (anything else flagged by the RAG context)

Each factor carries severity ∈ [0,1] + evidence snippet + cite back-refs.
The aggregate `risk_score` is the weighted max of severities, capped at 1.

Cost target: ~$0.10.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Final

from agents.g8_state import OfferEvaluationState, RiskFactor, make_g8_turn

logger = logging.getLogger(__name__)

_MODEL: Final[str] = "deepseek-reasoner"
_PROVIDER: Final[str] = "deepseek"

_SYSTEM_PROMPT: Final[str] = """You are a risk analyst for senior tech offers.
Given a parsed offer + recent (last 90d) company news / Glassdoor signals,
enumerate risk factors. For each:
  - kind:    one of [financial_health, role_risk, vesting_risk, geo_risk, other]
  - severity: float in [0, 1]  (0=negligible, 1=disqualifying)
  - description: 1-2 sentences
  - evidence: a verbatim snippet from the provided context (if any)
  - cite_ids: list of company_knowledge.id strings you used

Return strict JSON: {"factors": [...], "risk_score": float in [0,1]}.
risk_score should be the highest-severity factor's severity unless
multiple medium factors compound (then bump by +0.1 per extra factor,
capped at 1.0). No prose outside the JSON.
"""


async def risk_detector_node(state: OfferEvaluationState) -> dict:
    """Assess risk factors using the RAG context + offer terms."""
    parsed = state.get("parsed_offer") or {}
    knowledge = state.get("company_knowledge") or []

    # Trim knowledge to the most recent 20 items to control prompt size.
    trimmed = knowledge[:20]
    knowledge_payload = [
        {
            "id": str(item.get("id", "")),
            "kind": item.get("kind"),
            "title": item.get("title"),
            "snippet": (item.get("content") or item.get("snippet") or "")[:300],
            "published_at": item.get("published_at") or item.get("captured_at"),
        }
        for item in trimmed
    ]

    user_payload = json.dumps(
        {
            "offer": {
                "company": parsed.get("company"),
                "title": parsed.get("title"),
                "level": parsed.get("level"),
                "location": parsed.get("location"),
                "total_y1": parsed.get("total_comp_year_1"),
                "equity_amortized": parsed.get("equity_grant_usd"),
            },
            "company_knowledge_last_90d": knowledge_payload,
        },
        default=str,
    )[:8000]

    try:
        from agents.llm_router import get_router
    except Exception as exc:
        logger.error("[g8.risk_detector] llm_router unavailable: %s", exc)
        return _empty_result(error=f"router unavailable: {exc}")

    t0 = time.monotonic()
    try:
        result = await get_router().ask(
            provider=_PROVIDER,
            model=_MODEL,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_payload}],
            max_tokens=2000,
            temperature=0.2,
            graph="G8",
            node="risk_detector",
            user_id=state.get("user_id"),
        )
    except Exception as exc:
        logger.error("[g8.risk_detector] LLM failed: %s", exc)
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
        parsed_json = json.loads(text)
    except Exception as exc:
        logger.warning("[g8.risk_detector] JSON parse failed: %s text=%r", exc, text[:200])
        return _empty_result(error=f"parse error: {exc}", cost=cost, latency_ms=latency_ms)

    raw_factors = parsed_json.get("factors", []) or []
    risk_score = float(parsed_json.get("risk_score", 0.0) or 0.0)
    risk_score = min(max(risk_score, 0.0), 1.0)  # clip [0, 1]

    factors: list[RiskFactor] = []
    for f in raw_factors:
        if not isinstance(f, dict):
            continue
        factors.append(
            RiskFactor(
                kind=f.get("kind", "other"),
                severity=float(f.get("severity", 0.0) or 0.0),
                description=f.get("description", ""),
                evidence=f.get("evidence"),
                cite_ids=list(f.get("cite_ids", []) or []),
            )
        )

    return {
        "risk_factors": factors,
        "risk_score": risk_score,
        "total_cost_usd": cost,
        "total_latency_ms": latency_ms,
        "transcript": [
            make_g8_turn(
                "risk_detector",
                provider=_PROVIDER,
                model=_MODEL,
                input_summary=f"knowledge_items={len(trimmed)}",
                output={"n_factors": len(factors), "risk_score": risk_score},
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        ],
    }


def _empty_result(error: str | None = None, *, cost: float = 0.0, latency_ms: int = 0) -> dict:
    return {
        "risk_factors": [],
        "risk_score": 0.0,
        "total_cost_usd": cost,
        "total_latency_ms": latency_ms,
        "transcript": [
            make_g8_turn(
                "risk_detector",
                provider=_PROVIDER,
                model=_MODEL,
                error=error,
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        ],
    }
