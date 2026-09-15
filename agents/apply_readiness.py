"""
agents/apply_readiness.py — the gate that decides whether a role is worth
applying to (G10-C).

Why this exists
---------------
The pieces were all present and none of them were wired to the apply
decision:

  - job_validation.py   drops fabricated/unreachable postings at discovery
  - legitimacy_agent.py scores ghost postings (already-filled, compliance
                        posts, recruiter bait, "always hiring" pools)
  - job_validator.py    re-checks URLs on a 6h cycle, sets posting_closed_at
  - scoring_agent.py    scores FIT across 6 dimensions

`get_eligible_jobs()` only checked `posting_closed_at IS NULL` and
`validation_failed IS NULL`. A posting could be scored A-grade, flagged
"suspicious" by the legitimacy agent, be 90 days old, and still reach the
applier. This module closes that.

Fit is not the same question as interview odds
-----------------------------------------------
G5 answers "is this role right for me". It does not answer "will anyone
read this application". Those come apart constantly:

  - a perfect-fit role posted 70 days ago is very likely already filled
  - a ghost posting has interview odds of zero at any fit score
  - a good-fit role posted yesterday, at a company where a warm referral
    path exists, is worth far more than three cold 40-day-old postings

So this module scores APPLY-WORTHINESS as a separate axis from fit, and the
apply pipeline gates on both.

Why freshness dominates the weighting
--------------------------------------
Recency is the strongest controllable signal in the set. Postings collect
applicants monotonically while a shortlist forms early, so the same
application sent on day 2 and day 30 lands in very different piles. Nothing
else here is as actionable: fit is fixed by the time you see the role, and
legitimacy is a property of the posting. Timing is the one thing a pipeline
can actually optimise, so it carries the largest weight.

Everything in this module is pure and offline except `assess_job_id()`,
which loads rows. The scoring core takes plain dicts so it can be unit
tested without a database or network.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from uuid import UUID

logger = logging.getLogger(__name__)

Decision = Literal["apply", "hold", "skip"]
InterviewOdds = Literal["high", "moderate", "low", "negligible"]


# ─── Hard blockers ───────────────────────────────────────────────────────────
# Any of these means do not apply, regardless of how good the fit is. These
# are not weighted — they are vetoes. A 95-fit ghost posting is still a ghost
# posting, and sending an application into it costs a tailored resume, an
# OpenRouter spend, and a slot in the operator's attention for nothing.
MAX_AGE_DAYS = 45              # beyond this a posting is very likely filled
MIN_LEGITIMACY_SCORE = 0.5     # below = legitimacy_agent's "suspicious" tier
MIN_FIT_COMPOSITE = 70         # below = not worth a tailored application

# ─── Weights for the apply-worthiness composite (sum = 100) ──────────────────
W_FRESHNESS = 40       # the one lever a pipeline can actually pull
W_LEGITIMACY = 25      # is anyone reading applications for this req at all
W_FIT = 25             # G5 composite
W_REFERRAL = 10        # a warm path into the company

# Decision thresholds on the composite.
APPLY_THRESHOLD = 65
HOLD_THRESHOLD = 50


@dataclass
class ReadinessVerdict:
    decision: Decision
    interview_odds: InterviewOdds
    score: int                                   # 0-100 composite
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def should_apply(self) -> bool:
        return self.decision == "apply"

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "interview_odds": self.interview_odds,
            "score": self.score,
            "reasons": self.reasons,
            "blockers": self.blockers,
            "signals": self.signals,
        }


# ─── Signal: freshness ───────────────────────────────────────────────────────
def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


# Timestamps the EMPLOYER set — these measure how long the role has been live.
AUTHORITATIVE_DATE_FIELDS = ("posted_at", "published_at", "date_posted")
# Timestamps WE set — these only bound freshness from above. A posting first
# seen yesterday may have been open for months, so age derived from these is
# a floor on staleness, never evidence of freshness.
DERIVED_DATE_FIELDS = ("discovered_at", "created_at")

# Cap applied to the freshness signal when age came from a derived field.
# Enough to keep a genuinely new posting competitive, not enough to award the
# top band on evidence we do not actually have.
DERIVED_FRESHNESS_CAP = 0.6


def posting_age_days(job: dict[str, Any]) -> tuple[Optional[int], bool]:
    """Return (age_in_days, is_authoritative).

    `is_authoritative` is True only when the age came from a date the
    EMPLOYER published. When it came from our own discovered_at/created_at,
    the age is an upper bound on freshness — we know the posting is at least
    that old, not that it is only that old — and the caller must not treat it
    as evidence of recency.
    """
    for key in AUTHORITATIVE_DATE_FIELDS:
        dt = _parse_dt(job.get(key))
        if dt:
            return max(0, (datetime.now(timezone.utc) - dt).days), True
    for key in DERIVED_DATE_FIELDS:
        dt = _parse_dt(job.get(key))
        if dt:
            return max(0, (datetime.now(timezone.utc) - dt).days), False
    return None, False


def _score_freshness(
    age_days: Optional[int], authoritative: bool = True
) -> tuple[float, str]:
    """Normalised 0..1 plus a human-readable reason.

    Unknown age scores 0.4 rather than 0 or 1: absent a date we should not
    assume a posting is fresh, but we also should not discard a role purely
    because the board omitted the field.

    When `authoritative` is False the age came from our own discovered_at
    rather than the employer's posted_at, so it is an upper bound on
    freshness. The score is capped at DERIVED_FRESHNESS_CAP — a posting we
    happened to find yesterday is not evidence that it went live yesterday,
    and awarding the top band on that would manufacture confidence we have
    not earned.
    """
    if age_days is None:
        return 0.4, "Posting date unknown — treated as moderately stale"

    if age_days <= 3:
        score, why = 1.0, f"Posted {age_days}d ago — inside the highest-response window"
    elif age_days <= 7:
        score, why = 0.85, f"Posted {age_days}d ago — still early"
    elif age_days <= 14:
        score, why = 0.60, f"Posted {age_days}d ago — shortlist likely forming"
    elif age_days <= 30:
        score, why = 0.35, f"Posted {age_days}d ago — late; shortlist probably set"
    elif age_days <= MAX_AGE_DAYS:
        score, why = 0.15, f"Posted {age_days}d ago — very late, likely filled"
    else:
        return 0.0, f"Posted {age_days}d ago — beyond the {MAX_AGE_DAYS}d cutoff"

    if not authoritative and score > DERIVED_FRESHNESS_CAP:
        score = DERIVED_FRESHNESS_CAP
        why = (
            f"First seen {age_days}d ago, but the employer published no posting "
            "date — true age unknown, so freshness is capped"
        )
    return score, why


# ─── Signal: referral path ───────────────────────────────────────────────────
def _score_referral(referral: Optional[dict[str, Any]]) -> tuple[float, str]:
    """Score a warm path into the company, from the referral graph.

    `referral` is expected to look like {"hops": int, "contact_name": str}
    where hops is the shortest path length. None means no path found.
    """
    if not referral:
        return 0.0, "No referral path found into this company"
    hops = referral.get("hops")
    who = referral.get("contact_name") or "a contact"
    if hops == 1:
        return 1.0, f"Direct connection at this company ({who})"
    if hops == 2:
        return 0.7, f"Second-degree path via {who}"
    if hops == 3:
        return 0.4, f"Third-degree path via {who}"
    return 0.2, "Distant referral path — weak but non-zero"


# ─── Core assessment (pure) ──────────────────────────────────────────────────
def assess_readiness(
    *,
    job: dict[str, Any],
    legitimacy: Optional[dict[str, Any]] = None,
    fit_breakdown: Optional[dict[str, Any]] = None,
    referral: Optional[dict[str, Any]] = None,
) -> ReadinessVerdict:
    """Decide whether to apply. Pure — no DB, no network, fully testable.

    `legitimacy` is a LegitimacyResult.as_dict()-shaped dict (score, tier).
    `fit_breakdown` is jobs.fit_score_breakdown from the G5 scorer.
    `referral` is {"hops": int, "contact_name": str} or None.
    """
    reasons: list[str] = []
    blockers: list[str] = []
    signals: dict[str, Any] = {}

    # ── Hard vetoes ─────────────────────────────────────────────────────
    if job.get("posting_closed_at"):
        blockers.append("Posting is closed (posting_closed_at is set)")
    if job.get("validation_failed"):
        blockers.append(f"Failed discovery validation: {job.get('validation_failed')}")

    age_days, age_authoritative = posting_age_days(job)
    signals["age_days"] = age_days
    signals["age_source"] = "employer" if age_authoritative else "first_seen"
    if age_days is not None and age_days > MAX_AGE_DAYS:
        blockers.append(
            f"Posting is {age_days} days old (cutoff {MAX_AGE_DAYS}) — "
            "almost certainly filled"
        )

    legit_score = float((legitimacy or {}).get("score") or 0.0)
    legit_tier = (legitimacy or {}).get("tier")
    signals["legitimacy_score"] = round(legit_score, 3)
    signals["legitimacy_tier"] = legit_tier
    if legitimacy is None:
        blockers.append("Not yet checked by the legitimacy agent")
    elif legit_tier == "suspicious" or legit_score < MIN_LEGITIMACY_SCORE:
        blockers.append(
            f"Legitimacy tier '{legit_tier}' (score {legit_score:.2f}) — "
            "likely a ghost posting"
        )

    fit_composite = int((fit_breakdown or {}).get("composite") or 0)
    signals["fit_composite"] = fit_composite
    signals["letter_grade"] = (fit_breakdown or {}).get("letter_grade")
    if not fit_breakdown:
        blockers.append("Not yet scored by G5")
    elif fit_composite < MIN_FIT_COMPOSITE:
        blockers.append(
            f"Fit composite {fit_composite} is below the {MIN_FIT_COMPOSITE} floor"
        )

    if blockers:
        return ReadinessVerdict(
            decision="skip",
            interview_odds="negligible",
            score=0,
            reasons=reasons,
            blockers=blockers,
            signals=signals,
        )

    # ── Weighted composite ──────────────────────────────────────────────
    fresh_n, fresh_why = _score_freshness(age_days, authoritative=age_authoritative)
    ref_n, ref_why = _score_referral(referral)
    fit_n = min(1.0, fit_composite / 100.0)
    legit_n = min(1.0, legit_score)

    reasons.extend([fresh_why, ref_why])
    reasons.append(f"Fit composite {fit_composite}/100")
    reasons.append(f"Legitimacy {legit_score:.2f} ({legit_tier})")

    composite = (
        fresh_n * W_FRESHNESS
        + legit_n * W_LEGITIMACY
        + fit_n * W_FIT
        + ref_n * W_REFERRAL
    )
    score = int(round(composite))

    signals["weights"] = {
        "freshness": W_FRESHNESS, "legitimacy": W_LEGITIMACY,
        "fit": W_FIT, "referral": W_REFERRAL,
    }
    signals["normalised"] = {
        "freshness": round(fresh_n, 3), "legitimacy": round(legit_n, 3),
        "fit": round(fit_n, 3), "referral": round(ref_n, 3),
    }

    if score >= APPLY_THRESHOLD:
        decision: Decision = "apply"
    elif score >= HOLD_THRESHOLD:
        decision = "hold"
    else:
        decision = "skip"

    # Interview odds read off the composite, but two signals can veto the top
    # band regardless of how high the composite climbs:
    #
    #   - freshness: a stale posting cannot be "high" however strong the fit,
    #     because the shortlist is already formed.
    #   - legitimacy 'caution': the agent found something off about this
    #     posting. A strong fit does not resolve that doubt, and reporting
    #     "high" odds on a role that may not really be open is exactly the
    #     overconfidence this module exists to prevent.
    if score >= 80 and fresh_n >= 0.85 and legit_tier == "legitimate":
        odds: InterviewOdds = "high"
    elif score >= 65:
        odds = "moderate"
    elif score >= 50:
        odds = "low"
    else:
        odds = "negligible"

    return ReadinessVerdict(
        decision=decision,
        interview_odds=odds,
        score=score,
        reasons=reasons,
        blockers=blockers,
        signals=signals,
    )


# ─── DB-backed wrapper ───────────────────────────────────────────────────────
async def assess_job_id(
    *,
    job_id: int,
    user_id: UUID,
    run_legitimacy_if_missing: bool = True,
) -> ReadinessVerdict:
    """Load a job plus its legitimacy/fit/referral context and assess it.

    When the legitimacy agent has not yet run for this job and
    `run_legitimacy_if_missing` is set, it is invoked (costs ~$0.005 for the
    Perplexity news signal). That is far cheaper than a wasted application:
    a tailored resume build runs into dollars.
    """
    from db.client import get_supabase

    db = get_supabase()
    rows = (
        db.table("jobs")
        .select(
            "id, title, company, location, apply_url, description, "
            "posted_at, discovered_at, created_at, posting_closed_at, "
            "validation_failed, fit_score_breakdown, letter_grade, "
            "legitimacy_score, legitimacy_tier"
        )
        .eq("id", job_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        return ReadinessVerdict(
            decision="skip", interview_odds="negligible", score=0,
            blockers=[f"Job {job_id} not found for this user"],
        )
    job = rows[0]

    legitimacy: Optional[dict[str, Any]] = None
    if job.get("legitimacy_score") is not None:
        legitimacy = {
            "score": job["legitimacy_score"],
            "tier": job.get("legitimacy_tier"),
        }
    elif run_legitimacy_if_missing:
        try:
            from agents.legitimacy_agent import score_legitimacy
            result = await score_legitimacy(job=job, user_id=user_id)
            legitimacy = {"score": result.score, "tier": result.tier}
        except Exception as exc:
            logger.warning(
                "apply_readiness: legitimacy check failed for job %s: %r",
                job_id, exc,
            )
            # Leave legitimacy None — the blocker below is deliberate. An
            # unverified posting must not slip through as though it passed.

    referral = _lookup_referral(db, user_id=user_id, company=job.get("company") or "")

    return assess_readiness(
        job=job,
        legitimacy=legitimacy,
        fit_breakdown=job.get("fit_score_breakdown"),
        referral=referral,
    )


def _lookup_referral(
    db: Any, *, user_id: UUID, company: str
) -> Optional[dict[str, Any]]:
    """Shortest known path into `company` from the referral graph.

    Best-effort: a missing table or an empty graph simply means no referral
    bonus, never a failed assessment.
    """
    if not company:
        return None
    try:
        rows = (
            db.table("people")
            .select("full_name, company, hops")
            .eq("user_id", str(user_id))
            .ilike("company", company)
            .order("hops", desc=False)
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception as exc:
        logger.debug("apply_readiness: referral lookup skipped: %r", exc)
        return None
    if not rows:
        return None
    return {
        "hops": rows[0].get("hops") or 2,
        "contact_name": rows[0].get("full_name"),
    }
