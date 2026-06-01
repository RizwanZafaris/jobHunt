"""tests/test_resume_build_resilience.py — workspace resume-build resilience.

Two production incidents from 2026-06-01, both fixed in api/queue.py's
``_enqueue_or_dedup`` (and exercised by the workspace ``/build-resume`` path):

  1. TERMINAL-FAILED DEDUP TRAP. The dedup branch returned the existing run
     id for ANY terminal status — including ``failed``. A build that failed
     (e.g. the worker was down, so the row was swept to ``failed``/attempts=0
     without ever running) PERMANENTLY blocked rebuilds: every click hit the
     dedup branch, the UI polled the stale ``failed`` row, and showed
     "build failed" forever. The only escape was ``force=true``. Fix:
     ``succeeded`` still dedups (never silently rebuild a good resume), but
     ``failed``/``cancelled`` reset the row in place (``reset_run``) and
     re-enqueue.

  2. DEAD-WORKER SILENT ROT. Redis can be reachable while no worker drains
     the queue (pool down, crash-looping, or — the incident — bound to a
     different queue NAME than the API enqueues to). ``q.enqueue()`` then
     succeeds but the job never runs and the UI polls a ``queued`` row that
     never advances. Fix: when no live worker is heartbeating on the queue,
     run in-process (reusing the Redis-down fallback), gated by
     ``RESUME_INPROCESS_WHEN_NO_WORKER`` (default on) and FAIL-SAFE to normal
     queueing on any detection error.

Style mirrors tests/test_tenant_inflight_cap.py: ``supabase`` is stubbed so
the import never needs a real DB, and we patch at api.queue's seams.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# api.queue / api.jobs_runs pull in supabase transitively on some paths; stub
# it like the sibling queue tests so import never fails in a bare env.
sys.modules.setdefault("supabase", MagicMock())

import api.queue as q  # noqa: E402
from api.jobs_runs import JobRun  # noqa: E402

USER = "11111111-1111-1111-1111-111111111111"
WF = "api.worker.worker_run_g2"
PAYLOAD = {"job_id": 13908, "force": False}


def _run(status: str, rid: str = "run-existing") -> JobRun:
    return JobRun(
        id=rid,
        user_id=USER,
        kind="g2_resume",
        payload=PAYLOAD,
        status=status,
        idempotency_key="key-abc",
    )


def _call():
    return q._enqueue_or_dedup(
        user_id=USER, kind="g2_resume", payload=PAYLOAD, worker_func=WF
    )


# ───────────────────────── 1. terminal-failed dedup trap ─────────────────────


def test_failed_run_is_reset_and_reenqueued_not_returned_stale():
    """A prior FAILED run must NOT short-circuit. It is reset in place and
    re-enqueued so the user can actually retry (the 2026-06-01 trap)."""
    fake_q = MagicMock()
    fake_q.name = "jobhunt"
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=_run("failed")), \
            patch("api.jobs_runs.reset_run", return_value=_run("queued")) as reset_fn, \
            patch("api.jobs_runs.create_run") as create_fn, \
            patch.object(q, "_get_queue", return_value=fake_q), \
            patch.object(q, "_has_live_worker", return_value=True):
        rid = _call()
    reset_fn.assert_called_once_with("run-existing")  # reset, not stale-return
    create_fn.assert_not_called()                     # reuse the row, no new insert
    fake_q.enqueue.assert_called_once()               # actually re-enqueued
    assert rid == "run-existing"


def test_cancelled_run_is_also_retryable():
    fake_q = MagicMock()
    fake_q.name = "jobhunt"
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=_run("cancelled")), \
            patch("api.jobs_runs.reset_run", return_value=_run("queued")) as reset_fn, \
            patch.object(q, "_get_queue", return_value=fake_q), \
            patch.object(q, "_has_live_worker", return_value=True):
        _call()
    reset_fn.assert_called_once()
    fake_q.enqueue.assert_called_once()


def test_succeeded_run_still_dedups_no_rebuild():
    """A SUCCEEDED run must still short-circuit — never silently spend ~$1
    rebuilding a good resume. No reset, no enqueue."""
    fake_q = MagicMock()
    fake_q.name = "jobhunt"
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=_run("succeeded")), \
            patch("api.jobs_runs.reset_run") as reset_fn, \
            patch.object(q, "_get_queue", return_value=fake_q), \
            patch.object(q, "_has_live_worker", return_value=True):
        rid = _call()
    assert rid == "run-existing"
    reset_fn.assert_not_called()
    fake_q.enqueue.assert_not_called()


def test_active_run_still_dedups():
    """A queued/running run still dedups (unchanged behavior)."""
    fake_q = MagicMock()
    fake_q.name = "jobhunt"
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=_run("running")), \
            patch.object(q, "_get_queue", return_value=fake_q), \
            patch.object(q, "_has_live_worker", return_value=True):
        rid = _call()
    assert rid == "run-existing"
    fake_q.enqueue.assert_not_called()


# ───────────────────── 2. dead-worker in-process fallback ────────────────────


def test_no_live_worker_runs_inprocess_not_enqueued():
    """Redis reachable but no live worker → run in-process, do NOT enqueue."""
    fake_q = MagicMock()
    fake_q.name = "jobhunt"
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=None), \
            patch("api.jobs_runs.create_run", return_value=_run("queued", "run-new")), \
            patch.object(q, "_over_inflight_cap", return_value=(False, 0, 0)), \
            patch.object(q, "_get_queue", return_value=fake_q), \
            patch.object(q, "_has_live_worker", return_value=False), \
            patch.object(q, "_run_inprocess_fallback") as fallback:
        rid = _call()
    fallback.assert_called_once_with(WF, "run-new")
    fake_q.enqueue.assert_not_called()
    assert rid == "run-new"


def test_live_worker_enqueues_normally():
    fake_q = MagicMock()
    fake_q.name = "jobhunt"
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=None), \
            patch("api.jobs_runs.create_run", return_value=_run("queued", "run-new")), \
            patch.object(q, "_over_inflight_cap", return_value=(False, 0, 0)), \
            patch.object(q, "_get_queue", return_value=fake_q), \
            patch.object(q, "_has_live_worker", return_value=True), \
            patch.object(q, "_run_inprocess_fallback") as fallback:
        rid = _call()
    fake_q.enqueue.assert_called_once()
    fallback.assert_not_called()
    assert rid == "run-new"


def test_inprocess_guard_disabled_by_env(monkeypatch):
    """RESUME_INPROCESS_WHEN_NO_WORKER=0 disables the guard → always enqueue,
    even with no live worker (operator opt-out for a strict queue-only mode)."""
    monkeypatch.setenv("RESUME_INPROCESS_WHEN_NO_WORKER", "0")
    fake_q = MagicMock()
    fake_q.name = "jobhunt"
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=None), \
            patch("api.jobs_runs.create_run", return_value=_run("queued", "run-new")), \
            patch.object(q, "_over_inflight_cap", return_value=(False, 0, 0)), \
            patch.object(q, "_get_queue", return_value=fake_q), \
            patch.object(q, "_has_live_worker", return_value=False), \
            patch.object(q, "_run_inprocess_fallback") as fallback:
        _call()
    fake_q.enqueue.assert_called_once()  # guard off → normal enqueue
    fallback.assert_not_called()


# ───────────────────────── _has_live_worker fail-safe ────────────────────────


def test_has_live_worker_failsafe_true_when_rq_errors():
    """Any probe error → assume a worker exists (never divert work on a blip)."""
    with patch("rq.Worker.all", side_effect=RuntimeError("redis hiccup")):
        assert q._has_live_worker(MagicMock()) is True


def test_has_live_worker_false_when_no_workers_registered():
    with patch("rq.Worker.all", return_value=[]):
        assert q._has_live_worker(MagicMock()) is False


def test_has_live_worker_true_with_fresh_heartbeat():
    from datetime import datetime, timezone
    w = MagicMock()
    w.last_heartbeat = datetime.now(timezone.utc)
    with patch("rq.Worker.all", return_value=[w]):
        assert q._has_live_worker(MagicMock()) is True


def test_has_live_worker_false_with_stale_heartbeat():
    from datetime import datetime, timedelta, timezone
    w = MagicMock()
    w.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=999)
    with patch("rq.Worker.all", return_value=[w]):
        assert q._has_live_worker(MagicMock()) is False
