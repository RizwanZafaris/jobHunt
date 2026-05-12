"""
api/_job_guards.py — single source of truth for "is this job spend-worthy?"

Why this exists
---------------
The 2026-05-12 bug-hunt sprint surfaced two production incidents tied to the
same archetype:

  1. OKX job 1641 "Senior Product Manager, Payment" — a 1-year-old LinkedIn
     listing scored 95/100 on /today and the user paid $1.07 to build a G2
     resume against it (resume_build 936560e1, cost confirmed via SQL audit).
     The posting was unreachable for application anyway → pure financial
     waste.

  2. Agent B (code audit) found 13 callsites across api/server.py,
     api/workspace.py, resume_agents/g2_io.py, agents/boss_agent.py, and
     agents/profile_analyzer.py that look up `jobs` by id without filtering
     on `posting_closed_at` or `validation_failed`. Two of those are LLM-
     spending entry points (`POST /jobs/{id}/generate-resume`, ~$5 per call;
     `POST /jobs/{id}/prep-interview`, ~$0.50 per call) — direct shortcuts
     around the workspace.py wallet guard that shipped on the freshness PR.

Design
------
ONE function: `load_open_job`. Every code path that loads a single job for
the purpose of spending LLM tokens calls it. It:

  - Fetches the row by id (and optionally scoped to a user_id).
  - 404s if the row doesn't exist or doesn't belong to the user.
  - 409s with a structured error body if the job is closed/failed AND `force`
    is not set. The 409 body carries `code`, a human-readable `message`, the
    `posting_closed_at` / `validation_failed` values, and a hint about the
    `cost_label` so the dashboard can show the right confirmation copy
    ("Spend $5 building a G2 resume against a closed posting?").
  - Otherwise returns the full job dict.

Non-route callers (resume_agents/g2_io.py::load_job, boss_agent digest
queries) use `load_open_job_or_none`, which returns `None` on stale rather
than raising — those callers are batch / background workers and should skip
silently, not crash a whole digest run.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _stale_detail(job: dict[str, Any], cost_label: str) -> dict[str, Any]:
    """Build the structured 409 body for a stale job."""
    closed_at = job.get("posting_closed_at")
    reason = job.get("validation_failed") or "stale"
    if closed_at:
        message = (
            f"This job posting is marked closed ({reason}). "
            f"Running {cost_label} costs LLM tokens but you cannot apply to "
            f"a closed posting anyway. Pass force=true to override."
        )
        code = "posting_closed"
    else:
        message = (
            f"This job failed validation ({reason}). "
            f"Running {cost_label} is likely wasted spend. "
            f"Pass force=true to override."
        )
        code = "validation_failed"
    return {
        "code": code,
        "message": message,
        "cost_label": cost_label,
        "posting_closed_at": closed_at,
        "validation_failed": job.get("validation_failed"),
        "validation_status": job.get("validation_status"),
        "job_id": job.get("id"),
        "company": job.get("company"),
        "title": job.get("title"),
    }


def _is_stale(job: dict[str, Any]) -> bool:
    """True if the job is marked closed or failed validation."""
    return (
        job.get("posting_closed_at") is not None
        or bool(job.get("validation_failed"))
    )


def load_open_job(
    db,
    *,
    job_id: int,
    user_id: Optional[UUID] = None,
    force: bool = False,
    cost_label: str = "this operation",
) -> dict[str, Any]:
    """Load a job row with staleness + tenancy guards.

    Args:
        db: Supabase client (from `db.client.get_supabase()`).
        job_id: jobs.id (integer).
        user_id: if provided, scope the lookup to this user (multi-tenant
                guard). Pass None on admin-style routes that authenticate
                with `verify_secret` and aren't scoped to a single user.
        force: bypass the staleness guard. Surfaces the row regardless of
              posting_closed_at / validation_failed.
        cost_label: short human-readable label that gets included in the
                  409 error body. Examples: "G2 resume build",
                  "G3 interview prep", "rescore"/"reclassify".

    Returns:
        The full job row as a dict.

    Raises:
        HTTPException 404 — row not found OR not owned by user_id.
        HTTPException 409 — row is stale (closed or validation_failed) AND
                            force is False. The detail body has a
                            structured code, message, and the staleness
                            fields so the dashboard can decide how to
                            present the confirmation dialog.
    """
    q = db.table("jobs").select("*").eq("id", job_id)
    if user_id is not None:
        q = q.eq("user_id", str(user_id))
    rows = q.limit(1).execute().data or []
    if not rows:
        raise HTTPException(status_code=404, detail="job_not_found")
    job = rows[0]
    if not force and _is_stale(job):
        raise HTTPException(status_code=409, detail=_stale_detail(job, cost_label))
    return job


def load_open_job_or_none(
    db,
    *,
    job_id: int,
    user_id: Optional[UUID] = None,
    force: bool = False,
) -> Optional[dict[str, Any]]:
    """Non-raising variant for batch / background workers.

    Returns the job dict if found and (open or force=True), else None.

    Use this in:
      - resume_agents/g2_io.py::load_job (G2 graph entry point — should
        skip stale jobs, not crash the worker queue)
      - agents/boss_agent.py digest queries (build a clean digest, skip
        anything that's closed)
      - Any cron / scheduled task that iterates over many jobs.
    """
    q = db.table("jobs").select("*").eq("id", job_id)
    if user_id is not None:
        q = q.eq("user_id", str(user_id))
    rows = q.limit(1).execute().data or []
    if not rows:
        return None
    job = rows[0]
    if not force and _is_stale(job):
        logger.info(
            "load_open_job_or_none: skipping stale job_id=%s company=%r "
            "closed_at=%s validation_failed=%s",
            job_id,
            job.get("company"),
            job.get("posting_closed_at"),
            job.get("validation_failed"),
        )
        return None
    return job


def filter_open_jobs_query(query):
    """Add `posting_closed_at IS NULL AND validation_failed IS NULL` filters.

    Chainable shim for any Supabase PostgREST query builder. Use on every
    LIST endpoint that returns jobs to a user (`GET /jobs`, /today digest,
    boss agent top-jobs query, etc.). Ranking by match_score with stale
    rows mixed in is the OKX-1641 archetype on the list surface.

    Example:
        query = db.table("jobs").select(...).gte("match_score", 80)
        query = filter_open_jobs_query(query).order(...).limit(...)
    """
    return query.is_("posting_closed_at", "null").is_("validation_failed", "null")
