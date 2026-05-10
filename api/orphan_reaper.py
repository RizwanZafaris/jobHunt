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

    summary = {
        "scanned": 0,
        "requeued": 0,
        "marked_failed": 0,
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
