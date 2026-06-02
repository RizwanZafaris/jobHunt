"""
api/journeys.py — FRD-16 read + admin surface for high-fit auto-prep journeys.

Read endpoints are tenant-scoped (get_current_user → filter by user_id). The
backfill is admin-only: a one-shot to prep existing open >=90 jobs that
predate the auto-trigger. The journey worker re-checks guardrails + dedup per
job, so the backfill is safe to call repeatedly.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from api.context import get_current_user, require_admin
from api.users import User
from api.journey import get_journey, list_journeys

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/journeys", tags=["journeys"])


@router.get("")
def list_journeys_endpoint(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List the tenant's recent high-fit journeys (the /today high-fit feed).

    Sync `def` (not async): the journey reads use the sync supabase client, so
    FastAPI runs this in its threadpool — no event-loop blocking, and it stays
    clear of the no-blocking-execute-in-async guardrail.
    """
    return {"journeys": list_journeys(user_id=str(user.id))}


@router.get("/{job_id}")
def get_journey_endpoint(
    job_id: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """One journey + its three leg statuses (resume / prep / network)."""
    j = get_journey(user_id=str(user.id), job_id=job_id)
    if j is None:
        return {"job_id": job_id, "exists": False}
    return {"exists": True, **j}


@router.post("/backfill")
def backfill_journeys(_admin: User = Depends(require_admin)) -> dict[str, Any]:
    """One-shot: enqueue journeys for existing OPEN jobs scoring >= the
    threshold that don't already have one. Bounded by journey_daily_cap so a
    backfill can't fan out unbounded. Idempotent — the journey worker
    re-checks guardrails + dedup per job, so repeat calls are safe."""
    from db.client import get_supabase
    from config.settings import get_settings
    from api.queue import enqueue_journey

    s = get_settings()
    min_score = int(getattr(s, "journey_min_score", 90))
    cap = int(getattr(s, "journey_daily_cap", 8))
    db = get_supabase()

    rows = (
        db.table("jobs")
        .select("id, user_id, company, match_score, posting_closed_at, validation_failed")
        .gte("match_score", min_score)
        .is_("posting_closed_at", "null")
        .order("match_score", desc=True)
        .limit(200)
        .execute()
    ).data or []

    enqueued: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for r in rows:
        job_id = r["id"]
        if r.get("validation_failed"):
            skipped.append({"job_id": job_id, "reason": "validation_failed"})
            continue
        existing = (
            db.table("journeys").select("id")
            .eq("user_id", r["user_id"]).eq("job_id", job_id)
            .limit(1).execute()
        ).data or []
        if existing:
            skipped.append({"job_id": job_id, "reason": "already_journeyed"})
            continue
        if len(enqueued) >= cap:
            skipped.append({"job_id": job_id, "reason": "backfill_cap"})
            continue
        try:
            run_id = enqueue_journey(r["user_id"], job_id)
            enqueued.append({"job_id": job_id, "run_id": run_id})
        except Exception as exc:
            skipped.append(
                {"job_id": job_id, "reason": f"enqueue_failed:{type(exc).__name__}"}
            )

    logger.info(
        "FRD-16 backfill: enqueued=%d skipped=%d (min_score=%d cap=%d)",
        len(enqueued), len(skipped), min_score, cap,
    )
    return {
        "enqueued": enqueued,
        "skipped": skipped,
        "min_score": min_score,
        "cap": cap,
    }


__all__ = ["router"]
