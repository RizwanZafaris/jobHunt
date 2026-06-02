"""
api/journey.py — FRD-16 High-Fit Auto-Prep Journey orchestrator.

When a job's G5 composite score crosses `JOURNEY_MIN_SCORE` (default 90), the
scoring path calls `maybe_trigger_journey`, which enqueues a `journey` run.
The worker (`api.worker.worker_run_journey`) then calls
`create_journey_for_job`, the saga that:

  1. re-checks guardrails (job open? journey not already done? under daily cap?)
  2. creates a draft `applications` row (status='resume_ready', auto_created)
  3. fans out the three prep legs as independent durable jobs:
        - G2 resume         (enqueue_g2_build)
        - G3 interview prep (enqueue_g3_interview_prep, on the draft application)
        - network sweep     (enqueue_people_finder for the company)
  4. records the three child run ids on the `journeys` row.

Dedup is enforced two ways: the `journeys(user_id, job_id)` UNIQUE index (DB
layer) and the queue's payload idempotency (jobs_runs layer). A re-score can
never double-prep a job.

All DB calls here are synchronous (supabase-py sync client), matching the
worker's other handlers. The module is import-cycle-safe: it imports
`api.queue` (enqueue_*) lazily inside functions.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ─── Config (mirrors config.settings; read lazily so tests can monkeypatch) ──
def _cfg() -> tuple[bool, int, int]:
    """Return (enabled, min_score, daily_cap) from settings, with safe
    defaults if settings can't load (never block scoring on a config read)."""
    try:
        from config.settings import get_settings
        s = get_settings()
        return (
            bool(getattr(s, "journey_enabled", True)),
            int(getattr(s, "journey_min_score", 90)),
            int(getattr(s, "journey_daily_cap", 8)),
        )
    except Exception:  # pragma: no cover - defensive
        return (True, 90, 8)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Trigger (called by the G5 scoring path) ────────────────────────────────
def maybe_trigger_journey(
    *,
    user_id: UUID | str,
    job_id: int,
    composite: Optional[int],
) -> Optional[str]:
    """Fire-and-forget journey trigger. FAIL-OPEN: any error is logged and
    swallowed so scoring is never broken by the journey path.

    Returns the enqueued journey run id, or None if not triggered.

    The job is already known-open here (the only caller, `score_role`, reaches
    its persist step only via `load_open_job`). Dedup + the heavy guardrail
    re-check live in `create_journey_for_job`, so this stays minimal.
    """
    enabled, min_score, _cap = _cfg()
    if not enabled:
        return None
    if composite is None or int(composite) < min_score:
        return None
    try:
        from api.queue import enqueue_journey
        run_id = enqueue_journey(user_id, int(job_id))
        logger.info(
            "FRD-16: journey triggered job_id=%s composite=%s run_id=%s",
            job_id, composite, run_id,
        )
        return run_id
    except Exception as exc:
        logger.warning(
            "FRD-16: journey trigger failed (non-fatal) job_id=%s: %r",
            job_id, exc,
        )
        return None


# ─── Orchestrator (called by worker_run_journey) ────────────────────────────
def create_journey_for_job(*, user_id: str, job_id: int) -> dict[str, Any]:
    """Saga body: guardrails → draft application → fan out 3 legs → record.

    Returns a result dict (stored on the journey jobs_runs row). Idempotent:
    if a journeys row already exists for (user, job) it is returned untouched.
    """
    from db.client import get_supabase
    db = get_supabase()
    uid = str(user_id)

    # 1. Load the job (tenant-scoped) + guardrails.
    job_rows = (
        db.table("jobs")
        .select("id, company, title, company_id, posting_closed_at, "
                "validation_failed, fit_score_breakdown, match_score")
        .eq("id", job_id)
        .eq("user_id", uid)
        .limit(1)
        .execute()
    ).data or []
    if not job_rows:
        return {"skipped": True, "reason": "job_not_found", "job_id": job_id}
    job = job_rows[0]
    if job.get("posting_closed_at") is not None or job.get("validation_failed"):
        return {"skipped": True, "reason": "posting_closed_or_invalid",
                "job_id": job_id}

    # 2. Dedup at the DB layer — the unique (user_id, job_id) index means a
    #    re-score can't double-prep. If a journey already exists, no-op.
    existing = (
        db.table("journeys").select("*").eq("user_id", uid).eq("job_id", job_id)
        .limit(1).execute()
    ).data or []
    if existing:
        return {"skipped": True, "reason": "already_journeyed",
                "journey_id": existing[0]["id"], "job_id": job_id}

    # 3. Daily cap — bound autonomous spend if a big scout batch crosses >=90.
    _enabled, _min, cap = _cfg()
    if cap > 0:
        today = datetime.now(timezone.utc).date().isoformat()
        cnt = (
            db.table("journeys").select("id", count="exact", head=True)
            .eq("user_id", uid).gte("created_at", today).execute()
        )
        used = cnt.count or 0
        if used >= cap:
            logger.warning(
                "FRD-16: daily journey cap hit (%s/%s) — deferring job_id=%s",
                used, cap, job_id,
            )
            return {"skipped": True, "reason": "daily_cap", "used": used,
                    "cap": cap, "job_id": job_id}

    composite = None
    bd = job.get("fit_score_breakdown")
    if isinstance(bd, dict):
        composite = bd.get("composite")
    if composite is None:
        composite = job.get("match_score")

    company = job.get("company") or ""
    company_id = job.get("company_id")

    # 4. Draft application (reuse if one already exists for this job).
    application_id = _ensure_draft_application(
        db, user_id=uid, job=job, composite=composite,
    )

    # 5. Insert the journeys row first (the unique index guards against a race;
    #    if a concurrent run already inserted, we treat it as already-journeyed).
    try:
        jrow = (
            db.table("journeys").insert({
                "user_id": uid,
                "job_id": job_id,
                "application_id": application_id,
                "trigger_score": int(composite) if composite is not None else None,
                "status": "running",
            }).execute()
        ).data or []
    except Exception as exc:
        # Most likely the unique index tripped on a concurrent insert.
        logger.info("FRD-16: journeys insert raced/failed job_id=%s: %r",
                    job_id, exc)
        return {"skipped": True, "reason": "already_journeyed_race",
                "job_id": job_id}
    if not jrow:
        return {"skipped": True, "reason": "journey_insert_no_row",
                "job_id": job_id}
    journey_id = jrow[0]["id"]

    # 6. Fan out the three legs. Each is independent — one failing to enqueue
    #    must not block the others (the journey is partial, not dead).
    from api.queue import (
        enqueue_g2_build, enqueue_g3_interview_prep, enqueue_people_finder,
    )
    legs: dict[str, Optional[str]] = {
        "resume_run_id": None, "prep_run_id": None, "network_run_id": None,
    }
    leg_errors: dict[str, str] = {}

    try:
        legs["resume_run_id"] = enqueue_g2_build(user_id=uid, job_id=job_id)
    except Exception as exc:
        leg_errors["resume"] = f"{type(exc).__name__}: {exc}"
        logger.warning("FRD-16: resume leg enqueue failed job_id=%s: %r", job_id, exc)

    if application_id:
        try:
            legs["prep_run_id"] = enqueue_g3_interview_prep(
                user_id=uid, application_id=application_id,
            )
        except Exception as exc:
            leg_errors["prep"] = f"{type(exc).__name__}: {exc}"
            logger.warning("FRD-16: prep leg enqueue failed job_id=%s: %r", job_id, exc)
    else:
        leg_errors["prep"] = "no_application_id"

    if company:
        try:
            legs["network_run_id"] = enqueue_people_finder(
                user_id=uid, company_name=company, company_id=company_id,
            )
        except Exception as exc:
            leg_errors["network"] = f"{type(exc).__name__}: {exc}"
            logger.warning("FRD-16: network leg enqueue failed job_id=%s: %r", job_id, exc)
    else:
        leg_errors["network"] = "no_company"

    # 7. Record the child run ids + status on the journeys row.
    status = "running" if any(legs.values()) else "failed"
    update: dict[str, Any] = {**legs, "status": status}
    if leg_errors:
        update["note"] = "; ".join(f"{k}:{v}" for k, v in leg_errors.items())[:500]
    try:
        db.table("journeys").update(update).eq("id", journey_id).execute()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("FRD-16: journeys update failed id=%s: %r", journey_id, exc)

    logger.info(
        "FRD-16: journey %s created job_id=%s app=%s legs=%s errors=%s",
        journey_id, job_id, application_id, legs, leg_errors,
    )
    return {
        "journey_id": journey_id, "job_id": job_id,
        "application_id": application_id, "trigger_score": composite,
        **legs, "leg_errors": leg_errors, "status": status,
    }


def _ensure_draft_application(
    db, *, user_id: str, job: dict[str, Any], composite: Optional[int],
) -> Optional[str]:
    """Return an application id for this job — reusing an existing one, or
    creating a draft (status='resume_ready', auto_created=true). The
    `resume_ready` pre-apply status carries NULL applied_date legally
    (migration 2026_05_12_014), so no constraint violation."""
    job_id = job["id"]
    try:
        existing = (
            db.table("applications").select("id")
            .eq("user_id", user_id).eq("job_id", job_id)
            .limit(1).execute()
        ).data or []
        if existing:
            return existing[0]["id"]
    except Exception as exc:
        logger.warning("FRD-16: application lookup failed job_id=%s: %r", job_id, exc)

    row: dict[str, Any] = {
        "user_id": user_id,
        "job_id": job_id,
        "company": job.get("company") or "",
        "role": job.get("title") or "",
        "status": "resume_ready",  # pre-apply; applied_date stays NULL
        "auto_created": True,
        "company_id": job.get("company_id"),
    }
    if composite is not None:
        row["score"] = round(float(composite) / 20.0, 2)  # 0-100 → 0-5
    try:
        res = db.table("applications").insert(row).execute()
        created = (res.data or [None])[0]
        return created["id"] if created else None
    except Exception as exc:
        logger.warning(
            "FRD-16: draft application insert failed job_id=%s: %r", job_id, exc
        )
        return None


# ─── Read side (API) ────────────────────────────────────────────────────────
def _leg_status(run_id: Optional[str]) -> dict[str, Any]:
    if not run_id:
        return {"run_id": None, "status": "not_started"}
    try:
        from api.jobs_runs import get_run
        run = get_run(run_id)
        if not run:
            return {"run_id": run_id, "status": "unknown"}
        return {"run_id": run_id, "status": run.status,
                "last_error": run.last_error}
    except Exception as exc:  # pragma: no cover - defensive
        return {"run_id": run_id, "status": "unknown", "error": str(exc)}


def _aggregate(legs: list[dict[str, Any]]) -> str:
    """Roll the three leg statuses into one journey status."""
    statuses = [leg["status"] for leg in legs if leg["run_id"]]
    if not statuses:
        return "failed"
    terminal_ok = {"succeeded"}
    terminal_bad = {"failed", "cancelled"}
    if all(s in terminal_ok for s in statuses):
        return "converged"
    if all(s in (terminal_ok | terminal_bad) for s in statuses):
        return "partial" if any(s in terminal_ok for s in statuses) else "failed"
    return "running"


def get_journey(*, user_id: str, job_id: int) -> Optional[dict[str, Any]]:
    """Aggregate one journey + its three leg statuses for the dashboard."""
    from db.client import get_supabase
    db = get_supabase()
    rows = (
        db.table("journeys").select("*")
        .eq("user_id", str(user_id)).eq("job_id", job_id)
        .limit(1).execute()
    ).data or []
    if not rows:
        return None
    j = rows[0]
    legs = {
        "resume": _leg_status(j.get("resume_run_id")),
        "prep": _leg_status(j.get("prep_run_id")),
        "network": _leg_status(j.get("network_run_id")),
    }
    jb = _job_brief(db, user_id=str(user_id), job_ids=[j["job_id"]]).get(j["job_id"], {})
    return {
        "journey_id": j["id"],
        "job_id": j["job_id"],
        "company": jb.get("company"),
        "title": jb.get("title"),
        "application_id": j.get("application_id"),
        "trigger_score": j.get("trigger_score"),
        "status": _aggregate(list(legs.values())),
        "legs": legs,
        "created_at": j.get("created_at"),
        "note": j.get("note"),
    }


def _job_brief(db, *, user_id: str, job_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Batch-load {job_id: {company, title}} for a tenant's jobs. Tenant-scoped
    (the .eq('user_id') keeps the tenant-scoping guardrail happy)."""
    if not job_ids:
        return {}
    try:
        rows = (
            db.table("jobs").select("id, company, title")
            .in_("id", job_ids).eq("user_id", user_id).execute()
        ).data or []
        return {r["id"]: r for r in rows}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("FRD-16: job_brief lookup failed: %r", exc)
        return {}


def list_journeys(*, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Recent journeys for a tenant, newest first (the /today high-fit feed)."""
    from db.client import get_supabase
    db = get_supabase()
    rows = (
        db.table("journeys").select("*")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(min(max(limit, 1), 200))
        .execute()
    ).data or []
    briefs = _job_brief(db, user_id=str(user_id), job_ids=[j["job_id"] for j in rows])
    out = []
    for j in rows:
        legs = {
            "resume": _leg_status(j.get("resume_run_id")),
            "prep": _leg_status(j.get("prep_run_id")),
            "network": _leg_status(j.get("network_run_id")),
        }
        jb = briefs.get(j["job_id"], {})
        out.append({
            "journey_id": j["id"], "job_id": j["job_id"],
            "company": jb.get("company"), "title": jb.get("title"),
            "application_id": j.get("application_id"),
            "trigger_score": j.get("trigger_score"),
            "status": _aggregate(list(legs.values())),
            "legs": legs, "created_at": j.get("created_at"),
        })
    return out
