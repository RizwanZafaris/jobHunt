"""
agents/legitimacy_agent.py — Tier 2 §4.3 Legitimacy agent v1.

Detects "ghost postings" — jobs that look real but are dead-from-the-start
(already filled internally, posted for compliance, recruiter bait,
"always hiring" sourcing pools, etc.). The freshness gate (Tier 1) catches
stale postings; this catches the dead-from-the-start ones.

Architecture
------------
career-ops uses a 6-signal heuristic. v1 ships **4 of the 6 signals**:

    Signal 1 — Posting age            (weight 0.25, $0)
    Signal 2 — URL reachability       (weight 0.25, $0 cached)
    Signal 3 — Repost pattern (90d)   (weight 0.25, $0)
    Signal 5 — Recent news (Perplex)  (weight 0.25, ~$0.005)
    Composite score = sum(weight_i * normalised_i)

Signals 4 (company hiring velocity) and 6 (per-company response rate) are
deferred to v2 — they need >=50 application outcomes to be meaningful.

Output
------
LegitimacyResult.score  ∈ [0.00, 1.00]
LegitimacyResult.tier   ∈ {"legitimate", "caution", "suspicious"}
    score >= 0.7 → "legitimate"
    0.5 <= score < 0.7 → "caution"
    score < 0.5 → "suspicious"
LegitimacyResult.signals  dict[signal_name] -> {raw_value, normalised, weight, contribution}
LegitimacyResult.cost_usd  Perplexity charge for this run (0 if cached)

The result is written back to jobs.legitimacy_score, jobs.legitimacy_tier,
jobs.legitimacy_signals. The /today filter hides suspicious by default
(see api/actions.py::_build_job_actions).

Cost target
-----------
~$1/month at current scale (50 new jobs/week × $0.005). Per-job ceiling
$0.005 — only signal 5 has a marginal cost; signals 1/2/3 are $0.
URL reachability and news both cache for 24h to keep the cron-driven
re-scoring at ~$0 amortised.

Threading
---------
Pure async, no class. Caller (queue worker, API endpoint) is responsible
for transactional updates and for skipping stale jobs (load_open_job
refuses them upstream).

User scope
----------
Every read/write is scoped to user_id. RLS on `jobs` enforces this server-
side; we pass user_id explicitly so the agent can run in worker context
(no FastAPI dependency).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ─── Static weights (v1 — equal across 4 signals) ─────────────────────────
WEIGHT_AGE = 0.25
WEIGHT_URL = 0.25
WEIGHT_REPOST = 0.25
WEIGHT_NEWS = 0.25
assert abs(WEIGHT_AGE + WEIGHT_URL + WEIGHT_REPOST + WEIGHT_NEWS - 1.0) < 1e-6

# ─── Tier thresholds ──────────────────────────────────────────────────────
TIER_LEGITIMATE_MIN = 0.7
TIER_CAUTION_MIN = 0.5

# ─── Signal 1 — Posting age decay ─────────────────────────────────────────
AGE_FRESH_DAYS = 7      # score 1.0 below this
AGE_DEAD_DAYS = 60      # score 0.0 at or above this

# ─── Signal 2 — URL reachability score table ─────────────────────────────
# 200/301/302 → 1.0 (live, possibly moved)
# 404         → 0.5 (could have been moved — career-ops calls this "ambiguous")
# 410/500+    → 0.0 (gone / server error / dead)
# timeout/0   → 0.0 (treat as dead — repeated 0-network-status means user
#                    can't apply anyway)
URL_SCORE_BY_STATUS: dict[int, float] = {
    200: 1.0, 301: 1.0, 302: 1.0, 303: 1.0, 307: 1.0, 308: 1.0,
    404: 0.5,
    410: 0.0,
}

# ─── Signal 3 — Repost pattern ────────────────────────────────────────────
# Career-ops finding: reposting often signals a failed previous search →
# the role may not actually be filled, but the org is shopping for an
# external candidate while the team waits for an internal promotion.
REPOST_SCORE_BY_COUNT: dict[int, float] = {
    0: 1.0,   # first posting
    1: 0.7,   # mild flag
    2: 0.4,   # moderate flag
}
REPOST_HIGH_COUNT_SCORE = 0.1  # 3+ duplicates in 90d

# ─── Signal 5 — News cache TTL ────────────────────────────────────────────
NEWS_CACHE_TTL_HOURS = 24

# ─── URL check cache TTL — same posting can be re-scored cheap ───────────
URL_CACHE_TTL_HOURS = 24

# ─── Suspicious keyword classifier for the news signal ────────────────────
# We classify the Perplexity summary into three buckets without a second
# LLM call — keyword scan is good enough for v1 and keeps the cost at
# ~$0.005/job (one Perplexity call, no follow-up).
_NEGATIVE_NEWS_PATTERNS = [
    r"\blay\s?off",         # "layoffs", "lay off", "laid off"
    r"\bjob\s?cuts?\b",
    r"\bworkforce\s+reduction",
    r"\brif\b",
    r"\bhiring\s+freeze",
    r"\bhiring\s+pause",
    r"\bpause(d)?\s+hiring",
    r"\bdownsiz",
    r"\brestructur",
    r"\bredundanc",
    r"\bclosing\s+offices?",
    r"\bbankrupt",
]
_POSITIVE_NEWS_PATTERNS = [
    r"\bhiring\b",
    r"\bgrowing\s+(team|the\s+team|engineering|product)",
    r"\bexpand(ing)?\s+(into|the\s+team|operations)",
    r"\b(announce(d|s)?|opens?)\s+(new\s+)?(office|HQ|hub)",
    r"\bopen\s+roles?\b",
    r"\bjob\s+openings?\b",
    r"\bseries\s+[A-Z]\b",
    r"\b\$\d+(\.\d+)?\s*(b|bn|billion|m|mn|million)\s+(round|funding|raise)\b",
    r"\braised\s+\$",
]

_NEGATIVE_RE = re.compile("|".join(_NEGATIVE_NEWS_PATTERNS), re.IGNORECASE)
_POSITIVE_RE = re.compile("|".join(_POSITIVE_NEWS_PATTERNS), re.IGNORECASE)


# ─── Data classes ─────────────────────────────────────────────────────────
@dataclass
class SignalContribution:
    """One row in the per-signal breakdown."""

    raw_value: Any
    normalised: float  # 0.0..1.0
    weight: float
    contribution: float  # normalised * weight

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_value": self.raw_value,
            "normalised": round(self.normalised, 4),
            "weight": round(self.weight, 4),
            "contribution": round(self.contribution, 4),
        }


@dataclass
class LegitimacyResult:
    """Return shape from score_legitimacy()."""

    score: float                                 # composite 0.00..1.00
    tier: Literal["legitimate", "caution", "suspicious"]
    signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    cost_usd: float = 0.0


# ─── Signal 1 — Posting age (free, sync) ─────────────────────────────────
def _score_age(discovered_at: Optional[Any]) -> SignalContribution:
    """Linear decay from 1.0 at <7d to 0.0 at >=60d.

    Uses jobs.discovered_at, preserved on re-discovery (BUG-026 fix —
    never overwritten). NULL discovered_at degrades to 0.5 (unknown) so
    we don't accidentally tank ancient legacy rows or boost them.
    """
    if discovered_at is None:
        return SignalContribution(
            raw_value=None,
            normalised=0.5,
            weight=WEIGHT_AGE,
            contribution=0.5 * WEIGHT_AGE,
        )

    dt = _parse_dt(discovered_at)
    if dt is None:
        return SignalContribution(
            raw_value=str(discovered_at)[:32],
            normalised=0.5,
            weight=WEIGHT_AGE,
            contribution=0.5 * WEIGHT_AGE,
        )

    age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    if age_days < AGE_FRESH_DAYS:
        normalised = 1.0
    elif age_days >= AGE_DEAD_DAYS:
        normalised = 0.0
    else:
        # Linear decay between (AGE_FRESH_DAYS, 1.0) and (AGE_DEAD_DAYS, 0.0)
        span = AGE_DEAD_DAYS - AGE_FRESH_DAYS
        normalised = 1.0 - (age_days - AGE_FRESH_DAYS) / span

    return SignalContribution(
        raw_value=round(age_days, 2),
        normalised=round(normalised, 4),
        weight=WEIGHT_AGE,
        contribution=round(normalised * WEIGHT_AGE, 4),
    )


def _parse_dt(val: Any) -> Optional[datetime]:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        s = val.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


# ─── Signal 2 — URL reachability (async, cached) ─────────────────────────
async def _score_url(
    *,
    url: Optional[str],
    cached: Optional[dict[str, Any]] = None,
) -> SignalContribution:
    """HEAD probe; cache result in jobs.legitimacy_signals.url.

    Reuses agents/job_validation.py::_http_head_or_get so the entire
    httpx setup (timeouts, redirect handling, UA) stays consistent with
    the validation pipeline.
    """
    if not url:
        return SignalContribution(
            raw_value=None,
            normalised=0.0,
            weight=WEIGHT_URL,
            contribution=0.0,
        )

    # Cache hit?
    if cached and _is_fresh_cache(cached.get("cached_at"), URL_CACHE_TTL_HOURS):
        status = cached.get("status")
        if isinstance(status, int):
            normalised = _url_score_for_status(status)
            return SignalContribution(
                raw_value={"status": status, "cached_at": cached["cached_at"]},
                normalised=normalised,
                weight=WEIGHT_URL,
                contribution=round(normalised * WEIGHT_URL, 4),
            )

    # Live HEAD probe — fall back to in-module httpx if the helper can't be
    # imported (e.g. test environment with a different mock surface).
    status = await _probe_url(url)
    normalised = _url_score_for_status(status)
    return SignalContribution(
        raw_value={
            "status": status,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        },
        normalised=normalised,
        weight=WEIGHT_URL,
        contribution=round(normalised * WEIGHT_URL, 4),
    )


async def _probe_url(url: str) -> int:
    """Wrapper around agents.job_validation._http_head_or_get."""
    try:
        from agents.job_validation import _http_head_or_get
        status, _final_url, _body = await _http_head_or_get(url)
        return int(status)
    except Exception as e:
        logger.debug("legitimacy URL probe failed for %s: %s", url, e)
        return 0


def _url_score_for_status(status: int) -> float:
    """Map an HTTP status code to a [0.0, 1.0] reachability score."""
    if status in URL_SCORE_BY_STATUS:
        return URL_SCORE_BY_STATUS[status]
    if 200 <= status < 400:
        return 1.0  # any other 2xx/3xx is fine
    if status >= 500:
        return 0.0
    if status == 0:
        # network timeout / DNS error — career-ops treats this as dead
        return 0.0
    # 4xx other than 404/410 — ambiguous (e.g. 401 behind paywall)
    return 0.3


def _is_fresh_cache(cached_at: Any, ttl_hours: int) -> bool:
    if not cached_at:
        return False
    dt = _parse_dt(cached_at)
    if dt is None:
        return False
    return (datetime.now(timezone.utc) - dt) < timedelta(hours=ttl_hours)


# ─── Signal 3 — Repost pattern (sync, DB) ────────────────────────────────
def _score_repost(
    *,
    db,
    user_id: UUID,
    company: Optional[str],
    title: Optional[str],
    self_job_id: int,
) -> SignalContribution:
    """COUNT distinct jobs by (company, title) in the last 90d, excluding self.

    The DB query is scoped to user_id so other users' postings don't bleed
    into the count (RLS would enforce anyway; we double up explicitly).
    """
    if not company or not title:
        return SignalContribution(
            raw_value={"company": company, "title": title, "count": None},
            normalised=0.5,
            weight=WEIGHT_REPOST,
            contribution=0.5 * WEIGHT_REPOST,
        )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    title_pattern = f"%{_sanitise_like(title)}%"
    try:
        # PostgREST: ilike is fnmatch-style, with `*` as wildcard.
        # The python-supabase client maps `.ilike(col, pattern)` to ILIKE.
        # We need an exact-ish title match, so we wrap in '%...%' and let
        # the index seek do its thing. discovered_at index is non-partial,
        # so this stays cheap.
        rows = (
            db.table("jobs")
            .select("id", count="exact")
            .eq("user_id", str(user_id))
            .eq("company", company)
            .ilike("title", title_pattern)
            .gte("discovered_at", cutoff)
            .neq("id", self_job_id)
            .limit(10)
            .execute()
        )
        # Supabase python returns .count for `count="exact"` queries.
        count = getattr(rows, "count", None)
        if count is None:
            count = len(rows.data or [])
    except Exception as e:
        logger.debug("legitimacy repost query failed: %s", e)
        # Fail-open at 0.5 (neutral) so a DB hiccup doesn't punish jobs.
        return SignalContribution(
            raw_value={"error": str(e)[:120]},
            normalised=0.5,
            weight=WEIGHT_REPOST,
            contribution=0.5 * WEIGHT_REPOST,
        )

    normalised = REPOST_SCORE_BY_COUNT.get(int(count), REPOST_HIGH_COUNT_SCORE)
    return SignalContribution(
        raw_value={"duplicate_count": int(count), "lookback_days": 90},
        normalised=normalised,
        weight=WEIGHT_REPOST,
        contribution=round(normalised * WEIGHT_REPOST, 4),
    )


def _sanitise_like(s: str) -> str:
    """Strip ILIKE wildcards from user-controlled title text."""
    return s.replace("%", "").replace("_", "").strip()


# ─── Signal 5 — Recent news (async, cached, Perplexity) ──────────────────
async def _score_news(
    *,
    company: Optional[str],
    role: Optional[str],
    cached: Optional[dict[str, Any]] = None,
    user_id: UUID,
) -> tuple[SignalContribution, float]:
    """Call Perplexity recency_check and classify the summary.

    Returns (SignalContribution, cost_usd). Cost is 0 on cache hit.

    Caching: jobs.legitimacy_signals.news.cached_at — 24h TTL. The cron
    re-scores jobs every ~6h; within a day each Perplexity call gets
    amortised across multiple scoring requests.
    """
    if not company:
        return (
            SignalContribution(
                raw_value=None,
                normalised=0.5,
                weight=WEIGHT_NEWS,
                contribution=0.5 * WEIGHT_NEWS,
            ),
            0.0,
        )

    # Cache hit?
    if cached and _is_fresh_cache(cached.get("cached_at"), NEWS_CACHE_TTL_HOURS):
        normalised = float(cached.get("normalised", 0.5))
        return (
            SignalContribution(
                raw_value={
                    "summary": (cached.get("summary") or "")[:400],
                    "verdict": cached.get("verdict"),
                    "citations": cached.get("citations") or [],
                    "cached_at": cached["cached_at"],
                },
                normalised=normalised,
                weight=WEIGHT_NEWS,
                contribution=round(normalised * WEIGHT_NEWS, 4),
            ),
            0.0,
        )

    summary, citations, cost_usd = await _call_perplexity_news(
        company=company,
        role=role or "product manager",
        user_id=user_id,
    )
    verdict, normalised = _classify_news(summary)
    contribution = SignalContribution(
        raw_value={
            "summary": (summary or "")[:400],
            "verdict": verdict,
            "citations": citations,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        },
        normalised=normalised,
        weight=WEIGHT_NEWS,
        contribution=round(normalised * WEIGHT_NEWS, 4),
    )
    return contribution, cost_usd


async def _call_perplexity_news(
    *,
    company: str,
    role: str,
    user_id: UUID,
) -> tuple[str, list[dict[str, Any]], float]:
    """Run a Perplexity recency_check tuned for "are they hiring this role?".

    Logs the call to agent_call_log with agent_name='legitimacy.news' and
    graph=NULL (utility call — not part of any graph).
    """
    try:
        from agents.perplexity_search import recency_check
        result = await recency_check(
            company=company,
            days=30,
            industry_hint="fintech / payments / financial technology",
        )
    except Exception as e:
        logger.warning(
            "legitimacy.news Perplexity call failed for %r: %s",
            company,
            e,
        )
        return ("", [], 0.0)

    summary = str(result.get("summary") or "")
    citations = list(result.get("citations") or [])
    cost = float(result.get("cost_usd") or 0.0)

    _log_perplexity_cost(
        user_id=user_id,
        company=company,
        role=role,
        cost_usd=cost,
        model=str(result.get("model") or "sonar"),
        input_tokens=int((result.get("raw_tokens") or {}).get("input") or 0),
        output_tokens=int((result.get("raw_tokens") or {}).get("output") or 0),
    )
    return summary, citations, cost


def _log_perplexity_cost(
    *,
    user_id: UUID,
    company: str,
    role: str,
    cost_usd: float,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Best-effort insert into agent_call_log. Non-fatal."""
    try:
        from db.client import get_supabase
        get_supabase().table("agent_call_log").insert({
            "agent_name": "legitimacy.news",
            "graph": None,                # utility call, not a graph node
            "node_name": "perplexity_recency_check",
            "provider": "perplexity",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "latency_ms": 0,              # recency_check doesn't surface latency
            "called_at": datetime.now(timezone.utc).isoformat(),
            "user_id": str(user_id),
        }).execute()
    except Exception as e:
        logger.debug("legitimacy.news agent_call_log insert failed: %s", e)


def _classify_news(summary: str) -> tuple[str, float]:
    """Three-bucket classifier on the Perplexity summary.

    Returns ("verdict", normalised). Verdict ∈
    {"positive", "neutral", "negative", "no_signal"}.

    Score table (per roadmap §4.3 signal 5):
      positive hiring news    → 1.0
      neutral                 → 0.7
      layoff/freeze news      → 0.3
      no signal at all        → 0.5  (Perplexity returned empty / "no news")
    """
    s = (summary or "").strip()
    if not s or len(s) < 40:
        return "no_signal", 0.5

    negatives = len(_NEGATIVE_RE.findall(s))
    positives = len(_POSITIVE_RE.findall(s))

    if negatives >= 1 and negatives >= positives:
        return "negative", 0.3
    if positives >= 2 and negatives == 0:
        return "positive", 1.0
    if positives >= 1:
        return "positive", 1.0
    if negatives >= 1:
        return "negative", 0.3
    return "neutral", 0.7


# ─── Composite scoring + tier mapping ─────────────────────────────────────
def _tier_for(score: float) -> Literal["legitimate", "caution", "suspicious"]:
    if score >= TIER_LEGITIMATE_MIN:
        return "legitimate"
    if score >= TIER_CAUTION_MIN:
        return "caution"
    return "suspicious"


# ─── Public API ───────────────────────────────────────────────────────────
async def score_legitimacy(
    job_id: int,
    user_id: UUID,
    force: bool = False,
) -> LegitimacyResult:
    """Compute 4-signal legitimacy for one job and persist to jobs columns.

    Args:
        job_id: jobs.id (integer).
        user_id: Supabase auth UUID. Read/write scoped to this user.
        force: bypass the 24h cache for signal 5 (recompute Perplexity).

    Returns:
        LegitimacyResult with the per-signal breakdown + composite score.

    Persistence:
        jobs.legitimacy_score   ← result.score    (NUMERIC(3,2))
        jobs.legitimacy_tier    ← result.tier     ("legitimate"|"caution"|"suspicious")
        jobs.legitimacy_signals ← per-signal dict (see SignalContribution.as_dict)

    Wallet guard:
        Caller is expected to wrap with api._job_guards.load_open_job so
        we never spend Perplexity dollars on jobs already marked closed.
    """
    from db.client import get_supabase
    db = get_supabase()

    rows = (
        db.table("jobs")
        .select(
            "id, title, company, url, discovered_at, "
            "legitimacy_signals, posting_closed_at, validation_failed, user_id"
        )
        .eq("id", job_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise ValueError(f"job_not_found: id={job_id} user_id={user_id}")
    job = rows[0]

    existing_signals = job.get("legitimacy_signals") or {}
    if isinstance(existing_signals, list):
        # Legacy list-of-strings shape from GPT-4.1 classifier — discard,
        # we're overwriting with the v1 dict shape.
        existing_signals = {}

    url_cache = (existing_signals or {}).get("url")
    news_cache = (existing_signals or {}).get("news") if not force else None

    # Run signals 1 + 2 + 5 in parallel; signal 3 is sync (Supabase python
    # client is blocking). Run the sync one first, then await the async pair.
    age = _score_age(job.get("discovered_at"))
    repost = _score_repost(
        db=db,
        user_id=user_id,
        company=job.get("company"),
        title=job.get("title"),
        self_job_id=int(job_id),
    )
    url_contrib, news_pair = await asyncio.gather(
        _score_url(url=job.get("url"), cached=url_cache),
        _score_news(
            company=job.get("company"),
            role=job.get("title"),
            cached=news_cache,
            user_id=user_id,
        ),
    )
    news, news_cost = news_pair

    composite = round(
        age.contribution + url_contrib.contribution
        + repost.contribution + news.contribution,
        4,
    )
    # Defensive clamp.
    composite = max(0.0, min(1.0, composite))
    tier = _tier_for(composite)

    signals_out = {
        "age": age.as_dict(),
        "url": url_contrib.as_dict(),
        "repost": repost.as_dict(),
        "news": news.as_dict(),
        "version": "v1",
        "scored_at": datetime.now(timezone.utc).isoformat(),
    }

    # Persist.
    try:
        db.table("jobs").update({
            "legitimacy_score": composite,
            "legitimacy_tier": tier,
            "legitimacy_signals": signals_out,
        }).eq("id", job_id).eq("user_id", str(user_id)).execute()
    except Exception as e:
        logger.warning("legitimacy persist failed for job %s: %s", job_id, e)

    return LegitimacyResult(
        score=composite,
        tier=tier,
        signals=signals_out,
        cost_usd=round(news_cost, 6),
    )
