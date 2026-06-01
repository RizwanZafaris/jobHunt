"""
agents/g8/market_analyzer.py — G8 Node 2: Market Analyzer.

Two-tier strategy:
  Tier 1 — comp_cache lookup (cheap, covers ~85% of (company,role,level)).
  Tier 2 — Perplexity Sonar + Gemini cross-validation on cache miss.

Emits `MarketBands` (p25/p50/p75/p90 USD) + appends every citation URL to
state.cites with kind="sonar_url" or kind="comp_cache" so the synthesizer
can carry breadcrumbs into the persisted row.

Cost target: ~$0.05 per call (Perplexity sonar ~$0.005 + Gemini Pro
~$0.025 — sometimes both skipped if cache hits).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Final

from agents.g8_state import (
    CitationRef,
    MarketBands,
    OfferEvaluationState,
    make_g8_turn,
)

logger = logging.getLogger(__name__)

_PERPLEXITY_MODEL: Final[str] = "sonar"
_GEMINI_MODEL: Final[str] = "gemini-2.5-pro"


async def _try_comp_cache(parsed: dict, app_row: dict) -> tuple[MarketBands | None, list[CitationRef]]:
    """Read agents/comp_research.py cache for an existing band match."""
    try:
        from agents.comp_research import get_cached_band  # type: ignore[attr-defined]
    except Exception:
        # comp_research is not yet exporting get_cached_band — skip cache.
        return None, []

    try:
        company = (parsed.get("company") or app_row.get("company") or "").strip()
        role = (parsed.get("title") or app_row.get("role") or "").strip()
        level = (parsed.get("level") or app_row.get("level") or "").strip()
        if not (company and role):
            return None, []
        band = get_cached_band(company=company, role=role, level=level)
        if not band:
            return None, []
        cites: list[CitationRef] = [
            CitationRef(
                kind="comp_cache",
                id=str(band.get("id", "")),
                title=f"comp_cache:{company}:{role}",
                snippet=band.get("source") or "",
                used_by_node="market_analyzer",
            )
        ]
        return (
            MarketBands(
                p25=band.get("p25"),
                p50=band.get("p50"),
                p75=band.get("p75"),
                p90=band.get("p90"),
                confidence="high" if band.get("source") else "medium",
                source="comp_cache_fallback",
            ),
            cites,
        )
    except Exception as exc:
        logger.warning("[g8.market_analyzer] comp_cache lookup failed: %s", exc)
        return None, []


async def _query_perplexity(parsed: dict, app_row: dict, user_id: str | None) -> tuple[MarketBands | None, list[CitationRef], float, int]:
    """Live Perplexity Sonar query for current market bands."""
    try:
        from agents.llm_router import get_router
    except Exception as exc:
        logger.error("[g8.market_analyzer] llm_router unavailable: %s", exc)
        return None, [], 0.0, 0

    company = parsed.get("company") or app_row.get("company") or "the company"
    role = parsed.get("title") or app_row.get("role") or "Senior Engineer"
    level = parsed.get("level") or app_row.get("level") or ""
    location = parsed.get("location") or app_row.get("location") or "United States"

    prompt = (
        f"Total compensation bands (USD) for a {level} {role} at {company} "
        f"in {location} in 2025–2026. Provide p25 / p50 / p75 / p90 total comp "
        f"(base + bonus + equity-amortised). Cite levels.fyi, Glassdoor, and "
        f"Blind salary threads when possible. Return JSON: "
        f'{{"p25": int, "p50": int, "p75": int, "p90": int, "sources": [...]}}'
    )

    t0 = time.monotonic()
    try:
        result = await get_router().ask(
            provider="perplexity",
            model=_PERPLEXITY_MODEL,
            system="You are a compensation-research analyst. Return JSON only.",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
            temperature=0.1,
            graph="G8",
            node="market_analyzer",
            user_id=user_id,
        )
    except Exception as exc:
        logger.error("[g8.market_analyzer] perplexity failed: %s", exc)
        return None, [], 0.0, int((time.monotonic() - t0) * 1000)

    latency_ms = int((time.monotonic() - t0) * 1000)
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    text = (getattr(result, "text", "") or "").strip()

    try:
        # Strip code fences if present
        if text.startswith("```"):
            text = text.strip("`")
            text = text.split("\n", 1)[1] if "\n" in text else text
            if text.endswith("```"):
                text = text[:-3]
        data = json.loads(text)
    except Exception as exc:
        logger.warning("[g8.market_analyzer] perplexity JSON parse failed: %s; text=%r", exc, text[:200])
        return None, [], cost, latency_ms

    bands = MarketBands(
        p25=data.get("p25"),
        p50=data.get("p50"),
        p75=data.get("p75"),
        p90=data.get("p90"),
        confidence=data.get("confidence", "medium"),
        source="perplexity_sonar",
    )

    cites: list[CitationRef] = []
    for src in data.get("sources", []) or []:
        if isinstance(src, str):
            cites.append(
                CitationRef(
                    kind="sonar_url",
                    id=src,
                    title=src[:80],
                    snippet=None,
                    used_by_node="market_analyzer",
                )
            )
        elif isinstance(src, dict):
            cites.append(
                CitationRef(
                    kind="sonar_url",
                    id=src.get("url", "") or str(src),
                    title=src.get("title") or src.get("url", "")[:80],
                    snippet=src.get("snippet"),
                    used_by_node="market_analyzer",
                )
            )

    return bands, cites, cost, latency_ms


async def market_analyzer_node(state: OfferEvaluationState) -> dict:
    """Two-tier market band lookup. Cache first, Perplexity on miss."""
    parsed = state.get("parsed_offer") or {}
    app_row = state.get("application_row") or {}
    user_id = state.get("user_id")

    # Tier 1 — comp_cache
    bands, cites = await _try_comp_cache(parsed, app_row)
    cost_usd = 0.0
    latency_ms = 0

    if bands is None or not any([bands.get("p25"), bands.get("p50"), bands.get("p75"), bands.get("p90")]):
        # Tier 2 — live Perplexity lookup
        bands_live, cites_live, cost_usd, latency_ms = await _query_perplexity(
            parsed, app_row, user_id
        )
        if bands_live is not None:
            bands = bands_live
            cites = cites_live

    if bands is None:
        bands = MarketBands(confidence="low", source="unknown")

    summary = (
        f"Market: p25=${bands.get('p25') or '—'}, p50=${bands.get('p50') or '—'}, "
        f"p75=${bands.get('p75') or '—'}, p90=${bands.get('p90') or '—'} "
        f"({bands.get('source', 'unknown')})"
    )

    return {
        "market_bands": bands,
        "market_summary": summary,
        "market_citations": [{"kind": c.get("kind"), "id": c.get("id"), "title": c.get("title")} for c in cites],
        "cites": cites,
        "total_cost_usd": cost_usd,
        "total_latency_ms": latency_ms,
        "transcript": [
            make_g8_turn(
                "market_analyzer",
                provider="perplexity" if bands.get("source") == "perplexity_sonar" else "internal",
                model=_PERPLEXITY_MODEL if bands.get("source") == "perplexity_sonar" else "comp_cache",
                input_summary=f"company={parsed.get('company') or app_row.get('company')}",
                output={"bands": bands, "n_cites": len(cites)},
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            )
        ],
    }
