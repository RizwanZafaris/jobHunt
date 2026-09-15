"""
agents/hourly_discovery.py — the unattended half of the pipeline.

What runs every hour, with nobody watching
-------------------------------------------
    discover  →  score  →  gate  →  tailor  →  queue

    1. Sweep the boards for new roles (multi_source_discovery).
    2. Score anything new through G5 (6 dimensions).
    3. Run the apply-readiness gate: drop ghost postings, expired listings,
       anything past the age cutoff, anything below the fit floor.
    4. Enqueue a tailored G2 resume for every role that clears the gate.
    5. Leave it in linkedin_apply_queue, resume attached, ready to send.

When the operator sits down, the queue already holds fresh, validated,
tailored applications. Nothing was submitted on their behalf.

Why hourly, and why this is the highest-value automation here
--------------------------------------------------------------
Freshness is weighted highest in the readiness gate because it is the only
input a pipeline can actually move. Fit is fixed by the time a role appears;
legitimacy is a property of the posting. Timing is a choice — and it is
decided entirely by how soon after publication you see the role.

A daily sweep finds a posting somewhere in its first 24 hours. An hourly one
finds it within the hour. That difference lands the application in the pile
a recruiter reads on day one rather than the pile they skim on day three,
and it is worth more than any amount of prompt tuning downstream.

Cost control
------------
The expensive steps are G5 scoring (~$0.15/role) and G2 resume builds
(dollars each). Both are gated:

  - Scoring only touches roles never scored before. Discovery dedupes on
    apply_url, so a re-found posting costs nothing.
  - Resume builds only fire for roles that clear the readiness gate, which
    is a small fraction of what discovery returns.
  - MAX_* ceilings below cap the worst case per run regardless.

Set HOURLY_DISCOVERY_ENABLED=0 to switch the whole thing off without a deploy.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# Per-run ceilings. These bound spend when a sweep returns an unusually large
# batch — a burst of new postings should not turn into an unbounded bill.
MAX_SCORED_PER_RUN = int(os.environ.get("HOURLY_MAX_SCORED", "15"))
MAX_RESUMES_PER_RUN = int(os.environ.get("HOURLY_MAX_RESUMES", "5"))


def _enabled() -> bool:
    return os.environ.get("HOURLY_DISCOVERY_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _default_user_id() -> UUID:
    return UUID(os.environ.get(
        "RIZWAN_USER_ID", "00000000-0000-0000-0000-000000000001",
    ))


@dataclass
class HourlyRunResult:
    started_at: str = ""
    discovered: int = 0
    inserted: int = 0
    scored: int = 0
    passed_gate: int = 0
    resumes_queued: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def summary(self) -> str:
        return (
            f"discovered={self.discovered} inserted={self.inserted} "
            f"scored={self.scored} passed_gate={self.passed_gate} "
            f"resumes_queued={self.resumes_queued} "
            f"cost=${self.cost_usd:.2f}"
        )


async def run_hourly_discovery(
    *,
    user_id: UUID | None = None,
) -> HourlyRunResult:
    """One unattended cycle. Safe to call on a cron; never submits anything."""
    result = HourlyRunResult(started_at=datetime.now(timezone.utc).isoformat())

    if not _enabled():
        logger.info("hourly_discovery: disabled via HOURLY_DISCOVERY_ENABLED")
        return result

    uid = user_id or _default_user_id()

    # ── 1. Discover ─────────────────────────────────────────────────────
    from agents.multi_source_discovery import discover_jobs

    try:
        disc = discover_jobs(user_id=uid)
    except Exception as exc:
        logger.error("hourly_discovery: discovery failed: %r", exc)
        result.errors.append(f"discovery: {type(exc).__name__}: {exc}"[:200])
        return result

    result.discovered = disc.after_filters
    result.inserted = disc.inserted
    result.errors.extend(disc.errors[:5])

    if not disc.inserted:
        logger.info("hourly_discovery: no new roles this cycle")
        return result

    # ── 2. Score the newly inserted roles ───────────────────────────────
    # Only rows with no fit_score_breakdown — anything re-found by a later
    # sweep was already scored and must not be paid for twice.
    from db.client import get_supabase

    db = get_supabase()
    try:
        unscored = (
            db.table("jobs")
            .select("id, title, company, location")
            .eq("user_id", str(uid))
            .is_("fit_score_breakdown", "null")
            .is_("posting_closed_at", "null")
            .order("id", desc=True)
            .limit(MAX_SCORED_PER_RUN)
            .execute()
            .data
        ) or []
    except Exception as exc:
        logger.error("hourly_discovery: unscored lookup failed: %r", exc)
        result.errors.append(f"lookup: {type(exc).__name__}: {exc}"[:200])
        return result

    from agents.scoring_agent import score_role

    scored_ids: list[int] = []
    for row in unscored:
        try:
            breakdown = await score_role(job_id=row["id"], user_id=uid)
            result.scored += 1
            result.cost_usd += float(breakdown.get("cost_usd") or 0.0)
            scored_ids.append(row["id"])
        except Exception as exc:
            logger.warning(
                "hourly_discovery: scoring failed for job %s: %r", row["id"], exc
            )
            result.errors.append(f"score[{row['id']}]: {type(exc).__name__}"[:120])

    if not scored_ids:
        return result

    # ── 3. Readiness gate ───────────────────────────────────────────────
    # This is what keeps ghost postings and 60-day-old listings out of the
    # queue while nobody is watching.
    from agents.apply_readiness import assess_job_id

    ready: list[int] = []
    for job_id in scored_ids:
        try:
            verdict = await assess_job_id(job_id=job_id, user_id=uid)
        except Exception as exc:
            logger.warning(
                "hourly_discovery: readiness check failed for %s: %r", job_id, exc
            )
            continue

        if verdict.should_apply:
            ready.append(job_id)
        else:
            reason = (verdict.blockers[0] if verdict.blockers
                      else f"decision={verdict.decision}")
            key = reason.split("(")[0].split("—")[0].strip()[:60]
            result.skipped_reasons[key] = result.skipped_reasons.get(key, 0) + 1

    result.passed_gate = len(ready)
    if not ready:
        logger.info("hourly_discovery: %s", result.summary())
        return result

    # ── 4. Queue a tailored resume for each ─────────────────────────────
    # Capped separately: resume builds are the most expensive step by an
    # order of magnitude.
    from agents.linkedin_easy_apply import ensure_resume_enqueued, upsert_queue_row

    for job_id in ready[:MAX_RESUMES_PER_RUN]:
        try:
            build_id = ensure_resume_enqueued(job_id=job_id, user_id=uid)
            upsert_queue_row(
                user_id=uid,
                job_id=job_id,
                status="paused" if build_id else "resume_building",
                resume_build_id=build_id,
            )
            result.resumes_queued += 1
        except Exception as exc:
            logger.warning(
                "hourly_discovery: resume enqueue failed for %s: %r", job_id, exc
            )
            result.errors.append(f"resume[{job_id}]: {type(exc).__name__}"[:120])

    logger.info("hourly_discovery: %s", result.summary())
    return result
