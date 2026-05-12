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

    # 2026-05-12: in-process fallback when Redis is unreachable.
    # Production reported `redis.exceptions.ConnectionError: Error 111
    # connecting to localhost:6379` on every build-resume click because
    # Railway's Redis plugin isn't deployed. Rather than 500 the user,
    # we degrade gracefully: run the worker function in the current
    # process via asyncio.create_task. Loses durability (API restart
    # mid-build kills the work) but produces a working resume — which
    # is the user's goal RIGHT NOW. The jobs_runs row is still created
    # so the UI can poll for status.
    try:
        q = _get_queue()
        q.enqueue(
            worker_func,
            run.id,
            job_timeout=job_timeout,
            result_ttl=result_ttl,
            failure_ttl=failure_ttl,
            description=f"{kind} run_id={run.id} user_id={user_id}",
            meta={"run_id": run.id, "kind": kind, "user_id": str(user_id)},
        )
        logger.info(
            f"queue: enqueued kind={kind} run_id={run.id} user_id={user_id} "
            f"key={key[:12]}… (via Redis)"
        )
        return run.id
    except Exception as e:
        # Detect Redis connectivity issues. We catch broad Exception
        # because redis-py raises ConnectionError but also wraps in
        # other types depending on version. The error message contains
        # "connecting to" or "Connection refused" / "111" for the
        # network-down case we care about. Other errors (auth, bad URL)
        # also benefit from the fallback so we don't 500 on a
        # misconfigured queue.
        err_msg = str(e)
        looks_like_redis_down = (
            "connecting" in err_msg.lower()
            or "connection refused" in err_msg.lower()
            or "redis" in type(e).__name__.lower()
            or "111" in err_msg
        )
        if not looks_like_redis_down:
            # Unknown error — surface it instead of silently falling back.
            logger.exception(
                f"queue: enqueue failed for kind={kind} run_id={run.id} "
                f"(non-Redis error — re-raising)"
            )
            raise
        logger.warning(
            f"queue: Redis unreachable ({type(e).__name__}: {err_msg[:120]}) "
            f"— falling back to in-process execution for kind={kind} "
            f"run_id={run.id}. Resume will build but won't survive an "
            f"API restart. Deploy the Railway Redis plugin to fix."
        )
        # Run the worker function in-process via asyncio.create_task so
        # the API request returns immediately. The worker function is
        # synchronous (RQ design) but wraps async graphs internally via
        # asyncio.run. We invoke it on a background thread so the API
        # event loop isn't blocked.
        _run_inprocess_fallback(worker_func, run.id)
        return run.id


def _run_inprocess_fallback(worker_func: str, run_id: str) -> None:
    """Run an RQ worker function in-process via a background thread.

    Used when Redis is unreachable so build-resume / build-prep / build-
    linkedin all degrade gracefully to in-process execution. Threading
    is the simplest approach — the worker function is synchronous and
    uses asyncio.run internally, so we don't need to coordinate with
    the API's event loop.

    No durability — if the API container restarts mid-build, work is
    lost. Acceptable in single-user mode until Redis is deployed.
    """
    import importlib
    import threading

    def _execute() -> None:
        try:
            module_path, func_name = worker_func.rsplit(".", 1)
            module = importlib.import_module(module_path)
            func = getattr(module, func_name)
            logger.info(
                f"queue.inprocess: starting worker_func={worker_func} "
                f"run_id={run_id}"
            )
            func(run_id)
            logger.info(
                f"queue.inprocess: completed worker_func={worker_func} "
                f"run_id={run_id}"
            )
        except Exception:
            logger.exception(
                f"queue.inprocess: worker_func={worker_func} run_id={run_id} "
                f"FAILED — jobs_runs row will be marked 'failed' by the "
                f"worker's mark_failed() call, OR by the orphan_reaper "
                f"if mark_failed isn't reached."
            )

    threading.Thread(
        target=_execute,
        name=f"inprocess-{worker_func.split('.')[-1]}-{run_id[:8]}",
        daemon=True,  # die on API shutdown — we accept the durability loss
    ).start()


# ─── Public API: one enqueue function per graph ────────────────────────────


def enqueue_g2_build(
    user_id: UUID | str,
    job_id: int,
    *,
    force: bool = False,
    max_cost_usd: Optional[float] = None,
    warm_start_md: Optional[str] = None,
    edit_intent: Optional[str] = None,
    rebuild_scope: Optional[str] = None,
) -> str:
    """Enqueue a G2 resume build for one job.

    Mirrors the call signature of POST /jobs/{id}/generate-resume so the
    follow-up PR that swaps BackgroundTasks→queue is mechanical:
      background_tasks.add_task(_run)
        ↓
      run_id = enqueue_g2_build(user_id, job_id, force=force, max_cost_usd=max_cost_usd)

    Returns the jobs_runs.id (UUID string). Caller exposes that to the
    UI for status polling: GET /jobs-runs/{run_id}.

    Phase 2.1 — workspace rebuild plumbing:
      `warm_start_md` is the current resume markdown. When set, the
      writer node seeds from it instead of generating cold. Used by
      `POST /workspace/{id}/rebuild-section` and full-rebuild.
      `edit_intent` is the human instruction ("make this more
      product-led"). Surfaced to the writer's brief.
      `rebuild_scope` is `'section'` or `'full'` — recorded in payload
      so the worker / audit log knows which path was taken.

    All three flow into the idempotency-key payload, which means a
    rebuild after a fresh build doesn't dedup (different payload →
    different sha256). That's the right shape: the user explicitly
    asked for a different operation.
    """
    payload: dict[str, Any] = {"job_id": int(job_id), "force": bool(force)}
    if max_cost_usd is not None:
        payload["max_cost_usd"] = float(max_cost_usd)
    if warm_start_md:
        # Hash the warm-start to keep the payload (and the idempotency
        # key) compact. The full text rides on the worker side via the
        # jobs_runs row's payload column — but a sha is enough to make
        # the key unique. NOTE: we still STORE the full markdown in
        # payload so the worker can read it; the hash is just defensive
        # logging hygiene. Keeping the full text here means two distinct
        # warm starts (different drafts) generate different keys, which
        # is what we want.
        payload["warm_start_md"] = str(warm_start_md)
    if edit_intent:
        payload["edit_intent"] = str(edit_intent)
    if rebuild_scope:
        payload["rebuild_scope"] = str(rebuild_scope)
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


def enqueue_g9_story_extract(
    user_id: UUID | str,
    *,
    cv_hash: Optional[str] = None,
    force: bool = False,
) -> str:
    """Enqueue a G9 STAR+R story extraction run for one user.

    Triggered on master-CV update (the upload endpoint enqueues this so
    the user's story_bank stays in sync with their latest CV) and from
    the manual UI button (POST /workspace/stories/build).

    `cv_hash` is folded into the idempotency payload — re-uploading the
    SAME CV markdown collapses to a single run (cv_hash is the natural
    dedup key). A real edit produces a new hash and a new run. Pass
    `force=True` to bypass dedup (e.g. when retrying after a transient
    LLM failure).

    Returns the jobs_runs.id (UUID string). Cost is ~$0.20 per run.
    """
    payload: dict[str, Any] = {"force": bool(force)}
    if cv_hash:
        payload["cv_hash"] = str(cv_hash)
    return _enqueue_or_dedup(
        user_id=user_id,
        kind="g9_story_extract",
        payload=payload,
        worker_func="api.worker.worker_run_g9",
        # G9 is fixed-shape (no critic loop); 5 min is more than enough.
        job_timeout=300,
    )


def enqueue_g5_score(
    user_id: UUID | str,
    job_id: int,
    *,
    force: bool = False,
) -> str:
    """Enqueue a G5 fit-score evaluation for one job.

    Phase 2 §4.1: scoring is INFORMATIONAL (no auto-actions), so we never
    block JobScout's persistence on this run. The auto-trigger inside
    `JobScoutAgent.run` enqueues this for every freshly-upserted job and
    forgets about it; the worker calls into `agents.scoring_agent.score_role`,
    which idempotency-gates within a 7-day window.

    `force=True` re-scores even if the job was scored < 7 days ago.

    Returns the jobs_runs.id (UUID string). The G5 scoring call itself
    costs ~$0.15 in LLM tokens.
    """
    payload: dict[str, Any] = {"job_id": int(job_id), "force": bool(force)}
    return _enqueue_or_dedup(
        user_id=user_id,
        kind="g5_score",
        payload=payload,
        worker_func="api.worker.worker_run_g5",
        # G5 is fixed-shape (no critic loop, no iteration). 5 min is more
        # than enough; the four Sonnet calls + comp_cache + 2 RAG calls
        # add up to ~12s.
        job_timeout=300,
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


def enqueue_legitimacy_check(
    user_id: UUID | str,
    job_id: int,
    *,
    force: bool = False,
) -> str:
    """Enqueue a Tier-2 legitimacy scoring run for one job.

    Cheap (~$0.005 per run), so we don't apply a max_cost_usd cap — the
    Perplexity signal is the only LLM call and it self-caps at one
    sonar query. Caller (jobs_scout) enqueues this after the freshness
    pipeline; the /workspace endpoint calls score_legitimacy directly
    (no queue hop) because it's a user-facing button-press.

    `force` flips the idempotency hash so a manual re-run after an
    auto-scored row produces a fresh queued job rather than dedupping
    to the existing terminal one.
    """
    payload: dict[str, Any] = {
        "job_id": int(job_id),
        "force": bool(force),
    }
    return _enqueue_or_dedup(
        user_id=user_id,
        kind="legitimacy_check",
        payload=payload,
        worker_func="api.worker.worker_run_legitimacy",
        # Tight timeout — the heaviest call (Perplexity) has its own
        # 30s timeout, plus the URL HEAD probe (5s). 90s is plenty.
        job_timeout=120,
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
