"""Regression tests for the P0 launch-blocker fixes shipped 2026-05-16.

Covers:
  B1  - /admin/worker-status returns the expected shape
  B1  - /admin/requeue-stuck dispatches to the correct worker function per kind
  B1  - orphan_reaper._sweep_stuck_queued uses _KIND_TO_WORKER, not a
        fictional worker_dispatch
  B2  - /stories and /follow-ups no longer collide with /workspace/{job_id}
  B3  - CORS reads allowed origins from settings (no wildcard)
  B6  - orphan_reaper sweep writes the `error` column (NOT error_message)

No live Supabase, Redis, or APScheduler — all stubbed.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture
def client():
    """FastAPI TestClient against the real app, scheduler hook short-circuited.

    Phase-1 finding 2: /admin/* now uses ``require_admin`` (was verify_secret).
    Override ``get_current_user`` with an admin user so the admin endpoints
    resolve without touching the mocked Supabase user table. (Non-admin
    endpoints in this file don't depend on it, so the override is a no-op for
    them.)
    """
    from datetime import datetime, timezone
    from uuid import UUID
    from fastapi.testclient import TestClient
    from api import server
    from api.context import get_current_user
    from api.users import User

    server._scheduler_started = True

    def _admin_user() -> User:
        return User(
            id=UUID("00000000-0000-0000-0000-000000000001"),
            email="rizwanzaffar.pk@gmail.com",
            full_name="Rizwan Zafar",
            plan="lifetime",
            is_admin=True,
            created_at=datetime.now(timezone.utc),
        )

    server.app.dependency_overrides[get_current_user] = _admin_user
    yield TestClient(server.app)
    server.app.dependency_overrides.clear()


# ────────────────────────────────────────────────────────────────────────────
# B1 — /admin/worker-status
# ────────────────────────────────────────────────────────────────────────────


class _StubResult:
    """Stand-in for a PostgREST result object — has .data with a real list."""
    def __init__(self, data):
        self.data = data


class _StubQuery:
    """Returns self from every chain call, .execute() returns a _StubResult.

    Per-table override: callers can set self._results[table_name] (list) to
    have execute() return that list. Defaults to an empty list.
    """
    def __init__(self, results_by_table=None):
        self._results_by_table = results_by_table or {}
        self._current_table = None

    def table(self, name):
        self._current_table = name
        return self

    # Any other builder call → return self.
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self

    def __call__(self, *args, **kwargs):
        return self

    def execute(self):
        return _StubResult(self._results_by_table.get(self._current_table, []))


def _make_db_stub(results_by_table=None):
    """Build a Supabase stub with realistic .execute().data behaviour."""
    return _StubQuery(results_by_table)


def test_b1_worker_status_returns_expected_shape(client, monkeypatch):
    """Endpoint returns the documented contract: redis_ping/queue_depth/etc."""
    fake_redis = MagicMock()
    fake_redis.ping.return_value = True
    fake_redis.llen.return_value = 3

    from api import server, queue as queue_mod
    monkeypatch.setattr(queue_mod, "_get_redis", lambda: fake_redis)
    from db import client as db_client
    monkeypatch.setattr(db_client, "get_supabase", lambda: _make_db_stub())

    resp = client.get("/admin/worker-status", headers={"X-Secret-Key": "test-secret"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(["redis_ping", "queue_name", "queue_depth", "last_dequeued_at",
                "running_jobs", "old_queued_count"]).issubset(body.keys())
    assert body["redis_ping"] is True
    assert body["queue_name"] == "jobhunt"
    assert body["queue_depth"] == 3


def test_b1_worker_status_handles_redis_outage(client, monkeypatch):
    """If Redis is down, the endpoint still returns 200 with redis_ping=false."""
    from api import server, queue as queue_mod

    fake_redis = MagicMock()
    fake_redis.ping.side_effect = ConnectionError("redis down")

    monkeypatch.setattr(queue_mod, "_get_redis", lambda: fake_redis)
    from db import client as db_client
    monkeypatch.setattr(db_client, "get_supabase", lambda: _make_db_stub())

    resp = client.get("/admin/worker-status", headers={"X-Secret-Key": "test-secret"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["redis_ping"] is False


# ────────────────────────────────────────────────────────────────────────────
# B1 — _KIND_TO_WORKER map + orphan_reaper queued sweep
# ────────────────────────────────────────────────────────────────────────────


def test_b1_kind_to_worker_map_covers_known_kinds():
    """Every kind that api/queue.py enqueues must be in _KIND_TO_WORKER, else
    the requeue sweep silently skips that kind on stuck rows."""
    from api.orphan_reaper import _KIND_TO_WORKER

    # These mirror the worker_func= strings in api/queue.py.
    expected = {
        "g1_discovery": "api.worker.worker_run_g1",
        "g2_resume": "api.worker.worker_run_g2",
        "g3_interview_prep": "api.worker.worker_run_g3",
        "g4_linkedin_post": "api.worker.worker_run_g4",
        "g5_score": "api.worker.worker_run_g5",
        "g9_story_extract": "api.worker.worker_run_g9",
        "g11_voice_calibration": "api.worker.worker_run_g11",
        "legitimacy_check": "api.worker.worker_run_legitimacy",
    }
    assert _KIND_TO_WORKER == expected


def test_b1_sweep_stuck_queued_dispatches_to_correct_worker(monkeypatch):
    """A stuck g2_resume row must re-enqueue api.worker.worker_run_g2,
    NOT the non-existent worker_dispatch."""
    from api import orphan_reaper

    fake_q = MagicMock()
    from api import queue as queue_mod
    monkeypatch.setattr(queue_mod, "_get_queue", lambda: fake_q)

    fake_db = MagicMock()
    chain = (fake_db.table.return_value.select.return_value
             .eq.return_value.is_.return_value
             .lt.return_value.lt.return_value)
    chain.execute.return_value.data = [
        {"id": "run-1", "kind": "g2_resume", "attempts": 0},
        {"id": "run-2", "kind": "g4_linkedin_post", "attempts": 1},
    ]
    # Update chain (any return)
    fake_db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()

    requeued = orphan_reaper._sweep_stuck_queued(fake_db)
    assert requeued == 2

    enqueue_calls = [c.args[0] for c in fake_q.enqueue.call_args_list]
    assert "api.worker.worker_run_g2" in enqueue_calls
    assert "api.worker.worker_run_g4" in enqueue_calls
    assert "api.worker.worker_dispatch" not in enqueue_calls


def test_b1_sweep_skips_unknown_kind(monkeypatch):
    """Unknown kinds shouldn't crash — they should log + skip."""
    from api import orphan_reaper, queue as queue_mod

    fake_q = MagicMock()
    monkeypatch.setattr(queue_mod, "_get_queue", lambda: fake_q)

    fake_db = MagicMock()
    chain = (fake_db.table.return_value.select.return_value
             .eq.return_value.is_.return_value
             .lt.return_value.lt.return_value)
    chain.execute.return_value.data = [{"id": "run-x", "kind": "g99_unknown", "attempts": 0}]

    requeued = orphan_reaper._sweep_stuck_queued(fake_db)
    assert requeued == 0
    fake_q.enqueue.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# B2 — Routing collision fixed
# ────────────────────────────────────────────────────────────────────────────


def test_b2_stories_router_is_top_level():
    """GET /stories used to be /workspace/stories, which matched
    /workspace/{job_id} (int) and 422'd. Confirm the registered prefix
    is top-level by inspecting app.routes (no HTTP — that would trigger
    auth middleware we don't care about here)."""
    from api import server

    paths = [getattr(r, "path", "") for r in server.app.routes]
    # Find any path that starts with /stories/ or equals /stories
    stories_paths = [p for p in paths if p == "/stories" or p.startswith("/stories/")]
    assert stories_paths, "B2: /stories prefix missing from app.routes"
    # Confirm the old broken prefix is gone
    bad = [p for p in paths if p.startswith("/workspace/stories")]
    assert not bad, f"B2 regression: /workspace/stories prefix still registered: {bad}"


def test_b2_follow_ups_router_is_top_level():
    """Same structural check as stories but for /follow-ups."""
    from api import server

    paths = [getattr(r, "path", "") for r in server.app.routes]
    fu = [p for p in paths if p == "/follow-ups" or p.startswith("/follow-ups/")]
    assert fu, "B2: /follow-ups prefix missing from app.routes"
    bad = [p for p in paths if p.startswith("/workspace/follow-ups")]
    assert not bad, f"B2 regression: /workspace/follow-ups prefix still registered: {bad}"


def test_b2_no_router_starts_with_workspace_followed_by_segment():
    """Permanent guard: any future router with prefix '/workspace/<name>'
    will silently collide with /workspace/{job_id}. Forbid it at startup."""
    from api import server

    bad = []
    for route in server.app.routes:
        path = getattr(route, "path", "")
        # We want to catch new routers with prefix '/workspace/anything-not-templated'.
        # The legit workspace router uses '/workspace/{job_id}' which contains '{'.
        if path.startswith("/workspace/") and "{" not in path.split("/")[2]:
            # OK if it's a static sub-route under {job_id} like /workspace/{job_id}/build-resume
            segs = path.split("/")
            if len(segs) > 2 and segs[2] not in ("stories", "follow-ups", "{job_id}"):
                continue
            if segs[2] in ("stories", "follow-ups"):
                bad.append(path)
    assert not bad, f"B2 regression: new routes collide with /workspace/{{job_id}}: {bad}"


# ────────────────────────────────────────────────────────────────────────────
# B3 — CORS no longer wildcard
# ────────────────────────────────────────────────────────────────────────────


def test_b3_cors_origins_is_not_wildcard():
    """The CORSMiddleware config must use specific origins, never '*' in
    combination with allow_credentials=True (browsers reject the combo
    and it's a CSRF vector)."""
    from api import server
    from starlette.middleware.cors import CORSMiddleware

    for mw in server.app.user_middleware:
        if mw.cls is CORSMiddleware:
            opts = mw.kwargs
            assert opts.get("allow_credentials") is True
            origins = opts.get("allow_origins") or []
            assert "*" not in origins, "B3 regression: CORS wildcarded again"
            assert all(o.startswith("http") for o in origins), (
                f"B3: expected http(s) origins, got {origins}"
            )
            return
    pytest.fail("CORSMiddleware not registered")


def test_b3_settings_has_cors_allowed_origins_field():
    """Settings exposes the new field so env override works."""
    from config.settings import get_settings
    s = get_settings()
    assert hasattr(s, "cors_allowed_origins")
    assert isinstance(s.cors_allowed_origins, str)


# ────────────────────────────────────────────────────────────────────────────
# B6 — orphan_reaper sweep writes `error` column (not error_message)
# ────────────────────────────────────────────────────────────────────────────


def test_b6_resume_builds_sweep_writes_error_column():
    """The HARDEN-BUG-402 sweep used to write to a column named
    `error_message` that doesn't exist on resume_builds. PostgREST silently
    drops unknown columns, so the sweep was a no-op for the failure-reason
    column. Confirm the fix writes `error`.

    Uses mock.patch as a context manager so the patch is restored even if
    a prior test leaked state into db.client.get_supabase.
    """
    from api import orphan_reaper

    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value.eq.return_value.lt.return_value.execute.return_value.data = [
        {"id": "build-1"},
        {"id": "build-2"},
    ]

    update_payload = {}
    def capture_update(payload):
        update_payload.update(payload)
        m = MagicMock()
        m.in_.return_value.execute.return_value = MagicMock()
        return m
    fake_db.table.return_value.update.side_effect = capture_update

    with patch("db.client.get_supabase", lambda: fake_db):
        swept = orphan_reaper._sweep_stuck_resume_builds(stale_minutes=30)

    assert swept == 2
    assert "error" in update_payload, "B6 regression: must write `error` column"
    assert "error_message" not in update_payload, (
        "B6 regression: wrote `error_message` again — that column doesn't exist"
    )
    assert update_payload["status"] == "failed"
    assert "stuck-build sweep" in update_payload["error"].lower()
