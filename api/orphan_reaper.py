"""
api/orphan_reaper.py — periodic sweeper for stale `jobs_runs` rows.

When Railway redeploys the worker container, any RQ job that was
mid-execution dies. RQ's own crash-recovery handles the queue side
(unfinished jobs go to its `started` registry → eventually moved to
`failed` registry by the next worker's startup), but the `jobs_runs`
row stays at status='running' with a stale started_at forever.

This module finds those rows and either:
  - re-queues them (if attempts < MAX_ATTEMPTS), bumping the attempt
    counter via the worker's mark_running on next pickup; OR
  - marks them failed (last_error='orphaned by redeploy') if we've
    already burned through the retry budget.

Operation modes:

  1. Background scheduler (via APScheduler — already in requirements.txt).
     Runs every 5 minutes. Boot from main.py's scheduler service or
     spawn alongside the worker.

  2. CLI one-shot for cron-on-Railway:
         python -m api.orphan_reaper
     Runs reap_orphans() once, prints a summary, exits 0. Pair with
     Railway's cron-job feature or a scheduled-tasks MCP entry.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

# How long before a 'running' row is considered orphaned. Worst-case
# legitimate run is ~5 min (G2). 15 min gives plenty of slack — anything
# longer is almost certainly dead.
DEFAULT_STALE_MINUTES = 15

# Mirror api/worker.py's MAX_ATTEMPTS — once we've burned through these,
# stop requeuing and mark terminal.
MAX_ATTEMPTS = 3


def _sweep_stuck_resume_builds(stale_minutes: int = 30) -> int:
    """HARDEN-BUG-402: Flip resume_builds stuck in 'running' >30 min to 'failed'.

    The orphan_reaper handles jobs_runs; this handles resume_builds which
    can also get stuck when the worker dies mid-graph.  Called from
    reap_orphans() so both tables are swept in the same tick.

    Returns count of rows swept.
    """
    try:
        from db.client import get_supabase
        from datetime import datetime, timezone, timedelta

        db = get_supabase()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()

        stuck = db.table("resume_builds").select("id").eq("status", "running").lt("updated_at", cutoff).execute()
        ids = [r["id"] for r in (stuck.data or [])]
        if not ids:
            return 0

        # B6: Column is `error`, NOT `error_message`. PostgREST silently
        # drops unknown fields — without this fix the sweep marks rows as
        # 'failed' but never writes a reason (same bug class as the 15
        # mass-failed builds with error=NULL from 2026-05-11).
        db.table("resume_builds").update({
            "status": "failed",
            "error": "B6: stuck-build sweep (30min timeout)",
        }).in_("id", ids).execute()

        logger.warning(
            "HARDEN-BUG-402: swept %d stuck resume_builds (running >%dmin)",
            len(ids), stale_minutes,
        )
        return len(ids)
    except Exception:
        logger.exception("HARDEN-BUG-402: resume_builds sweep failed")
        return 0


# B1: kind → worker_func map. Mirror of the registrations in api/queue.py.
# When we re-enqueue a stuck queued row we have to dispatch to the right
# worker function — there is no single `worker_dispatch` entrypoint.
_KIND_TO_WORKER = {
    "g1_discovery": "api.worker.worker_run_g1",
    "g2_resume": "api.worker.worker_run_g2",
    "g3_interview_prep": "api.worker.worker_run_g3",
    "g4_linkedin_post": "api.worker.worker_run_g4",
    "g5_score": "api.worker.worker_run_g5",
    "g9_story_extract": "api.worker.worker_run_g9",
    "g11_voice_calibration": "api.worker.worker_run_g11",
    "legitimacy_check": "api.worker.worker_run_legitimacy",
}


def _sweep_stuck_queued(db, stale_minutes: int = 60) -> int:
    """B1: Re-enqueue jobs_runs rows stuck in 'queued' for too long.

    When the worker dies after a row is inserted but before RQ picks it up,
    the row sits forever. We detect these (queued + no started_at + old)
    and re-push them to the RQ queue (if attempts < 3) using the same
    worker_func that `api/queue.py::_enqueue_or_dedup` would have picked.
    """
    try:
        from datetime import datetime, timezone, timedelta
        from api.queue import _get_queue

        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()

        stuck = (db.table("jobs_runs")
            .select("id, kind, attempts")
            .eq("status", "queued")
            .is_("started_at", "null")
            .lt("created_at", cutoff)
            .lt("attempts", 3)
            .execute())

        rows = stuck.data or []
        if not rows:
            return 0

        q = _get_queue()
        requeued = 0
        for row in rows:
            worker_func = _KIND_TO_WORKER.get(row.get("kind"))
            if not worker_func:
                logger.error("B1: unknown kind=%s for run %s — skipping", row.get("kind"), row["id"])
                continue
            try:
                q.enqueue(worker_func, row["id"])
                db.table("jobs_runs").update({
                    "attempts": (row.get("attempts") or 0) + 1,
                }).eq("id", row["id"]).execute()
                requeued += 1
            except Exception as e:
                logger.error("B1: failed to re-enqueue queued jobs_run %s: %s", row["id"], e)

        if requeued:
            logger.warning("B1: re-enqueued %d stuck queued jobs_runs (>%dmin)", requeued, stale_minutes)
        return requeued
    except Exception:
        logger.exception("B1: queued sweep failed")
        return 0


def reap_orphans(stale_minutes: int = DEFAULT_STALE_MINUTES) -> dict[str, Any]:
    """Find stale `running` rows and either requeue or mark failed.

    Returns a summary dict for logging / cron-job output:
        {
            "scanned": 7,
            "requeued": 5,
            "marked_failed": 2,
            "errors": [],
        }

    Never raises — best-effort. Errors per-row are caught and logged so
    one broken row doesn't stop the sweep.
    """
    from api.jobs_runs import find_orphans, mark_failed
    from db.client import get_supabase

    # SCALE-BUG fix (2026-05-29 audit): `db` was passed to _sweep_stuck_queued()
    # below but never bound in this scope, so every reaper tick raised
    # NameError *before* find_orphans() ran — silently disabling the ONLY
    # recovery path for stuck/orphaned jobs (the _scheduled_reap wrapper
    # swallowed it as "tick crashed"). Bind the client here.
    db = get_supabase()

    # HARDEN-BUG-402: sweep stuck resume_builds in the same tick
    swept_resume_builds = _sweep_stuck_resume_builds(stale_minutes=30)

    # B1: Also sweep jobs_runs rows stuck in 'queued' for >60 min.
    # These happen when the worker dies after enqueue but before pick-up.
    # We re-enqueue them (if attempts < 3) so the new worker picks them up.
    _sweep_stuck_queued(db, stale_minutes=60)

    summary = {
        "scanned": 0,
        "requeued": 0,
        "marked_failed": 0,
        "swept_resume_builds": swept_resume_builds,
        "errors": [],
    }

    try:
        orphans = find_orphans(stale_minutes=stale_minutes)
    except Exception as e:
        logger.exception("reap_orphans: find_orphans failed")
        summary["errors"].append(f"find_orphans: {type(e).__name__}: {e}")
        return summary

    summary["scanned"] = len(orphans)
    if not orphans:
        logger.info(f"reap_orphans: no stale rows (cutoff={stale_minutes}min)")
        return summary

    logger.warning(
        f"reap_orphans: found {len(orphans)} stale running rows "
        f"(cutoff={stale_minutes}min) — sweeping"
    )

    for run in orphans:
        try:
            attempts = run.attempts or 0
            if attempts >= MAX_ATTEMPTS:
                # Out of retry budget — terminal failure.
                mark_failed(
                    run.id,
                    f"orphaned by redeploy (attempts={attempts}/{MAX_ATTEMPTS}); "
                    f"giving up. started_at={run.started_at}",
                    retry=False,
                )
                summary["marked_failed"] += 1
                logger.warning(
                    f"reap_orphans: {run.id} ({run.kind}) → failed "
                    f"(attempts exhausted)"
                )
                continue

            # Still have retry budget — requeue. We mark_failed with
            # retry=True (which sets status back to 'queued' but
            # records the orphan reason) and then push a new RQ job
            # onto the queue pointing at the same run id.
            mark_failed(
                run.id,
                f"orphaned by redeploy (attempt {attempts}/{MAX_ATTEMPTS}); "
                f"requeued by orphan_reaper. started_at={run.started_at}",
                retry=True,
            )
            _requeue(run.kind, run.id)
            summary["requeued"] += 1
            logger.info(
                f"reap_orphans: {run.id} ({run.kind}) → requeued "
                f"(attempt {attempts + 1}/{MAX_ATTEMPTS})"
            )
        except Exception as e:
            logger.exception(f"reap_orphans: row {run.id} failed to sweep")
            summary["errors"].append(
                f"{run.id}: {type(e).__name__}: {str(e)[:200]}"
            )

    return summary


def _requeue(kind: str, run_id: str) -> None:
    """Push a fresh RQ job pointing at an existing jobs_runs row.

    Maps kind → worker function. Uses a long-ish delay (60s) for the
    first orphan retry to avoid hammering the worker if it's still
    coming up from a deploy.
    """
    from api.queue import _get_queue

    func_by_kind = {
        "g2_resume": "api.worker.worker_run_g2",
        "g1_discovery": "api.worker.worker_run_g1",
        "g3_interview": "api.worker.worker_run_g3",
        # g4_linkedin_post: not yet implemented; orphans of unknown
        # kinds are skipped with an error.
    }
    worker_func = func_by_kind.get(kind)
    if not worker_func:
        raise ValueError(
            f"requeue: no worker function mapped for kind={kind!r}"
        )

    q = _get_queue()
    # We `enqueue` immediately (no enqueue_in delay) — orphan rows are
    # already ≥15 min stale, so there's no benefit to deferring further.
    # If we ever need a backoff before requeue, switch to:
    #     q.enqueue_in(timedelta(seconds=60), worker_func, run_id, ...)
    q.enqueue(
        worker_func,
        run_id,
        job_timeout=900,
        result_ttl=86400,
        failure_ttl=604800,
        description=f"REQUEUE {kind} run_id={run_id}",
        meta={"run_id": run_id, "kind": kind, "requeue": True},
    )


# ─── Background scheduler integration ──────────────────────────────────────
def start_scheduler(interval_minutes: int = 5):
    """Start an APScheduler BackgroundScheduler that calls reap_orphans
    every `interval_minutes`. Returns the scheduler so the caller can
    keep a reference / shutdown gracefully.

    Used from main.py's scheduler service. Fails open if APScheduler
    isn't installed (you'd see the warning at boot).
    """
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning(
            "orphan_reaper: APScheduler not available — falling back to "
            "manual cron. Wire up `python -m api.orphan_reaper` on a "
            "Railway cron job."
        )
        return None

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _scheduled_reap,
        "interval",
        minutes=interval_minutes,
        id="orphan_reaper",
        replace_existing=True,
        max_instances=1,  # never overlap
    )
    scheduler.start()
    logger.info(
        f"orphan_reaper: scheduler started (interval={interval_minutes}min)"
    )
    return scheduler


def _scheduled_reap():
    """APScheduler-friendly wrapper. Catches all errors so a failure in
    one tick doesn't kill the scheduler thread."""
    try:
        summary = reap_orphans()
        logger.info(f"orphan_reaper tick: {summary}")
    except Exception:
        logger.exception("orphan_reaper tick crashed")


# ─── CLI entrypoint ────────────────────────────────────────────────────────
if __name__ == "__main__":
    """One-shot CLI for cron-on-Railway.

    Usage:
        python -m api.orphan_reaper
        python -m api.orphan_reaper --stale-minutes 30

    Exit code 0 on success (even if errors per-row occurred — they're in
    the summary). Exit code 1 on hard failure (e.g. Supabase unreachable).
    """
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    stale_minutes = DEFAULT_STALE_MINUTES
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--stale-minutes" and i + 1 < len(args):
            stale_minutes = int(args[i + 1])
            i += 2
        else:
            print(f"unknown arg: {args[i]}", file=sys.stderr)
            sys.exit(2)

    started = time.perf_counter()
    summary = reap_orphans(stale_minutes=stale_minutes)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    summary["elapsed_ms"] = elapsed_ms
    print(json.dumps(summary, default=str))
    # Exit 0 even if errors[] is non-empty — partial success is still
    # success for cron purposes; the dashboards/logs surface details.
    sys.exit(0)
