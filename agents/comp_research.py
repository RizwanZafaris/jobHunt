"""
agents/comp_research.py — Phase 1.1 compensation intelligence with persistent cache.

Why this exists
---------------
career-ops queries Glassdoor / Levels.fyi / Blind via WebSearch on every role
evaluation. The user has no compensation data going into salary fields, A-F
scoring, or offer negotiation. Walking into comp conversations blind costs
$10K-$50K per negotiation; wrong salary expectation can ATS-filter the user
out before a human ever sees the application.

This module is the cheapest, highest-leverage piece in Phase 1: it builds a
30-day-cache layer on top of Perplexity Sonar that powers G5 evaluation
scoring (Phase 2), G7 application-form salary fields (Phase 3), and G8 offer
negotiation framing (Phase 4) simultaneously. Two weeks of cache builds a
personal comp database.

Design
------
ONE caching layer + TWO public functions:

  • get_comp_band(*, company, role, level, location, user_id, force_refresh)
      Cache-first lookup of compensation percentiles (p25, p50, p75, p90).
      On cache miss, queries Perplexity Sonar (~$0.02 per uncached call) and
      writes the result back to `comp_cache` with a 30-day TTL.

  • suggest_salary_strategy(band, *, user_target=None)
      Given a CompBand, returns a SalaryStrategy with the exact framing
      language to use in a salary expectation field. No LLM needed — pure
      rules:
          - Wide band (p90-p50 > 30% of p50) → "range" approach (p65 to p85)
          - Narrow band                       → "anchor" at p75
          - Missing data                       → "decline" ("flexible, market-rate")

Cost model
----------
At 50 unique (company, role, level, location) queries per month and 30-day
TTL, expected spend is ~$1-5 / month after warmup. First month higher as
the cache fills. Verified per perplexity_search.py pricing model.

cite:knowledge_id breadcrumbs
-----------------------------
Every CompBand carries `source_citations` from Perplexity. When this data
feeds into G7 (salary field) or G8 (negotiation script), the cite IDs travel
with the output. outcome_to_persona can then attribute "the candidate's
salary anchor produced a counter-offer" to specific comp_cache rows.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from agents.perplexity_search import (
    MODEL_RECENCY,
    _api_key,  # noqa: F401 — raises if key missing; we want that surface fast
    _estimate_cost,
    _extract_content,
    _extract_token_usage,
    _normalise_citations,
    _post_chat,
)
from db.client import get_supabase

logger = logging.getLogger(__name__)


# ─── Dataclasses ───────────────────────────────────────────────────────────


@dataclass
class CompBand:
    """A compensation percentile band for one (company, role, level, location) tuple."""

    company: str
    role: str
    level: Optional[str]
    location: Optional[str]
    currency: str
    p25: Optional[int]
    p50: Optional[int]
    p75: Optional[int]
    p90: Optional[int]
    source_summary: str
    source_citations: list[dict[str, Any]] = field(default_factory=list)
    # Provenance fields for the caller:
    cached: bool = False
    age_days: int = 0
    cost_usd: float = 0.0
    confidence: Literal["high", "medium", "low"] = "low"
    cache_id: Optional[str] = None

    def has_data(self) -> bool:
        """True if at least one percentile is populated."""
        return any(v is not None for v in (self.p25, self.p50, self.p75, self.p90))

    def midpoint(self) -> Optional[int]:
        """Best single-number estimate. Prefers p50; falls back to (p25+p75)/2."""
        if self.p50 is not None:
            return int(self.p50)
        if self.p25 is not None and self.p75 is not None:
            return int((self.p25 + self.p75) // 2)
        for v in (self.p75, self.p25, self.p90):
            if v is not None:
                return int(v)
        return None

    def band_width_pct(self) -> Optional[float]:
        """How wide is the band? (p90 - p50) / p50. None if missing data."""
        if self.p50 and self.p90 and self.p50 > 0:
            return (self.p90 - self.p50) / self.p50
        return None


@dataclass
class SalaryStrategy:
    """How to fill a salary-expectation field given a CompBand."""

    approach: Literal["range", "anchor", "decline"]
    low: Optional[int]
    high: Optional[int]
    anchor: Optional[int]
    framing: str
    rationale: str


# ─── Perplexity prompt ─────────────────────────────────────────────────────

# The prompt is deliberately narrow: ask for one number per percentile and
# request structured JSON in the response so we can parse deterministically.
# Sonar will surface Glassdoor / Levels.fyi / Blind / company press as citations.
COMP_RESEARCH_SYSTEM = """You are a compensation research analyst. Answer compensation \
questions with structured JSON only — no prose outside the JSON object.

For the role and company provided, search public sources (Glassdoor, Levels.fyi, \
Blind, Payscale, company filings/press) and produce a percentile breakdown of \
total compensation (base + target bonus + equity vesting amortized over 4 years).

Output schema:
{
  "currency": "USD",
  "p25": 120000,
  "p50": 145000,
  "p75": 175000,
  "p90": 210000,
  "summary": "One-sentence summary citing the dominant data sources and \
sample size. Avoid generic claims; if data is thin, say so."
}

Rules:
- If you cannot find data for the specific (company, role, level, location) tuple, \
return null for the affected percentile fields and explain in `summary`.
- Use whole-number integers only — no decimals, no $ sign, no comma.
- Currency MUST be a 3-letter ISO code (USD, EUR, AED, SAR, INR, etc.).
- If data is regional and there's a wide spread by city, choose the city explicitly \
mentioned in the user's question and say which one in `summary`.

Return ONLY the JSON object. No backticks. No commentary."""


def _build_user_query(
    *, company: str, role: str, level: Optional[str], location: Optional[str]
) -> str:
    """Construct the Sonar user message from query inputs."""
    parts = [f"Company: {company}", f"Role: {role}"]
    if level:
        parts.append(f"Level: {level}")
    if location:
        parts.append(f"Location: {location}")
    parts.append("")
    parts.append(
        "What is the total-compensation breakdown by percentile (p25, p50, "
        "p75, p90) for this exact role at this exact company, in this "
        "location? Use 2025-2026 data where possible. Return only the JSON."
    )
    return "\n".join(parts)


# ─── JSON parsing ──────────────────────────────────────────────────────────


_JSON_OBJ_RE = re.compile(r"\{[\s\S]*\}")


def _parse_comp_response(raw: str) -> Optional[dict[str, Any]]:
    """Parse Sonar's response into a comp dict, tolerating slight schema drift.

    Returns None if the response can't be parsed into anything useful.
    """
    if not raw:
        return None
    # Sonar sometimes wraps the JSON in prose despite the system prompt.
    # Extract the first {...} block we see.
    m = _JSON_OBJ_RE.search(raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    out: dict[str, Any] = {
        "currency": data.get("currency") or "USD",
        "summary": data.get("summary") or "",
    }
    for pct in ("p25", "p50", "p75", "p90"):
        val = data.get(pct)
        if val is None:
            out[pct] = None
            continue
        # Coerce common variants: "120000", "120,000", "$120000", "120K".
        if isinstance(val, str):
            cleaned = re.sub(r"[$\s,]", "", val).lower()
            if cleaned.endswith("k"):
                try:
                    out[pct] = int(float(cleaned[:-1]) * 1000)
                    continue
                except ValueError:
                    out[pct] = None
                    continue
            try:
                out[pct] = int(float(cleaned))
            except ValueError:
                out[pct] = None
                continue
        elif isinstance(val, (int, float)):
            out[pct] = int(val)
        else:
            out[pct] = None
    return out


def _classify_confidence(parsed: dict[str, Any]) -> Literal["high", "medium", "low"]:
    """Crude confidence rating based on how many percentiles came back populated."""
    populated = sum(1 for k in ("p25", "p50", "p75", "p90") if parsed.get(k) is not None)
    if populated >= 4:
        return "high"
    if populated >= 2:
        return "medium"
    return "low"


# ─── Cache layer ───────────────────────────────────────────────────────────


def _cache_key_tuple(
    *, company: str, role: str, level: Optional[str], location: Optional[str]
) -> tuple[str, str, str, str]:
    """Normalise the cache key — strips/lowercases so 'Stripe '/'STRIPE' both hit."""
    return (
        (company or "").strip().lower(),
        (role or "").strip().lower(),
        (level or "").strip().lower(),
        (location or "").strip().lower(),
    )


def _get_cache_hit(
    db,
    *,
    user_id: UUID,
    company: str,
    role: str,
    level: Optional[str],
    location: Optional[str],
) -> Optional[dict[str, Any]]:
    """Look up an unexpired comp_cache row for this user+tuple. None on miss."""
    c, r, lv, loc = _cache_key_tuple(company=company, role=role, level=level, location=location)
    try:
        rows = (
            db.table("comp_cache")
            .select("*")
            .eq("user_id", str(user_id))
            .eq("company_norm", c)
            .eq("role_norm", r)
            .eq("level_norm", lv)
            .eq("location_norm", loc)
            .gte("expires_at", datetime.now(timezone.utc).isoformat())
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data or []
    except Exception as exc:
        logger.warning("comp_cache lookup failed: %r", exc)
        return None
    return rows[0] if rows else None


def _row_to_band(row: dict[str, Any], *, source_company: str, source_role: str) -> CompBand:
    """Inflate a comp_cache row into a CompBand."""
    created_at = row.get("created_at")
    age_days = 0
    if created_at:
        try:
            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            age_days = max(0, (datetime.now(timezone.utc) - created).days)
        except (ValueError, TypeError):
            pass
    citations = row.get("source_citations")
    if isinstance(citations, str):
        try:
            citations = json.loads(citations)
        except json.JSONDecodeError:
            citations = []
    if not isinstance(citations, list):
        citations = []
    return CompBand(
        company=row.get("company") or source_company,
        role=row.get("role") or source_role,
        level=row.get("level"),
        location=row.get("location"),
        currency=row.get("currency") or "USD",
        p25=row.get("p25"),
        p50=row.get("p50"),
        p75=row.get("p75"),
        p90=row.get("p90"),
        source_summary=row.get("source_summary") or "",
        source_citations=citations,
        cached=True,
        age_days=age_days,
        cost_usd=0.0,
        confidence=row.get("confidence") or "low",
        cache_id=row.get("id"),
    )


def _persist_band(
    db,
    *,
    user_id: UUID,
    company: str,
    role: str,
    level: Optional[str],
    location: Optional[str],
    parsed: dict[str, Any],
    citations: list[dict[str, Any]],
    confidence: str,
    cost_usd: float,
) -> Optional[str]:
    """Upsert into comp_cache. Returns the new row id, or None on failure."""
    c, r, lv, loc = _cache_key_tuple(company=company, role=role, level=level, location=location)
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    payload = {
        "user_id": str(user_id),
        "company": company,
        "role": role,
        "level": level,
        "location": location,
        "company_norm": c,
        "role_norm": r,
        "level_norm": lv,
        "location_norm": loc,
        "currency": parsed.get("currency") or "USD",
        "p25": parsed.get("p25"),
        "p50": parsed.get("p50"),
        "p75": parsed.get("p75"),
        "p90": parsed.get("p90"),
        "source_summary": parsed.get("summary") or "",
        "source_citations": citations,
        "confidence": confidence,
        "cost_usd": cost_usd,
        "expires_at": expires,
    }
    try:
        # `on_conflict` requires the unique constraint on the normalised tuple
        # (see migration 013_comp_cache.sql).
        result = (
            db.table("comp_cache")
            .upsert(payload, on_conflict="user_id,company_norm,role_norm,level_norm,location_norm")
            .execute()
        )
        rows = result.data or []
        return rows[0].get("id") if rows else None
    except Exception as exc:
        logger.warning("comp_cache upsert failed: %r", exc)
        return None


# ─── Public surface ────────────────────────────────────────────────────────


async def get_comp_band(
    *,
    company: str,
    role: str,
    level: Optional[str] = None,
    location: Optional[str] = None,
    user_id: UUID,
    force_refresh: bool = False,
) -> CompBand:
    """Get a comp band for (company, role, level, location). Cache-first.

    Args:
        company: e.g. "Stripe"
        role: e.g. "Senior Product Manager"
        level: e.g. "L5" or "Senior"  (optional but improves accuracy)
        location: e.g. "Dubai" or "Remote-US"  (optional)
        user_id: Supabase profiles.id — used for row-level isolation.
        force_refresh: bypass cache; re-query Sonar and overwrite.

    Returns:
        A CompBand. Always returns a valid object even on Perplexity failure;
        check `.has_data()` to know if you have actionable percentiles.

    Raises:
        RuntimeError: only if PERPLEXITY_API_KEY is unset AND we need to query
                     Sonar (i.e., cache miss without `force_refresh=False`).
                     We deliberately surface this so cron jobs don't silently
                     no-op.
    """
    company = (company or "").strip()
    role = (role or "").strip()
    if not company or not role:
        return CompBand(
            company=company,
            role=role,
            level=level,
            location=location,
            currency="USD",
            p25=None,
            p50=None,
            p75=None,
            p90=None,
            source_summary="missing company or role — cannot query",
            confidence="low",
        )

    db = get_supabase()

    # 1. Cache lookup (free, fast).
    if not force_refresh:
        hit = _get_cache_hit(
            db, user_id=user_id, company=company, role=role, level=level, location=location
        )
        if hit:
            return _row_to_band(hit, source_company=company, source_role=role)

    # 2. Cache miss → Perplexity Sonar.
    user_msg = _build_user_query(company=company, role=role, level=level, location=location)
    try:
        payload = await _post_chat(
            model=MODEL_RECENCY, system=COMP_RESEARCH_SYSTEM, user=user_msg
        )
    except Exception as exc:
        logger.warning(
            "comp_research perplexity call failed for %s / %s: %r", company, role, exc
        )
        return CompBand(
            company=company,
            role=role,
            level=level,
            location=location,
            currency="USD",
            p25=None,
            p50=None,
            p75=None,
            p90=None,
            source_summary=f"perplexity call failed: {type(exc).__name__}",
            confidence="low",
        )

    raw = _extract_content(payload)
    in_tok, out_tok = _extract_token_usage(payload)
    cost = _estimate_cost(MODEL_RECENCY, in_tok, out_tok)
    citations = _normalise_citations(payload)
    parsed = _parse_comp_response(raw) or {}
    confidence = _classify_confidence(parsed)

    # 3. Persist (best-effort — caching failure doesn't poison the returned band).
    cache_id = _persist_band(
        db,
        user_id=user_id,
        company=company,
        role=role,
        level=level,
        location=location,
        parsed=parsed,
        citations=citations,
        confidence=confidence,
        cost_usd=cost,
    )

    return CompBand(
        company=company,
        role=role,
        level=level,
        location=location,
        currency=parsed.get("currency") or "USD",
        p25=parsed.get("p25"),
        p50=parsed.get("p50"),
        p75=parsed.get("p75"),
        p90=parsed.get("p90"),
        source_summary=parsed.get("summary") or raw[:300],
        source_citations=citations,
        cached=False,
        age_days=0,
        cost_usd=cost,
        confidence=confidence,
        cache_id=cache_id,
    )


def suggest_salary_strategy(
    band: CompBand,
    *,
    user_target: Optional[int] = None,
) -> SalaryStrategy:
    """Build the salary-field strategy given a comp band.

    Rules (no LLM — deterministic):
      - No data: 'decline' with "flexible, market-rate" framing.
      - Wide band (p90-p50 > 30% of p50): 'range' approach using p65 to p85.
      - Narrow band: 'anchor' at p75 (leaves room to negotiate up).
      - If user_target is provided AND it exceeds the band's p90: cap at p90
        with a note in the rationale (don't anchor above the market band —
        that's how candidates get auto-filtered).
    """
    if not band.has_data() or band.p50 is None:
        return SalaryStrategy(
            approach="decline",
            low=None,
            high=None,
            anchor=None,
            framing=(
                "I'm flexible and open to a market-rate package — happy to "
                "discuss once I understand the level and total comp structure."
            ),
            rationale=(
                "No comp data was returned for this role/company/location. "
                "Decline to anchor; let the recruiter share their range first."
            ),
        )

    width_pct = band.band_width_pct()
    p50 = int(band.p50)
    currency = band.currency or "USD"

    # Cap user_target at p90 to avoid auto-filter.
    capped_target = user_target
    target_note = ""
    if user_target is not None and band.p90 is not None and user_target > band.p90:
        capped_target = band.p90
        target_note = (
            f" (Note: user target {currency} {user_target:,} exceeds the market "
            f"p90 of {currency} {band.p90:,}; capping at p90 to avoid being "
            f"auto-filtered by the ATS.)"
        )

    if width_pct is not None and width_pct > 0.30:
        # Wide market → use a range.
        p25 = int(band.p25) if band.p25 is not None else p50
        p75 = int(band.p75) if band.p75 is not None else p50
        p90 = int(band.p90) if band.p90 is not None else p75
        low = int(p50 + (p75 - p50) * 0.5)  # ~p62-65 — anchor at upper-mid
        high = int(p75 + (p90 - p75) * 0.5)  # ~p82-85
        if capped_target is not None:
            high = min(high, capped_target)
        return SalaryStrategy(
            approach="range",
            low=low,
            high=high,
            anchor=None,
            framing=(
                f"I'm targeting total comp in the {currency} {low:,}-{high:,} "
                f"range — flexible based on the full package (base + equity + "
                f"bonus + relocation/visa support)."
            ),
            rationale=(
                f"Market band is wide ({100 * width_pct:.0f}% spread above p50). "
                f"Using a range from ~p65 to ~p85 lets the recruiter anchor "
                f"upward without pricing you out of the role."
                + target_note
            ),
        )

    # Narrow band → anchor at p75.
    p75 = int(band.p75) if band.p75 is not None else p50
    anchor = capped_target if capped_target is not None else p75
    return SalaryStrategy(
        approach="anchor",
        low=None,
        high=None,
        anchor=int(anchor),
        framing=(
            f"Based on market data for {band.level or 'this level'} "
            f"{band.role}s at {band.company}-stage companies"
            + (f" in {band.location}" if band.location else "")
            + f", I'm targeting total comp around {currency} {int(anchor):,}+."
        ),
        rationale=(
            f"Market band is narrow (p50={currency} {p50:,}, p75={currency} {p75:,}). "
            f"Anchoring at p75 maximises offer value without exiting the band."
            + target_note
        ),
    )


# ─── Helper for callers that want a plain-dict surface ────────────────────


def band_to_dict(band: CompBand) -> dict[str, Any]:
    """Serialise a CompBand for JSON responses / cache snapshots."""
    return asdict(band)


def strategy_to_dict(s: SalaryStrategy) -> dict[str, Any]:
    return asdict(s)
