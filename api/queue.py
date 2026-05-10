"""
api/queue.py — Redis + RQ wrapper for jobHunt's long-running graph jobs.

Pre-Phase-3 every long-running graph (G2 resume build, G1 deep research,
G3 interview prep) ran via FastAPI's `BackgroundTasks`. That kept the
API simple but had a fatal flaw: a Railway redeploy mid-build dropped
all in-flight tasks on the floor. No reaper, no retry, no audit trail.

This module replaces that with a durable queue:

    enqueue_g2_build()
        ├── 1. compute idempotency_key = sha256(user|kind|payload)
        ├── 2. if a queued/running row already has this key → return its id
        ├── 3. create a `jobs_runs` row (status='queued', attempts=0)
        └── 4. push a job onto the RQ queue that will call worker.worker_run_g2(jobs_run_id)

The actual graph code lives in `resume_agents/g2_run.run_g2_graph` etc.
The worker just dispatches.

Why RQ (and not Celery)?
  - Single dependency (redis + rq), trivial to set up locally.
  - Simple retry semantics that compose with our jobs_runs row.
  - We can swap to Celery later without changing the call sites: the
    `enqueue_*` API is the abstraction.

ENV vars:
  - REDIS_URL           connection string (default redis://localhost:6379/0)
  - RQ_QUEUE_NAME       queue name (default 'jobhunt')
  - WORKER_CONCURRENCY  read by api/worker.py, NOT here

Job retry policy:
  - timeout=900s        (15 min — G2 worst-case is ~5 min, G1 ~3 min)
  - result_ttl=86400    (1 day — surface results to UI for a day)
  - failure_ttl=604800  (7 days — keep failure detail around for triage)
  - max_retries=3       handled by api/worker.py via Retry(max=3, intervals=[60, 240, 960])

Idempotency rule:
  Two enqueues of the same (user_id, kind, payload) within the same
  queued/running window collapse to one job. Use `force=true` in the
  payload to bypass; that changes the hash.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


# ─── Redis + RQ singletons ─────────────────────────────────────────────────
_redis = None
_queue = None


def _get_redis():
    """Singleton Redis connection.

    Lazy-imports `redis` so this module can be imported in environments
    where redis isn't installed yet (e.g. CI lint passes during the
    rollout window before requirements.txt picks up the new dep).
    """
    global _redis
    if _redis is None:
        try:
            from redis import Redis
        except ImportError as e:
            raise RuntimeError(
                "redis-py is not installed. Add `redis>=5.0` to "
                "requirements.txt (see _pending_deps_queue.txt)."
            ) from e
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _redis = Redis.from_url(url, decode_responses=False)
    return _redis


def _get_queue():
    """Singleton RQ Queue."""
    global _queue
    if _queue is None:
        try:
            from rq import Queue
        except ImportError as e:
            raise RuntimeError(
                "rq is not installed. Add `rq>=2.0` to requirements.txt "
                "(see _pending_deps_queue.txt)."
            ) from e
        name = os.environ.get("RQ_QUEUE_NAME", "jobhunt")
        _queue = Queue(name, connection=_get_redis())
    return _queue


def _idempotency_key(user_id: UUID | str, kind: str, payload: dict[str, Any]) -> str:
    """sha256 of `user_id|kind|sorted_json(payload)`.

    Sorted-keys serialization is critical — `{"a":1,"b":2}` and
    `{"b":2,"a":1}` must hash the same. We never include
    timestamps/nonces in the payload.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    blob = f"{user_id}|{kind}|{canonical}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _enqueue_or_dedup(
    *,
    user_id: UUID | str,
    kind: str,
    payload: dict[str, Any],
    worker_func: str,
    job_timeout: int = 900,
    result_ttl: int = 86400,
    failure_ttl: int = 604800,
) -> str:
    """Common dedup-then-enqueue path used by every kind.

    Returns the `jobs_runs.id` (the canonical run id) — NOT the RQ
    job_id. Callers track jobs by run id, not by RQ id, because RQ ids
    rotate on retry but the run id is stable.

    Sequence:
      1. compute key
      2. find_by_idempotency_key — if a queued/running row exists,
         return its id (don't enqueue)
      3. create_run (insert jobs_runs row, status='queued')
      4. queue.enqueue(worker_func, run.id, ...)
      5. return run.id

    Failure modes:
      - Step 2 finds a TERMINAL row (succeeded/failed/cancelled): we
        treat it as "no active duplicate" and proceed to step 3, but the
        DB UNIQUE constraint on idempotency_key would trip. To handle
        that we return the existing terminal row's id and let the caller
        decide whether to retry. Realistically, "force": True flips the
        hash so retries don't collide.
    """
    from api.jobs_runs import (
        ACTIVE_STATUSES,
        TERMINAL_STATUSES,
        create_run,
        find_by_idempotency_key,
    )

    key = _idempotency_key(user_id, kind, payload)

    existing = find_by_idempotency_key(key)
    if existing is not None:
        if existing.status in ACTIVE_STATUSES:
            logger.info(
                f"queue: dedup hit kind={kind} key={key[:12]}… "
                f"-> existing run {existing.id} (status={existing.status})"
            )
            return existing.id
        if existing.status in TERMINAL_STATUSES:
            # Terminal — but the unique index would block re-insert. For
            # a true retry the caller flips `force=True` (changes the
            # payload, changes the hash). Returning the terminal id here
            # is the least-surprising behavior; the API layer can decide
            # to surface "already completed" to the user.
            logger.info(
                f"queue: terminal dedup hit kind={kind} key={key[:12]}… "
                f"-> existing run {existing.id} (status={existing.status})"
            )
            return existing.id

    run = create_run(
        user_id=user_id,
        kind=kind,
        payload=payload,
        idempotency_key=key,
    )

    q = _get_queue()
    q.enqueue(
        worker_func,
        run.id,
        job_timeout=job_timeout,
        result_ttl=result_ttl,
        failure_ttl=failure_ttl,
        # `description` shows up in `rq info` and Sentry-style dashboards.
        description=f"{kind} run_id={run.id} user_id={user_id}",
        # `meta` is opaque dict stored on the RQ job for debugging.
        meta={"run_id": run.id, "kind": kind, "user_id": str(user_id)},
    )
    logger.info(
        f"queue: enqueued kind={kind} run_id={run.id} user_id={user_id} "
        f"key={key[:12]}…"
    )
    return run.id


# ─── Public API: one enqueue function per graph ────────────────────────────


def enqueue_g2_build(
    user_id: UUID | str,
    job_id: int,
    *,
    force: bool = False,
    max_cost_usd: Optional[float] = None,
) -> str:
    """Enqueue a G2 resume build for one job.

    Mirrors the call signature of POST /jobs/{id}/generate-resume so the
    follow-up PR that swaps BackgroundTasks→queue is mechanical:
      background_tasks.add_task(_run)
        ↓
      run_id = enqueue_g2_build(user_id, job_id, force=force, max_cost_usd=max_cost_usd)

    Returns the jobs_runs.id (UUID string). Caller exposes that to the
    UI for status polling: GET /jobs-runs/{run_id}.
    """
    payload: dict[str, Any] = {"job_id": int(job_id), "force": bool(force)}
    if max_cost_usd is not None:
        payload["max_cost_usd"] = float(max_cost_usd)
    return _enqueue_or_dedup(
        user_id=user_id,
        kind="g2_resume",
        payload=payload,
        worker_func="api.worker.worker_run_g2",
    )


def enqueue_g1_discovery(
    user_id: UUID | str,
    target_company_id: UUID | str,
    *,
    force: bool = False,
) -> str:
    """Enqueue a G1 persona deep-research run for one company.

    `target_company_id` is the `companies.id` UUID; the worker resolves
    it to a company name internally (via load by id). We avoid passing
    raw company name in the payload to keep idempotency keys stable
    even after typo fixes / re-canonicalisation.
    """
    payload: dict[str, Any] = {
        "target_company_id": str(target_company_id),
        "force": bool(force),
    }
    return _enqueue_or_dedup(
        user_id=user_id,
        kind="g1_discovery",
        payload=payload,
        worker_func="api.worker.worker_run_g1",
    )


def enqueue_g3_interview_prep(
    user_id: UUID | str,
    application_id: UUID | str,
    *,
    round_type: str = "hm",
    round_number: int = 1,
    max_cost_usd: Optional[float] = None,
    force: bool = False,
) -> str:
    """Enqueue a G3 interview prep build for one application/round."""
    payload: dict[str, Any] = {
        "application_id": str(application_id),
        "round_type": round_type,
        "round_number": int(round_number),
        "force": bool(force),
    }
    if max_cost_usd is not None:
        payload["max_cost_usd"] = float(max_cost_usd)
    return _enqueue_or_dedup(
        user_id=user_id,
        kind="g3_interview",
        payload=payload,
        worker_func="api.worker.worker_run_g3",
    )


def enqueue_g4_linkedin_post(
    user_id: UUID | str,
    *,
    count: int = 1,
    angle: Optional[str] = None,
    target_company_id: Optional[str] = None,
    nonce: int = 0,
    force: bool = False,
) -> str:
    """Enqueue a G4 LinkedIn-draft generation run for one user.

    The graph picks an angle + a recent news anchor and writes one
    `linkedin_drafts` row with status='draft'. The user reviews at
    /linkedin and approves / edits / rejects; the engine never auto-posts.

    `count` is encoded into the payload but the worker only ever runs
    ONE graph per call. Callers wanting N drafts loop and pass distinct
    `nonce` values so the idempotency key differs.

    Returns the jobs_runs.id (UUID string).
    """
    payload: dict[str, Any] = {
        "count": int(count),
        "force": bool(force),
        "nonce": int(nonce),
    }
    if angle is not None:
        payload["angle"] = str(angle)
    if target_company_id is not None:
        payload["target_company_id"] = str(target_company_id)
    return _enqueue_or_dedup(
        user_id=user_id,
        kind="g4_linkedin_post",
        payload=payload,
        worker_func="api.worker.worker_run_g4",
        # LinkedIn drafts are short — 60s/call is plenty.
        job_timeout=180,
    )


# ─── Operations helpers ────────────────────────────────────────────────────
def queue_health() -> dict[str, Any]:
    """Snapshot for /admin/queue-health endpoint and CLI debugging.

    Returns counts in each lifecycle bucket. Cheap (single PING + 4
    LLEN-equivalents).
    """
    try:
        q = _get_queue()
        return {
            "name": q.name,
            "queued": q.count,
            "started": q.started_job_registry.count,
            "deferred": q.deferred_job_registry.count,
            "failed": q.failed_job_registry.count,
            "scheduled": q.scheduled_job_registry.count,
            "redis_connected": _get_redis().ping(),
        }
    except Exception as e:
        logger.exception("queue_health failed")
        return {"error": f"{type(e).__name__}: {e}"}
