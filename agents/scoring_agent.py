"""
agents/scoring_agent.py — G5 evaluation scoring (Phase 2, gap-closure §4.1).

Why this exists
---------------
career-ops scores roles A-F across 6 dimensions and uses that to gate
apply / skip decisions. jobHunt has had a single `jobs.match_score`
integer that mixes everything together. The user can't see WHY a role is
a fit (or isn't); the application assistant (G7, Phase 3) has nothing
structured to prioritise on.

This module adds dimensional breakdown:

    role_fit | growth | comp | culture | remote | trajectory

…each scored 0-100 with a one-sentence rationale, combined into a
weighted composite, mapped to A/B/C/D/F. Results are persisted to
`jobs.fit_score_breakdown` (JSONB) and `jobs.letter_grade` (TEXT) by
migration 019.

Per roadmap §4.1: **this is a SCORING AGENT extending the existing
fit_score logic, NOT a new graph.** No nodes, no checkpointer, no state
machine. A single async entry point that fans out to six dimension
scorers, combines, persona-critics, persists.

Cost model (~$0.15/role at default volume)
------------------------------------------
- role_fit:    Sonnet 4.6 · ~$0.03
- growth:      Sonnet 4.6 · ~$0.02
- comp:        code only · $0 (or ~$0.02 on a comp_cache miss)
- culture:     Sonnet 4.6 + RAG · ~$0.05
- remote:      code only · $0
- trajectory:  Sonnet 4.6 + RAG · ~$0.05

Engineering principles wired in (per §11 of the roadmap)
--------------------------------------------------------
1. Persona-as-critic: after the LLM dimensions return, `_persona_critic`
   verifies role_fit / culture rationales reference at least ONE
   success_pattern from the persona. Failures log a warning and
   downgrade culture by 10 points (don't block — surface).
2. cite:knowledge_id breadcrumbs: every culture / trajectory call
   captures the company_knowledge row ids referenced in the prompt and
   carries them through to `fit_score_breakdown.cites` so
   outcome_to_persona can attribute callback outcomes back to specific
   knowledge sources.
3. Identity lock: the LLM prompts include the JD text and the
   company_knowledge passages by reference. The persona_critic's
   downgrade rule is the structural enforcement — a rationale that
   doesn't reference any persona success_pattern triggers a -10 to
   culture before persistence.
4. user_id NOT NULL: writes go through `db.client.upsert_job` which
   already enforces this.
5. Cost telemetry: every Sonnet call passes `agent_name="g5.<dim>"`;
   the llm_router default callback derives graph='G5' from that prefix.
6. Prompt caching: llm_router auto-wraps system prompts ≥1024 tokens with
   cache_control={"type":"ephemeral"} when ANTHROPIC_PROMPT_CACHE_ENABLED
   is on (default). LLMResult.cache_*_input_tokens telemetry is already
   wired for when our prompts grow over the threshold.
7. HITL: no auto-actions — scoring is informational; the user clicks
   "Build resume" / "Apply" themselves.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from agents.llm_router import _parse_json_loose, get_router
from api._job_guards import load_open_job
from db.client import get_supabase

logger = logging.getLogger(__name__)


SCORER_VERSION = "g5-v1"

# Dimension weights (sum = 100). Per roadmap §4.1.
WEIGHTS: dict[str, int] = {
    "role_fit":   25,
    "growth":     20,
    "comp":       20,
    "culture":    15,
    "remote":     10,
    "trajectory": 10,
}

# Letter grade thresholds. composite >= X gives that grade.
LETTER_GRADE_THRESHOLDS: list[tuple[int, str]] = [
    (85, "A"),
    (70, "B"),
    (55, "C"),
    (40, "D"),
    (0,  "F"),
]

# Single-user mode default — matches the pattern in every other writer.
def _default_user_id() -> str:
    return os.environ.get(
        "RIZWAN_USER_ID",
        "00000000-0000-0000-0000-000000000001",
    )


# Re-score window. If `fit_score_breakdown.scored_at` is newer than this,
# skip the re-score. Auto-trigger from JobScout consults this to keep cost
# bounded.
RESCORE_STALENESS_DAYS = 7


# ── Provider tier and model selection ──────────────────────────────────
# Per the roadmap and existing g6_persona_critic pattern, dimensional
# Sonnet calls hit Anthropic Sonnet 4.6. We keep this in one place so a
# future swap to Sonnet 4.7 is one constant change.
_LLM_PROVIDER = "anthropic"
_LLM_MODEL = "claude-sonnet-4-6"


# ═════════════════════════════════════════════════════════════════════════
# Dataclasses
# ═════════════════════════════════════════════════════════════════════════
@dataclass
class DimensionScore:
    """One dimension's result."""
    score: int          # 0-100, clamped
    rationale: str      # one sentence, ≤200 chars
    cost_usd: float = 0.0
    cites: list[dict[str, Any]] = None  # knowledge_id breadcrumbs

    def __post_init__(self) -> None:
        if self.cites is None:
            self.cites = []
        # Defensive clamps — LLMs occasionally return out-of-range scores.
        self.score = max(0, min(100, int(self.score)))


# ═════════════════════════════════════════════════════════════════════════
# Letter grade math
# ═════════════════════════════════════════════════════════════════════════
def _composite_score(dims: dict[str, DimensionScore]) -> int:
    """Weighted average of the six dimensions. Returns int 0-100."""
    total = 0.0
    weight_sum = 0
    for dim, weight in WEIGHTS.items():
        d = dims.get(dim)
        if d is None:
            continue
        total += d.score * weight
        weight_sum += weight
    if weight_sum == 0:
        return 0
    return int(round(total / weight_sum))


def _letter_grade(composite: int) -> str:
    """Map composite score → A/B/C/D/F."""
    for threshold, grade in LETTER_GRADE_THRESHOLDS:
        if composite >= threshold:
            return grade
    return "F"


# ═════════════════════════════════════════════════════════════════════════
# Idempotency helpers
# ═════════════════════════════════════════════════════════════════════════
def _is_recently_scored(job: dict[str, Any]) -> bool:
    """True if the job has a fit_score_breakdown.scored_at within the last
    RESCORE_STALENESS_DAYS days. Auto-trigger callers use this to skip
    re-work; manual /score endpoint bypasses via force=True."""
    breakdown = job.get("fit_score_breakdown")
    if not isinstance(breakdown, dict):
        return False
    scored_at = breakdown.get("scored_at")
    if not scored_at:
        return False
    try:
        ts = datetime.fromisoformat(str(scored_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return (datetime.now(timezone.utc) - ts) < timedelta(days=RESCORE_STALENESS_DAYS)


# ═════════════════════════════════════════════════════════════════════════
# Persona loader
# ═════════════════════════════════════════════════════════════════════════
def _load_company_persona(user_id: str, company_name: str) -> Optional[dict]:
    """Most-recent persona row for (user_id, company_name). Used by
    persona_critic + culture scoring. Returns None when no persona has
    been synthesised yet (very-new companies)."""
    if not company_name:
        return None
    try:
        rows = (
            get_supabase()
            .table("company_personas")
            .select("*")
            .eq("user_id", user_id)
            .eq("company_name", company_name)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception as exc:
        logger.warning(
            "scoring_agent: company_personas lookup failed for %s: %r",
            company_name, exc,
        )
        return None
    return rows[0] if rows else None


def _load_profile_competencies(user_id: str) -> list[str]:
    """Flat list of profile_master.core_competencies for role_fit anchor."""
    try:
        rows = (
            get_supabase()
            .table("profile_master")
            .select("core_competencies")
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception as exc:
        logger.warning("scoring_agent: profile_master lookup failed: %r", exc)
        return []
    if not rows:
        return []
    raw = rows[0].get("core_competencies") or []
    return [str(c).strip() for c in raw if c]


# ═════════════════════════════════════════════════════════════════════════
# Cite extractor — pulls knowledge_id breadcrumbs out of pgvector hits
# ═════════════════════════════════════════════════════════════════════════
def _cites_from_chunks(chunks: list[dict]) -> list[dict[str, Any]]:
    """Normalise pgvector RAG chunks into the cite breadcrumb shape."""
    out: list[dict[str, Any]] = []
    for c in chunks or []:
        kid = c.get("id")
        if not kid:
            continue
        out.append({
            "knowledge_id": str(kid),
            "section": c.get("section"),
            "similarity": c.get("similarity"),
        })
    return out


# ═════════════════════════════════════════════════════════════════════════
# Dimension scorer: role_fit (Sonnet 4.6)
# ═════════════════════════════════════════════════════════════════════════
_ROLE_FIT_SYSTEM = """You are a senior career strategist scoring how well a \
candidate's core competencies match a job description.

Given:
- The candidate's stated core competencies (their own profile, ground truth)
- The job description (JD)
- The company's success_patterns (the kind of bullet patterns historically \
converting at this employer, from prior persona research)

Score role-fit 0-100:
- 90-100: every JD requirement maps to a core competency AND at least 3 \
success_patterns are clearly demonstrable from the candidate's competencies
- 70-89: most JD requirements map; 1-2 success_patterns demonstrable
- 50-69: partial coverage; reskilling required for ≥30% of requirements
- 30-49: significant gaps; <30% of JD covered by core competencies
- 0-29: very poor match — wrong archetype or experience level

In your rationale, ALWAYS reference at least one specific success_pattern \
from the persona (verbatim or paraphrased) when available. This is how the \
critic checks for fabricated culture-fit signals downstream.

Output strict JSON only — no prose outside the object:
{"score": 0-100, "rationale": "one sentence, ≤200 chars, references a \
specific success_pattern when persona is non-empty"}"""


async def _score_role_fit(
    *,
    job: dict[str, Any],
    profile_competencies: list[str],
    persona: Optional[dict],
) -> DimensionScore:
    """role_fit dimension via Sonnet 4.6. ~$0.03 per call."""
    competencies_str = ", ".join(profile_competencies[:40]) if profile_competencies else "(profile_master.core_competencies is empty)"

    success_patterns: list[str] = []
    if persona:
        sp_raw = persona.get("success_patterns") or []
        if isinstance(sp_raw, list):
            success_patterns = [str(p).strip() for p in sp_raw if p][:8]

    persona_block = (
        ("SUCCESS_PATTERNS (this company's converting bullet templates):\n"
         + "\n".join(f"  - {p}" for p in success_patterns))
        if success_patterns
        else "SUCCESS_PATTERNS: (no persona synthesised yet for this company)"
    )

    desc = (job.get("description") or "")[:6000]
    user = f"""CANDIDATE CORE COMPETENCIES:
{competencies_str}

{persona_block}

JOB DESCRIPTION:
TITLE: {job.get("title", "")}
COMPANY: {job.get("company", "")}
LOCATION: {job.get("location", "")}

{desc}

Score the role-fit dimension. Return strict JSON only."""

    # Prompt caching: llm_router auto-wraps system prompts ≥1024 tokens with
    # cache_control={"type":"ephemeral"} when ANTHROPIC_PROMPT_CACHE_ENABLED
    # is on (default). Our G5 system prompts are smaller than the threshold,
    # so caching is a no-op unless the prompts grow — but the cost telemetry
    # already records `cache_*` token columns on the LLMResult, so when the
    # threshold trips later there's nothing else to wire.
    result = await get_router().ask(
        provider=_LLM_PROVIDER,
        model=_LLM_MODEL,
        system=_ROLE_FIT_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=400,
        temperature=0.15,
        agent_name="g5.role_fit",
    )
    parsed = _safe_parse_json(result.text)
    return DimensionScore(
        score=int(parsed.get("score", 0) or 0),
        rationale=str(parsed.get("rationale", ""))[:300],
        cost_usd=float(result.cost_usd or 0.0),
    )


# ═════════════════════════════════════════════════════════════════════════
# Dimension scorer: growth (Sonnet 4.6)
# ═════════════════════════════════════════════════════════════════════════
_GROWTH_SYSTEM = """You are a career growth analyst. Given a job description, \
score 0-100 how much career growth opportunity it describes.

Signals to weight:
- Explicit leadership/ownership language: "lead", "own", "drive", "scale", \
"build the team", "0-to-1", "P0"
- Senior-stakeholder visibility: "report to CXO", "present to the board", \
"partner with VP", "executive sponsorship"
- Company stage cues: pre-IPO, hypergrowth, Series B-D, "scaling from \
$X to $Y revenue", "expansion into new markets"
- Scope-expansion signals: "build out", "establish", "first hire", \
"green-field"

Discount for: "support", "assist", "execute", "maintain", \
"backfill", "rotational". These are individual-contributor maintenance roles.

Score buckets:
- 90-100: founding/0-to-1 role at a hypergrowth scale-up
- 70-89: clear leadership with CXO visibility at growth-stage
- 50-69: regular IC with some growth signals
- 30-49: maintenance IC or replacement hire
- 0-29: junior backfill / rotational program

Output strict JSON only:
{"score": 0-100, "rationale": "one sentence, ≤200 chars"}"""


async def _score_growth(*, job: dict[str, Any]) -> DimensionScore:
    """growth dimension via Sonnet 4.6. ~$0.02 per call."""
    desc = (job.get("description") or "")[:6000]
    user = f"""TITLE: {job.get("title", "")}
COMPANY: {job.get("company", "")}

JOB DESCRIPTION:
{desc}

Score career-growth opportunity. Return strict JSON only."""

    result = await get_router().ask(
        provider=_LLM_PROVIDER,
        model=_LLM_MODEL,
        system=_GROWTH_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=300,
        temperature=0.15,
        agent_name="g5.growth",
    )
    parsed = _safe_parse_json(result.text)
    return DimensionScore(
        score=int(parsed.get("score", 0) or 0),
        rationale=str(parsed.get("rationale", ""))[:300],
        cost_usd=float(result.cost_usd or 0.0),
    )


# ═════════════════════════════════════════════════════════════════════════
# Dimension scorer: comp (code-only, comp_cache via comp_research)
# ═════════════════════════════════════════════════════════════════════════
async def _score_comp(
    *,
    job: dict[str, Any],
    user_id: UUID,
    user_target_total_comp: Optional[int],
) -> DimensionScore:
    """comp dimension. No LLM cost on a comp_cache hit; ~$0.02 on miss.

    Compares the company's market p50 against the user's target. Scoring
    rules:
      - No comp data:                       score=50 (neutral; can't tell)
      - target_p50 / p50 >= 1.5:            score=95 (target well below market)
      - 1.2 ≤ target/p50 < 1.5:             score=90
      - 1.0 ≤ target/p50 < 1.2:             score=80
      - 0.85 ≤ target/p50 < 1.0:            score=65
      - 0.7 ≤ target/p50 < 0.85:            score=45
      - target/p50 < 0.7:                   score=20

    When user_target is None, we read profile_master/profiles for a saved
    target_total_comp; if also missing, we fall back to "neutral 60" with
    a note that the system needs a target to give a meaningful score.
    """
    from agents.comp_research import get_comp_band

    company = job.get("company") or ""
    role = job.get("title") or ""
    location = job.get("location") or None
    level = job.get("archetype") or _heuristic_level_from_title(role)

    if not company or not role:
        return DimensionScore(
            score=50,
            rationale="No company/role on the job row — cannot research comp.",
            cost_usd=0.0,
        )

    try:
        band = await get_comp_band(
            company=company,
            role=role,
            level=level,
            location=location,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("scoring_agent: comp lookup failed for %s/%s: %r", company, role, exc)
        return DimensionScore(
            score=50,
            rationale=f"comp_research lookup failed ({type(exc).__name__}); neutral score.",
            cost_usd=0.0,
        )

    if not band.has_data() or band.p50 is None:
        return DimensionScore(
            score=50,
            rationale="No comp data available for this (company, role, location); neutral score.",
            cost_usd=float(band.cost_usd or 0.0),
        )

    p50 = float(band.p50)
    target = user_target_total_comp
    if target is None:
        # Fall back to industry-typical anchor for the level (rough but
        # better than nothing). These numbers are USD and intentionally
        # conservative — the comp dimension should ANCHOR the user's
        # actual target, not the system's guess. If they care, they pass
        # user_target.
        target = _industry_typical_target(level)
    if target is None or target <= 0:
        return DimensionScore(
            score=60,
            rationale=(
                f"No user target_comp; cannot evaluate band fit (market p50 "
                f"~{band.currency} {int(p50):,}). Set profile target to "
                f"refine."
            ),
            cost_usd=float(band.cost_usd or 0.0),
        )

    ratio = p50 / float(target)  # >1 means market beats target → great
    if ratio >= 1.5:
        score = 95
    elif ratio >= 1.2:
        score = 90
    elif ratio >= 1.0:
        score = 80
    elif ratio >= 0.85:
        score = 65
    elif ratio >= 0.7:
        score = 45
    else:
        score = 20

    rationale = (
        f"Market p50 {band.currency} {int(p50):,} vs target "
        f"{band.currency} {int(target):,} → ratio {ratio:.2f}x "
        f"({'above' if ratio >= 1.0 else 'below'} target)."
    )
    return DimensionScore(
        score=score,
        rationale=rationale[:300],
        cost_usd=float(band.cost_usd or 0.0),
    )


def _heuristic_level_from_title(title: str) -> Optional[str]:
    """Crude title-based level extraction. Mirrors the workspace endpoint."""
    if not title:
        return None
    t = title.lower()
    for token in ("principal", "staff", "head of", "director", "lead",
                  "senior", "junior", "intern"):
        if token in t:
            return token.title()
    return None


def _industry_typical_target(level: Optional[str]) -> Optional[int]:
    """Fallback target_comp by level. USD total comp ballpark for the
    PM/Manager/Staff archetypes the user targets."""
    if not level:
        return 150_000
    l = level.lower()
    if "principal" in l or "staff" in l or "director" in l or "head of" in l:
        return 280_000
    if "senior" in l or "lead" in l:
        return 200_000
    if "junior" in l or "intern" in l:
        return 80_000
    return 150_000


# ═════════════════════════════════════════════════════════════════════════
# Dimension scorer: culture (Sonnet 4.6 + pgvector RAG)
# ═════════════════════════════════════════════════════════════════════════
_CULTURE_SYSTEM = """You are a culture-fit analyst. Given recent company_knowledge \
passages and the candidate's stated values (success_patterns / banned keywords \
from the persona), score how well the candidate would mesh with this company \
0-100.

Green flags (push score UP):
- Mission alignment with candidate's stated values
- Work-life balance signals (PTO, no-on-call language, async-first)
- Healthy review signals (>3.8 Glassdoor, no recent "toxic" reviews)
- Stable leadership (no <12-month CEO turnover)
- Persona success_patterns appear in the company's described culture

Red flags (push score DOWN):
- Recent layoff news (last 90 days)
- "Micromanagement", "low autonomy", "burnout" in employee reviews
- Repeated controversies (lawsuits, regulatory action, fraud)
- Persona banned keywords appear in the company's described culture

IDENTITY LOCK: cite SPECIFIC passages by their section name. Do NOT \
reference companies other than the one being scored. If the passages are \
empty or don't talk about culture, return score=50 with rationale \
"insufficient signal".

Output strict JSON only:
{"score": 0-100, "rationale": "one sentence, ≤200 chars, references a \
specific company_knowledge section or persona pattern when available"}"""


async def _score_culture(
    *,
    job: dict[str, Any],
    persona: Optional[dict],
) -> DimensionScore:
    """culture dimension. Sonnet 4.6 + RAG over company_knowledge. ~$0.05."""
    from agents.g6_io import load_recent_company_knowledge

    company = job.get("company") or ""
    if not company:
        return DimensionScore(
            score=50,
            rationale="No company name on job — neutral score.",
            cost_usd=0.0,
        )

    chunks: list[dict] = []
    try:
        chunks = await load_recent_company_knowledge(
            company_name=company,
            query="culture values work-life-balance leadership reviews layoffs",
            match_count=6,
            max_age_days=30,
        )
    except Exception as exc:
        logger.warning("scoring_agent: culture RAG failed for %s: %r", company, exc)

    if not chunks:
        return DimensionScore(
            score=50,
            rationale="No recent company_knowledge passages for culture — neutral score.",
            cost_usd=0.0,
        )

    cites = _cites_from_chunks(chunks)
    passages_block = "\n\n".join(
        f"[section={c.get('section')} cite:knowledge_id={c.get('id')}]\n{(c.get('content') or '')[:600]}"
        for c in chunks[:6]
    )

    persona_block = ""
    if persona:
        sp = (persona.get("success_patterns") or [])[:5]
        if sp:
            persona_block += "\nPERSONA success_patterns (candidate's tells):\n" + "\n".join(f"  - {p}" for p in sp)
        ats_bank = persona.get("ats_keyword_bank") or {}
        banned = ats_bank.get("banned") if isinstance(ats_bank, dict) else None
        if banned:
            persona_block += "\nPERSONA banned: " + ", ".join(str(b) for b in banned[:10])

    user = f"""COMPANY: {company}
JOB TITLE: {job.get("title", "")}
{persona_block}

RECENT COMPANY KNOWLEDGE (last 30 days):
{passages_block}

Score culture fit. Return strict JSON only."""

    result = await get_router().ask(
        provider=_LLM_PROVIDER,
        model=_LLM_MODEL,
        system=_CULTURE_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=400,
        temperature=0.2,
        agent_name="g5.culture",
    )
    parsed = _safe_parse_json(result.text)
    return DimensionScore(
        score=int(parsed.get("score", 50) or 50),
        rationale=str(parsed.get("rationale", ""))[:300],
        cost_usd=float(result.cost_usd or 0.0),
        cites=cites,
    )


# ═════════════════════════════════════════════════════════════════════════
# Dimension scorer: remote (code-only)
# ═════════════════════════════════════════════════════════════════════════
_REMOTE_TOKEN_REGEX = re.compile(
    r"\b(remote|hybrid|onsite|on-site|in-office|in-person|wfh|work[- ]from[- ]home|fully[- ]remote|remote[- ]first)\b",
    re.IGNORECASE,
)


def _classify_remote_mode(*, location: str, description: str) -> str:
    """Returns one of: 'remote' | 'hybrid' | 'onsite' | 'unknown'."""
    blob = f"{location} {description}".lower()
    if not blob.strip():
        return "unknown"
    # Hard remote signals first — they take precedence over location strings
    # like "London, UK (Remote)".
    if re.search(r"\b(fully[- ]remote|remote[- ]first|100%\s*remote)\b", blob):
        return "remote"
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if re.search(r"\bremote\b", blob) and not re.search(r"\bnot\s+remote\b", blob):
        return "remote"
    if re.search(r"\b(on[- ]?site|in[- ]office|in[- ]person|wfo)\b", blob):
        return "onsite"
    # Default: if there's a non-empty location and no remote signal at all,
    # assume onsite — that's the safer side for a remote-preferring user.
    return "onsite"


def _score_remote(
    *,
    job: dict[str, Any],
    user_remote_preference: Optional[str],
) -> DimensionScore:
    """remote dimension. Code-only. $0.

    Scoring matrix (user preference × job mode):
                       job=remote  job=hybrid  job=onsite  job=unknown
        user=remote        100          70          20         50
        user=hybrid         90         100          50         60
        user=onsite         40          80         100         60
        user=any            85          85          85         70
    """
    job_mode = _classify_remote_mode(
        location=str(job.get("location") or ""),
        description=str(job.get("description") or ""),
    )
    pref = (user_remote_preference or "any").lower()
    if pref not in {"remote", "hybrid", "onsite", "any"}:
        pref = "any"

    matrix: dict[tuple[str, str], int] = {
        ("remote", "remote"): 100, ("remote", "hybrid"): 70,
        ("remote", "onsite"): 20,  ("remote", "unknown"): 50,
        ("hybrid", "remote"): 90,  ("hybrid", "hybrid"): 100,
        ("hybrid", "onsite"): 50,  ("hybrid", "unknown"): 60,
        ("onsite", "remote"): 40,  ("onsite", "hybrid"): 80,
        ("onsite", "onsite"): 100, ("onsite", "unknown"): 60,
        ("any",    "remote"): 85,  ("any",    "hybrid"): 85,
        ("any",    "onsite"): 85,  ("any",    "unknown"): 70,
    }
    score = matrix.get((pref, job_mode), 70)
    rationale = (
        f"Job mode '{job_mode}' vs user preference '{pref}' → {score}/100."
    )
    return DimensionScore(score=score, rationale=rationale, cost_usd=0.0)


# ═════════════════════════════════════════════════════════════════════════
# Dimension scorer: trajectory (Sonnet 4.6 + pgvector RAG)
# ═════════════════════════════════════════════════════════════════════════
_TRAJECTORY_SYSTEM = """You are a company-trajectory analyst. Given recent \
company_knowledge passages, score 0-100 the company's trajectory \
(growth vs contraction).

Up signals (push score UP):
- New funding round announced (last 90 days), especially Series B+
- Headcount growth ("hiring 200", "doubling team", "expanding into <region>")
- Product launches, key partnerships, M&A as ACQUIRER
- Stable C-suite with multi-year tenure

Down signals (push score DOWN):
- Layoffs, hiring freezes, RIFs in last 90 days
- CEO/CFO/CPO turnover within last 6 months
- Revenue down-rounds, missed earnings, regulatory action
- M&A as TARGET (might mean roles get reorganised away)

IDENTITY LOCK: cite SPECIFIC passages. Do NOT reference companies other \
than the one being scored. If passages are empty, return score=50 with \
rationale "insufficient signal".

Output strict JSON only:
{"score": 0-100, "rationale": "one sentence, ≤200 chars, cites \
knowledge_id when available"}"""


async def _score_trajectory(*, job: dict[str, Any]) -> DimensionScore:
    """trajectory dimension. Sonnet 4.6 + RAG. ~$0.05."""
    from agents.g6_io import load_recent_company_knowledge

    company = job.get("company") or ""
    if not company:
        return DimensionScore(
            score=50,
            rationale="No company name on job — neutral score.",
            cost_usd=0.0,
        )

    chunks: list[dict] = []
    try:
        chunks = await load_recent_company_knowledge(
            company_name=company,
            query="funding hiring layoffs leadership-change growth contraction",
            match_count=6,
            max_age_days=90,
        )
    except Exception as exc:
        logger.warning("scoring_agent: trajectory RAG failed for %s: %r", company, exc)

    if not chunks:
        return DimensionScore(
            score=50,
            rationale="No recent company_knowledge — neutral trajectory score.",
            cost_usd=0.0,
        )

    cites = _cites_from_chunks(chunks)
    passages_block = "\n\n".join(
        f"[section={c.get('section')} cite:knowledge_id={c.get('id')}]\n{(c.get('content') or '')[:600]}"
        for c in chunks[:6]
    )

    user = f"""COMPANY: {company}

RECENT COMPANY KNOWLEDGE (last 90 days):
{passages_block}

Score trajectory. Return strict JSON only."""

    result = await get_router().ask(
        provider=_LLM_PROVIDER,
        model=_LLM_MODEL,
        system=_TRAJECTORY_SYSTEM,
        messages=[{"role": "user", "content": user}],
        max_tokens=400,
        temperature=0.2,
        agent_name="g5.trajectory",
    )
    parsed = _safe_parse_json(result.text)
    return DimensionScore(
        score=int(parsed.get("score", 50) or 50),
        rationale=str(parsed.get("rationale", ""))[:300],
        cost_usd=float(result.cost_usd or 0.0),
        cites=cites,
    )


# ═════════════════════════════════════════════════════════════════════════
# Persona-as-critic — §11.1 requirement
# ═════════════════════════════════════════════════════════════════════════
def _persona_critic(
    *,
    dims: dict[str, DimensionScore],
    persona: Optional[dict],
) -> tuple[dict[str, DimensionScore], list[str]]:
    """Verify that role_fit + culture rationales reference at least ONE
    success_pattern from the persona. If not, log a warning and downgrade
    culture by 10. Returns (possibly-mutated dims, list-of-warnings).

    This is the structural enforcement layer — the LLM is told to cite a
    pattern, but we can't trust prompts alone (per the persona-as-critic
    principle). If the persona is empty (no synthesis run yet for this
    company), we skip the check rather than penalise the score.
    """
    warnings: list[str] = []
    if not persona:
        return dims, warnings
    success_patterns_raw = persona.get("success_patterns") or []
    if not isinstance(success_patterns_raw, list) or not success_patterns_raw:
        return dims, warnings
    # Tokenise each pattern down to the most distinctive 2-3 word phrases.
    # We don't require verbatim match — paraphrase counts — so we check for
    # any of a pattern's content words appearing in the rationale.
    pattern_tokens: list[set[str]] = []
    for p in success_patterns_raw[:8]:
        toks = {t.lower() for t in re.findall(r"\b\w{4,}\b", str(p))}
        # Drop common filler — focuses the check on signal words.
        toks -= {"with", "from", "into", "their", "across", "through", "this",
                 "that", "using", "while", "after", "before", "every", "have",
                 "than", "more", "most", "team", "teams", "product", "products"}
        if toks:
            pattern_tokens.append(toks)

    if not pattern_tokens:
        return dims, warnings

    def _rationale_cites_pattern(rationale: str) -> bool:
        r = rationale.lower()
        for toks in pattern_tokens:
            if any(t in r for t in toks):
                return True
        return False

    for dim_name in ("role_fit", "culture"):
        dim = dims.get(dim_name)
        if dim is None:
            continue
        if not _rationale_cites_pattern(dim.rationale):
            warnings.append(
                f"{dim_name}: rationale doesn't reference any persona "
                f"success_pattern — possible fabrication"
            )

    # Only downgrade culture (per spec). role_fit warnings surface but
    # don't reduce its score — we'd rather log a warning than penalise a
    # legitimately good fit just because the LLM phrased its rationale
    # in a way our tokeniser doesn't catch.
    culture = dims.get("culture")
    if culture and any("culture:" in w for w in warnings):
        culture.score = max(0, culture.score - 10)
        culture.rationale = (
            culture.rationale + " [persona_critic: -10 — no success_pattern cited]"
        )[:300]
        dims["culture"] = culture

    return dims, warnings


# ═════════════════════════════════════════════════════════════════════════
# Persistence
# ═════════════════════════════════════════════════════════════════════════
def _persist_score(
    *,
    job_id: int,
    user_id: str,
    breakdown: dict[str, Any],
    letter_grade: str,
) -> None:
    """Write fit_score_breakdown + letter_grade back to jobs. Best-effort —
    failure logs but doesn't raise (the scoring result is still returned
    to the caller; the next re-score cycle picks up the stuck row)."""
    db = get_supabase()
    try:
        db.table("jobs").update({
            "fit_score_breakdown": breakdown,
            "letter_grade": letter_grade,
        }).eq("id", job_id).eq("user_id", user_id).execute()
    except Exception as exc:
        logger.warning(
            "scoring_agent: persist failed for job_id=%s: %r", job_id, exc
        )


# ═════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════
async def score_role(
    *,
    job_id: int,
    user_id: UUID,
    force: bool = False,
    user_target_total_comp: Optional[int] = None,
    user_remote_preference: Optional[str] = None,
) -> dict[str, Any]:
    """Score one job across 6 dimensions; persist; return the breakdown.

    Wallet guard via api._job_guards.load_open_job — closed / validation-
    failed jobs raise 409 unless force=True.

    Idempotency: if `fit_score_breakdown.scored_at` is < RESCORE_STALENESS_DAYS
    old and force=False, we short-circuit and return the existing breakdown
    without re-scoring. This is how the JobScout auto-trigger keeps cost
    bounded across daily refreshes.

    Returns the same dict that gets written to fit_score_breakdown — the
    API endpoint surfaces this directly to the dashboard.
    """
    db = get_supabase()
    # load_open_job 404s on missing, 409s on stale-without-force.
    job = load_open_job(
        db,
        job_id=job_id,
        user_id=user_id,
        force=force,
        cost_label="G5 evaluation scoring",
    )

    # Idempotency short-circuit. Re-scoring within the staleness window
    # is wasted spend; the existing breakdown is returned as-is.
    if not force and _is_recently_scored(job):
        existing = job.get("fit_score_breakdown") or {}
        return {
            **existing,
            "skipped": True,
            "skip_reason": "already_scored_within_window",
        }

    company = job.get("company") or ""
    user_id_str = str(user_id)
    persona = _load_company_persona(user_id_str, company)
    profile_competencies = _load_profile_competencies(user_id_str)

    # Fan out the six dimensions. We run them sequentially rather than via
    # asyncio.gather because:
    #   1. Anthropic prompt caching benefits most from cache-prefix
    #      warm-up — sequential lets a later call reuse the same client.
    #   2. The shared `comp_cache` lookup in _score_comp can race against
    #      itself on parallel calls for the same (company, role) tuple.
    #   3. Per-call latency is ~2s; 6 sequential ≈ 12s, well under the
    #      30s typical API timeout.
    # If profiling shows this becomes a bottleneck at scale, switch to
    # asyncio.gather of the four LLM dimensions and keep comp + remote
    # synchronous.
    role_fit = await _score_role_fit(
        job=job,
        profile_competencies=profile_competencies,
        persona=persona,
    )
    growth = await _score_growth(job=job)
    comp = await _score_comp(
        job=job,
        user_id=user_id,
        user_target_total_comp=user_target_total_comp,
    )
    culture = await _score_culture(job=job, persona=persona)
    remote = _score_remote(
        job=job,
        user_remote_preference=user_remote_preference,
    )
    trajectory = await _score_trajectory(job=job)

    dims: dict[str, DimensionScore] = {
        "role_fit":   role_fit,
        "growth":     growth,
        "comp":       comp,
        "culture":    culture,
        "remote":     remote,
        "trajectory": trajectory,
    }

    # Persona-as-critic (§11.1) — may downgrade culture by 10.
    dims, persona_warnings = _persona_critic(dims=dims, persona=persona)

    composite = _composite_score(dims)
    grade = _letter_grade(composite)
    total_cost = sum(d.cost_usd for d in dims.values())

    # Flatten cites across the dimensions that produce them.
    all_cites: list[dict[str, Any]] = []
    for dim in (culture, trajectory):
        all_cites.extend(dim.cites or [])

    breakdown: dict[str, Any] = {
        **{name: d.score for name, d in dims.items()},
        "composite":   composite,
        "letter_grade": grade,
        "rationale": {name: d.rationale for name, d in dims.items()},
        "cites": all_cites,
        "persona_warnings": persona_warnings,
        "weights": dict(WEIGHTS),
        "cost_usd": round(total_cost, 6),
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "scorer_version": SCORER_VERSION,
    }

    _persist_score(
        job_id=job_id,
        user_id=user_id_str,
        breakdown=breakdown,
        letter_grade=grade,
    )

    return breakdown


async def get_existing_score(
    *,
    job_id: int,
    user_id: UUID,
) -> Optional[dict[str, Any]]:
    """Fetch the existing fit_score_breakdown without re-scoring. Returns
    None if the job hasn't been scored yet (caller decides whether to
    enqueue a fresh score or surface a "not scored" state)."""
    db = get_supabase()
    rows = (
        db.table("jobs")
        .select("id, fit_score_breakdown, letter_grade")
        .eq("id", job_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        return None
    breakdown = rows[0].get("fit_score_breakdown")
    if not isinstance(breakdown, dict):
        return None
    # Defensive: keep the surface column in sync if a hand-edit drifted it.
    if rows[0].get("letter_grade") and rows[0].get("letter_grade") != breakdown.get("letter_grade"):
        breakdown["letter_grade"] = rows[0]["letter_grade"]
    return breakdown


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════
def _safe_parse_json(text: str) -> dict:
    """Tolerant JSON parse. Returns {} on any failure so the dimension
    scorer can fall back to neutral rather than crashing the whole G5 run."""
    try:
        parsed = _parse_json_loose(text or "")
        if isinstance(parsed, dict):
            return parsed
    except Exception as exc:
        logger.debug("scoring_agent: JSON parse failed: %r", exc)
    return {}
