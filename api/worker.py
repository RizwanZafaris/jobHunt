"""
api/worker.py — RQ worker entrypoint for jobHunt's long-running graphs.

The companion to api/queue.py. Three responsibilities:

  1. Provide three `worker_run_*` functions that RQ calls inside a worker
     process. Each one wraps an existing graph entrypoint:
         worker_run_g2  → resume_agents.g2_run.run_g2_graph
         worker_run_g1  → agents.persona_deep_research.deep_research_persona
         worker_run_g3  → interview_agents.g3_run.run_g3_graph

     The wrapper handles status transitions on `jobs_runs`:
         queued → running   (mark_running, attempts += 1)
         success         → succeeded (mark_succeeded with result blob)
         exception       → mark_failed(retry=...) and re-raise so RQ
                          retries the job with exponential backoff.

  2. Expose a `__main__` entrypoint so we can run as
     `python -m api.worker`. This is what railway.toml's worker service
     starts. It instantiates an RQ Worker on the default queue and
     loops forever.

  3. Implement the retry policy. RQ's `Retry` accepts a list of intervals
     in seconds. We use [60, 240, 960] for exponential-ish backoff
     (1m → 4m → 16m). After 3 failed attempts the job goes to RQ's
     failed registry AND the jobs_runs row goes to status='failed'.

The graph functions are async; we run them via `asyncio.run` inside the
worker. RQ workers are sync, so this is the natural bridge.

WORKER_CONCURRENCY env var:
    Default 1. To bump in production set WORKER_CONCURRENCY=4 in Railway
    and we'll spawn N RQ workers in subprocesses (RQ's own --burst
    isn't suitable for a long-lived worker; we use plain Worker.work()
    in a single process and rely on Railway's replicas for true
    horizontal scaling).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
from typing import Any

logger = logging.getLogger(__name__)

# Backoff intervals in seconds: 1 min → 4 min → 16 min. Three retries.
RETRY_INTERVALS = [60, 240, 960]
MAX_ATTEMPTS = 3


def _truncate(s: str, n: int = 4000) -> str:
    """Trim long traceback strings before storing in jobs_runs.last_error."""
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 3] + "..."


def _is_retryable(exc: BaseException) -> bool:
    """Decide whether to retry on a given exception class.

    Retry for: transient infra (timeouts, network, Redis blips),
    rate-limits, generic LLM 5xx.
    Don't retry for: programmer errors (TypeError, AttributeError,
    KeyError on payload), explicit ValueError signaling bad input.
    """
    transient = (
        TimeoutError, ConnectionError, OSError,
    )
    if isinstance(exc, transient):
        return True
    name = type(exc).__name__.lower()
    if any(s in name for s in ("rate", "timeout", "connection", "throttle", "transient")):
        return True
    # Anthropic / OpenAI / Google SDKs use APIError / RateLimitError /
    # ServiceUnavailableError; we string-match defensively to avoid
    # importing every SDK here.
    if any(s in name for s in ("apierror", "ratelimiterror", "serviceunavailable")):
        return True
    # Programmer errors — don't retry.
    if isinstance(exc, (TypeError, AttributeError, KeyError, ValueError)):
        return False
    # Default: retry once or twice. The attempts counter on jobs_runs
    # caps total retries.
    return True


def _run_async(coro):
    """Run an async coroutine from a sync RQ worker.

    Each worker_run_* call gets a fresh event loop; we don't share. This
    is intentionally simple — long-running graphs don't benefit from
    loop reuse and shared loops have caused weird state-leak bugs.
    """
    return asyncio.run(coro)


# ─── Worker job functions ─────────────────────────────────────────────────


def worker_run_g2(jobs_run_id: str) -> dict[str, Any]:
    """RQ job: run the G2 resume graph for one jobs_runs row.

    1. Load the row, transition queued→running (also bumps attempts).
    2. Extract job_id / force / max_cost_usd from row.payload.
    3. Call resume_agents.g2_run.run_g2_graph(...).
    4. On success: mark_succeeded with a trimmed result dict.
    5. On exception: mark_failed (retry=true unless we've hit MAX_ATTEMPTS
       OR the error is non-retryable), then re-raise so RQ retries.

    Returns the (trimmed) result dict; this is what RQ stores under
    job.result for the result_ttl window.
    """
    from api.jobs_runs import get_run, mark_failed, mark_running, mark_succeeded

    run = get_run(jobs_run_id)
    if not run:
        # Nothing we can do — the row was deleted between enqueue and
        # worker pickup. Don't retry; just log loudly.
        logger.error(f"worker_run_g2: jobs_runs row {jobs_run_id} not found; aborting")
        return {"error": "row_not_found", "run_id": jobs_run_id}

    mark_running(jobs_run_id)
    payload = run.payload or {}
    job_id = payload.get("job_id")
    if job_id is None:
        mark_failed(jobs_run_id, "payload.job_id missing", retry=False)
        raise ValueError(f"worker_run_g2 payload missing job_id: {payload!r}")

    max_cost_usd = payload.get("max_cost_usd")

    try:
        from resume_agents.g2_run import run_g2_graph
        final_state = _run_async(run_g2_graph(
            job_id=int(job_id),
            max_cost_usd=max_cost_usd,
        ))
        # Trim the state to a transport-safe shape — the full state can
        # be megabytes (transcript blob, intermediate artifacts).
        result = {
            "job_id": int(job_id),
            "final_score": final_state.get("final_score"),
            "iteration": final_state.get("iteration"),
            "converged": final_state.get("converged"),
            "cost_usd_total": final_state.get("cost_usd_total"),
            "latency_ms_total": final_state.get("latency_ms_total"),
            "resume_pdf_url": final_state.get("resume_pdf_url"),
            "resume_docx_url": final_state.get("resume_docx_url"),
        }
        mark_succeeded(jobs_run_id, result)
        return result
    except Exception as exc:
        attempt_n = (run.attempts or 0) + 1  # we just bumped in mark_running
        retry = _is_retryable(exc) and attempt_n < MAX_ATTEMPTS
        err = _truncate(
            f"{type(exc).__name__}: {exc}\n\n"
            f"attempt {attempt_n}/{MAX_ATTEMPTS} — retry={retry}\n\n"
            f"{traceback.format_exc()}"
        )
        logger.exception(
            f"worker_run_g2 failed run_id={jobs_run_id} job_id={job_id} "
            f"attempt={attempt_n} retry={retry}"
        )
        mark_failed(jobs_run_id, err, retry=retry)
        raise  # Let RQ apply Retry policy


def worker_run_g1(jobs_run_id: str) -> dict[str, Any]:
    """RQ job: run the G1 persona deep-research for one company.

    Resolves target_company_id → company name → calls deep_research_persona.
    """
    from api.jobs_runs import get_run, mark_failed, mark_running, mark_succeeded

    run = get_run(jobs_run_id)
    if not run:
        logger.error(f"worker_run_g1: jobs_runs row {jobs_run_id} not found; aborting")
        return {"error": "row_not_found", "run_id": jobs_run_id}

    mark_running(jobs_run_id)
    payload = run.payload or {}
    company_id = payload.get("target_company_id")
    if not company_id:
        mark_failed(jobs_run_id, "payload.target_company_id missing", retry=False)
        raise ValueError(f"worker_run_g1 payload missing target_company_id: {payload!r}")

    try:
        # Resolve company name from the companies row.
        from db.client import get_supabase
        db = get_supabase()
        rows = (
            db.table("companies")
            .select("name")
            .eq("id", company_id)
            .limit(1)
            .execute()
            .data or []
        )
        if not rows:
            mark_failed(
                jobs_run_id,
                f"companies row {company_id} not found",
                retry=False,
            )
            raise ValueError(f"companies row not found: {company_id}")
        company_name = rows[0]["name"]

        from agents.persona_deep_research import deep_research_persona
        r = _run_async(deep_research_persona(company_name))
        result = {
            "company_name": r.company_name,
            "persona_quality": r.persona_quality,
            "sources_count": r.sources_count,
            "sections": list(r.sections.keys()),
            "ats_keyword_bank": r.ats_keyword_bank,
            "cost_usd": r.cost_usd,
            "latency_ms": r.latency_ms,
            "note": r.note,
        }
        mark_succeeded(jobs_run_id, result)
        return result
    except Exception as exc:
        attempt_n = (run.attempts or 0) + 1
        retry = _is_retryable(exc) and attempt_n < MAX_ATTEMPTS
        err = _truncate(
            f"{type(exc).__name__}: {exc}\n\n"
            f"attempt {attempt_n}/{MAX_ATTEMPTS} — retry={retry}\n\n"
            f"{traceback.format_exc()}"
        )
        logger.exception(
            f"worker_run_g1 failed run_id={jobs_run_id} attempt={attempt_n} retry={retry}"
        )
        mark_failed(jobs_run_id, err, retry=retry)
        raise


def worker_run_g3(jobs_run_id: str) -> dict[str, Any]:
    """RQ job: run the G3 interview-prep graph for one application/round."""
    from api.jobs_runs import get_run, mark_failed, mark_running, mark_succeeded

    run = get_run(jobs_run_id)
    if not run:
        logger.error(f"worker_run_g3: jobs_runs row {jobs_run_id} not found; aborting")
        return {"error": "row_not_found", "run_id": jobs_run_id}

    mark_running(jobs_run_id)
    payload = run.payload or {}
    application_id = payload.get("application_id")
    if not application_id:
        mark_failed(jobs_run_id, "payload.application_id missing", retry=False)
        raise ValueError(f"worker_run_g3 payload missing application_id: {payload!r}")

    round_type = payload.get("round_type", "hm")
    round_number = int(payload.get("round_number", 1))
    max_cost_usd = payload.get("max_cost_usd")

    try:
        from interview_agents.g3_run import run_g3_graph
        final_state = _run_async(run_g3_graph(
            application_id=str(application_id),
            round_type=round_type,
            round_number=round_number,
            max_cost_usd=max_cost_usd,
        ))
        result = {
            "application_id": str(application_id),
            "round_type": round_type,
            "round_number": round_number,
            "mock_critic_score": final_state.get("mock_critic_score"),
            "mock_iteration": final_state.get("mock_iteration"),
            "converged": final_state.get("converged"),
            "cost_usd_total": final_state.get("cost_usd_total"),
            "latency_ms_total": final_state.get("latency_ms_total"),
            "interview_pack_path": final_state.get("interview_pack_path"),
        }
        mark_succeeded(jobs_run_id, result)
        return result
    except Exception as exc:
        attempt_n = (run.attempts or 0) + 1
        retry = _is_retryable(exc) and attempt_n < MAX_ATTEMPTS
        err = _truncate(
            f"{type(exc).__name__}: {exc}\n\n"
            f"attempt {attempt_n}/{MAX_ATTEMPTS} — retry={retry}\n\n"
            f"{traceback.format_exc()}"
        )
        logger.exception(
            f"worker_run_g3 failed run_id={jobs_run_id} attempt={attempt_n} retry={retry}"
        )
        mark_failed(jobs_run_id, err, retry=retry)
        raise


def worker_run_g4(jobs_run_id: str) -> dict[str, Any]:
    """RQ job: run the G4 LinkedIn-draft graph for one user."""
    from api.jobs_runs import get_run, mark_failed, mark_running, mark_succeeded

    run = get_run(jobs_run_id)
    if not run:
        logger.error(f"worker_run_g4: jobs_runs row {jobs_run_id} not found; aborting")
        return {"error": "row_not_found", "run_id": jobs_run_id}

    mark_running(jobs_run_id)
    payload = run.payload or {}

    try:
        from agents.g4_linkedin_graph import run_g4_graph
        final_state = _run_async(run_g4_graph(
            user_id=str(run.user_id),
            request_angle=payload.get("angle"),
            request_target_company_id=payload.get("target_company_id"),
        ))
        result = {
            "draft_id": final_state.get("draft_id"),
            "angle": final_state.get("chosen_angle"),
            "company_name": final_state.get("chosen_company_name"),
            "cost_usd_total": final_state.get("cost_usd_total"),
            "latency_ms_total": final_state.get("latency_ms_total"),
        }
        mark_succeeded(jobs_run_id, result)
        return result
    except Exception as exc:
        attempt_n = (run.attempts or 0) + 1
        retry = _is_retryable(exc) and attempt_n < MAX_ATTEMPTS
        err = _truncate(
            f"{type(exc).__name__}: {exc}\n\n"
            f"attempt {attempt_n}/{MAX_ATTEMPTS} — retry={retry}\n\n"
            f"{traceback.format_exc()}"
        )
        logger.exception(
            f"worker_run_g4 failed run_id={jobs_run_id} attempt={attempt_n} retry={retry}"
        )
        mark_failed(jobs_run_id, err, retry=retry)
        raise


# ─── Worker entrypoint ────────────────────────────────────────────────────


def _start_worker():
    """Boot one RQ worker in this process and block on .work()."""
    from api.queue import _get_queue, _get_redis
    try:
        from rq import Worker
    except ImportError as e:
        raise RuntimeError(
            "rq is not installed. Install via requirements.txt "
            "(see _pending_deps_queue.txt for the pinned version)."
        ) from e

    queue = _get_queue()
    redis_conn = _get_redis()

    # Sanity probe — fail fast if Redis is unreachable rather than
    # waiting until the first job pop.
    try:
        redis_conn.ping()
    except Exception as e:
        logger.error(f"worker boot: Redis ping failed: {e}")
        raise

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info(
        f"jobHunt worker starting (queue={queue.name}, redis={redis_conn})"
    )
    worker = Worker([queue], connection=redis_conn)
    worker.work(with_scheduler=True)


def _start_worker_pool(concurrency: int):
    """Spawn N RQ workers in subprocesses for in-pod concurrency.

    For Railway we recommend horizontal scaling (multiple worker
    replicas) over in-process concurrency — but if a deploy needs to
    run several short jobs in parallel inside one container, set
    WORKER_CONCURRENCY=N. Each subprocess is independent.
    """
    import multiprocessing as mp

    if concurrency <= 1:
        return _start_worker()

    procs = []
    for i in range(concurrency):
        p = mp.Process(target=_start_worker, name=f"rq-worker-{i}")
        p.start()
        procs.append(p)
        logger.info(f"spawned worker subprocess {i} (pid={p.pid})")

    # Block on all of them; if one dies, abort the whole thing so
    # Railway's restart policy kicks in.
    for p in procs:
        p.join()
        if p.exitcode != 0:
            logger.error(f"worker subprocess {p.name} exited with {p.exitcode}")
            for other in procs:
                if other.is_alive():
                    other.terminate()
            sys.exit(1)


if __name__ == "__main__":
    concurrency = int(os.environ.get("WORKER_CONCURRENCY", "1") or "1")
    _start_worker_pool(concurrency)
