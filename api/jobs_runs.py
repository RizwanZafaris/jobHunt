"""
api/jobs_runs.py — Pydantic models + Supabase DB helpers for the
`jobs_runs` table that backs the Redis/RQ queue (Phase 3 — durability).

Every long-running graph (G1 discovery, G2 resume, G3 interview, future
G4 LinkedIn post) writes a row here BEFORE it's enqueued to RQ. The
worker mutates this row through its lifecycle:

    queued → running → succeeded
                    → failed
                    → cancelled

`idempotency_key` is `sha256(user_id|kind|sorted_json(payload))`. Two
identical requests within a queued/running window collapse to the same
row, so the user clicking "Generate Resume" twice in 5s doesn't enqueue
two builds.

Used by:
  api/queue.py        — enqueue side
  api/worker.py       — RQ worker side
  api/orphan_reaper.py — periodic stale-row sweep
  api/server.py       — read-side endpoints (status polling) — added in
                        the follow-up PR that swaps BackgroundTasks calls.

The migration file `db/migrations/2026_05_10_003_jobs_runs.sql` creates
the table and RLS policies. service-role (used by the backend) bypasses
RLS; user JWTs see only their own rows.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ─── Constants ─────────────────────────────────────────────────────────────
ALLOWED_KINDS = {"g1_discovery", "g2_resume", "g3_interview", "g4_linkedin_post"}
ALLOWED_STATUSES = {"queued", "running", "succeeded", "failed", "cancelled"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
ACTIVE_STATUSES = {"queued", "running"}


# ─── Pydantic model ────────────────────────────────────────────────────────
class JobRun(BaseModel):
    """Mirror of one `jobs_runs` row.

    `id` is a UUID string (Supabase returns gen_random_uuid as text).
    `payload` is whatever the enqueuer wrote — typically the kwargs the
    worker function will need (job_id, force, max_cost_usd, etc).
    `result` is whatever the worker recorded on success (URLs, scores).
    `last_error` is set by the worker on failure (truncated traceback +
    exception class).
    """
    id: str
    user_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "queued"
    idempotency_key: str
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    attempts: int = 0
    last_error: Optional[str] = None
    result: Optional[dict[str, Any]] = None
    created_at: Optional[datetime] = None


# ─── DB helpers (synchronous — supabase-py is sync, called from worker
#     code where async isn't a fit) ───────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_model(row: dict) -> JobRun:
    """Coerce a raw Supabase row to a JobRun model.

    Supabase returns timestamptz as ISO strings — pydantic v2 parses them
    automatically. UUIDs come back as strings.
    """
    return JobRun(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        kind=row["kind"],
        payload=row.get("payload") or {},
        status=row.get("status", "queued"),
        idempotency_key=row["idempotency_key"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        attempts=row.get("attempts") or 0,
        last_error=row.get("last_error"),
        result=row.get("result"),
        created_at=row.get("created_at"),
    )


def create_run(
    user_id: UUID | str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
) -> JobRun:
    """Insert a fresh `queued` row. Caller is responsible for checking
    `find_by_idempotency_key` first to avoid duplicate enqueues — but if
    the unique index trips on a race, the caller can re-fetch.

    Returns the created JobRun.
    """
    if kind not in ALLOWED_KINDS:
        raise ValueError(
            f"Invalid kind {kind!r}; allowed: {sorted(ALLOWED_KINDS)}"
        )

    from db.client import get_supabase
    db = get_supabase()
    row = {
        "user_id": str(user_id),
        "kind": kind,
        "payload": payload,
        "status": "queued",
        "idempotency_key": idempotency_key,
        "attempts": 0,
    }
    result = db.table("jobs_runs").insert(row).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError(f"Insert into jobs_runs returned no rows for kind={kind}")
    return _row_to_model(rows[0])


def get_run(run_id: str) -> Optional[JobRun]:
    """Fetch a single run by id; returns None if not found."""
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("jobs_runs")
        .select("*")
        .eq("id", run_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return _row_to_model(rows[0]) if rows else None


def find_by_idempotency_key(key: str) -> Optional[JobRun]:
    """Look up an existing run by idempotency key.

    The unique index on `idempotency_key` guarantees at most one row.
    Returns None if no row.
    """
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("jobs_runs")
        .select("*")
        .eq("idempotency_key", key)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return _row_to_model(rows[0]) if rows else None


def mark_running(run_id: str) -> None:
    """Worker called: flip queued → running, increment attempts, set
    started_at. Idempotent — running→running is a no-op (server-side
    update). Re-runs after retry happen on the same row.
    """
    from db.client import get_supabase
    db = get_supabase()
    # Read attempts so we can increment client-side. Supabase-py doesn't
    # support `attempts = attempts + 1` raw expressions on update; use
    # a read-modify-write. The unique key on idempotency_key + the
    # single-worker-at-a-time invariant per run id make this race-safe
    # enough for now (RQ's job lock prevents concurrent execution).
    cur = get_run(run_id)
    if not cur:
        logger.warning(f"mark_running: jobs_runs row {run_id} not found")
        return
    db.table("jobs_runs").update({
        "status": "running",
        "started_at": _now_iso(),
        "attempts": (cur.attempts or 0) + 1,
        "last_error": None,
    }).eq("id", run_id).execute()


def mark_succeeded(run_id: str, result: dict[str, Any]) -> None:
    """Worker called: flip running → succeeded, store result blob."""
    from db.client import get_supabase
    db = get_supabase()
    db.table("jobs_runs").update({
        "status": "succeeded",
        "finished_at": _now_iso(),
        "result": result or {},
        "last_error": None,
    }).eq("id", run_id).execute()


def mark_failed(run_id: str, error: str, retry: bool) -> None:
    """Worker called: record failure.

    If `retry=True` we leave status='queued' so RQ's retry machinery can
    pick the row back up on the next attempt; we record `last_error` for
    debugging. If `retry=False` (last attempt or non-retryable) we move
    the row to terminal status='failed'.
    """
    from db.client import get_supabase
    db = get_supabase()
    update: dict[str, Any] = {
        "last_error": (error or "")[:4000],  # cap stored size
    }
    if retry:
        # Will be re-picked-up by RQ retry; revert to queued for clarity.
        update["status"] = "queued"
    else:
        update["status"] = "failed"
        update["finished_at"] = _now_iso()
    db.table("jobs_runs").update(update).eq("id", run_id).execute()


def mark_cancelled(run_id: str, reason: Optional[str] = None) -> None:
    """Manual cancel from an admin endpoint. Marks terminal."""
    from db.client import get_supabase
    db = get_supabase()
    db.table("jobs_runs").update({
        "status": "cancelled",
        "finished_at": _now_iso(),
        "last_error": (reason or "cancelled")[:4000],
    }).eq("id", run_id).execute()


def reset_run(run_id: str) -> Optional[JobRun]:
    """Flip a TERMINAL run back to ``queued`` so it can be retried IN PLACE.

    A failed (or cancelled) build is retried by *reusing its existing row*
    rather than inserting a new one — the UNIQUE index on
    ``idempotency_key`` would otherwise reject a fresh insert carrying the
    same ``(user_id, kind, payload)`` hash. We clear the execution
    bookkeeping (``started_at`` / ``finished_at`` / ``last_error`` /
    ``result``) and reset ``attempts`` to 0 so the worker (or the
    in-process fallback) treats it as a clean run.

    Used by ``api/queue._enqueue_or_dedup`` when a user re-requests a build
    whose previous run is terminal-**failed**: a ``succeeded`` run still
    dedups (we never silently rebuild a good resume), but a ``failed`` one
    MUST be retryable without forcing the caller to mutate the payload
    (``force=true``). Without this, a single failed build permanently traps
    that job — every retry hits the terminal-dedup branch and the UI polls
    a stale ``failed`` row, showing "build failed" forever
    (production incident 2026-06-01: jobs_runs left ``failed``/attempts=0
    by a worker outage could never be rebuilt).

    Returns the updated JobRun (re-fetched), or ``None`` if the row vanished.
    """
    from db.client import get_supabase
    db = get_supabase()
    db.table("jobs_runs").update({
        "status": "queued",
        "started_at": None,
        "finished_at": None,
        "last_error": None,
        "result": None,
        "attempts": 0,
    }).eq("id", run_id).execute()
    return get_run(run_id)


def list_user_runs(user_id: UUID | str, limit: int = 50) -> list[JobRun]:
    """List recent runs for a user, newest first. Used by the dashboard."""
    from db.client import get_supabase
    db = get_supabase()
    result = (
        db.table("jobs_runs")
        .select("*")
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(min(max(limit, 1), 200))
        .execute()
    )
    return [_row_to_model(r) for r in (result.data or [])]


def find_orphans(stale_minutes: int = 15) -> list[JobRun]:
    """Find rows that are still 'running' but haven't been touched in
    `stale_minutes`. These are almost certainly orphaned by a Railway
    redeploy that killed the worker mid-job. The reaper sweeps these
    every 5 minutes.

    Note: started_at is a timestamptz; we compare against an ISO string
    in UTC, which Postgres parses correctly via the supabase-py REST
    layer.
    """
    from db.client import get_supabase
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    ).isoformat()
    db = get_supabase()
    result = (
        db.table("jobs_runs")
        .select("*")
        .eq("status", "running")
        .lt("started_at", cutoff)
        .execute()
    )
    return [_row_to_model(r) for r in (result.data or [])]
