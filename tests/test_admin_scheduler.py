"""
Regression tests for the admin + lookup endpoints shipped 2026-05-13:
  - /admin/scheduler-status (BUG-053)
  - /applications/by-job/{job_id} (E3 / BUG-058)

Auth model after Phase-1 finding 2 (feat/endpoint-user-scoping):
  - /admin/scheduler-status is gated by ``require_admin`` (was verify_secret).
    In single-user mode the seed owner (user_001) is admin, so a request
    resolves to that admin user and succeeds without any header. In
    multi-tenant mode a non-admin / unauthenticated caller is rejected.
  - /applications/by-job/{job_id} is gated by ``get_current_user`` and now
    filters the applications query by user_id (was verify_secret, no scope).
    In single-user mode it resolves to the seed user; the row lookup is
    scoped by ``.eq("user_id", ...)`` AND ``.eq("job_id", ...)``.

No live Supabase or APScheduler — supabase is monkey-patched, the user
resolver is patched to the seed user, and the scheduler is replaced on
app.state.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock
from uuid import UUID

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SECRET_KEY", "test-secret")

from api.users import RIZWAN_USER_ID, User  # noqa: E402

SEED_ID = str(RIZWAN_USER_ID)


def _seed_admin() -> User:
    """A real (admin) User row standing in for the single-user-mode owner."""
    return User(
        id=RIZWAN_USER_ID,
        email="rizwanzaffar.pk@gmail.com",
        full_name="Rizwan Zafar",
        plan="lifetime",
        is_admin=True,
        created_at="2026-05-29T00:00:00Z",
    )


@pytest.fixture(autouse=True)
def _single_user_admin(monkeypatch):
    """Run every test in single-user mode with the seed owner resolved.

    Patching ``context._ensure_rizwan`` keeps ``get_current_user`` (and thus
    ``require_admin``) from touching the mocked Supabase user table.
    """
    from api import context as context_mod

    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    monkeypatch.setattr(context_mod, "_ensure_rizwan", _seed_admin)
    yield


@pytest.fixture
def client():
    """FastAPI TestClient against the real app. Skips the scheduler
    startup hook by setting _scheduler_started=True before the lifespan
    fires (we test the endpoint surface, not the actual cron)."""
    from fastapi.testclient import TestClient
    from api import server

    # Prevent the real start_scheduler_background() from being called
    # during TestClient lifespan startup.
    server._scheduler_started = True
    return TestClient(server.app)


# ── /admin/scheduler-status (require_admin) ──────────────────────────────


def test_scheduler_status_returns_jobs_when_scheduler_present(client, monkeypatch):
    """Endpoint returns running=true + the job list when scheduler is set.

    Admin gate passes because single-user mode resolves the seed owner, who
    is admin."""
    from api import server

    # Build a fake scheduler with one job.
    fake_job = MagicMock()
    fake_job.id = "job_scout"
    fake_job.name = "Daily Job Scout"
    fake_job.next_run_time = MagicMock()
    fake_job.next_run_time.isoformat = lambda: "2026-05-14T09:00:00+04:00"
    fake_job.trigger = "cron[hour='9', minute='0']"

    fake_scheduler = MagicMock()
    fake_scheduler.state = 1  # STATE_RUNNING
    fake_scheduler.get_jobs.return_value = [fake_job]

    server.app.state.scheduler = fake_scheduler

    resp = client.get("/admin/scheduler-status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["running"] is True
    assert data["job_count"] == 1
    assert data["jobs"][0]["id"] == "job_scout"
    assert data["jobs"][0]["next_run_time"] == "2026-05-14T09:00:00+04:00"


def test_scheduler_status_handles_no_scheduler(client):
    """If app.state.scheduler is None, return running=false gracefully."""
    from api import server

    # Clear the scheduler attr (simulates the startup hook failing).
    if hasattr(server.app.state, "scheduler"):
        delattr(server.app.state, "scheduler")

    resp = client.get("/admin/scheduler-status")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["running"] is False
    assert data["jobs"] == []


def test_scheduler_status_rejects_non_admin_in_multitenant(client, monkeypatch):
    """In multi-tenant mode a non-admin caller is rejected by require_admin.

    We flip out of single-user mode and resolve a non-admin user via the
    service-secret branch of get_current_user (bare X-Secret-Key, no bearer
    token). require_admin then returns 403.
    """
    from api import context as context_mod

    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "0")
    monkeypatch.setenv("SECRET_KEY", "real-secret")

    non_admin = User(
        id=UUID("22222222-2222-2222-2222-222222222222"),
        email="someone@example.com",
        full_name="Not Admin",
        plan="free",
        is_admin=False,
        created_at="2026-05-29T00:00:00Z",
    )
    # The service-secret branch of get_current_user resolves to
    # _ensure_rizwan(); patch it to a NON-admin so require_admin's 403 path is
    # exercised. Clear the admin allowlist so the env overlay doesn't
    # re-promote the id.
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)
    monkeypatch.setattr(context_mod, "_ensure_rizwan", lambda: non_admin)

    resp = client.get(
        "/admin/scheduler-status",
        headers={"x-secret-key": "real-secret"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "admin_required"


# ── /applications/by-job/{job_id} (get_current_user + user_id scope) ──────


def test_applications_by_job_returns_uuid_when_row_exists(client, monkeypatch):
    """Happy path: job_id with an applications row → 200 + the row dict
    containing applications.id (UUID). The query is scoped by user_id."""
    from api import server

    fake_row = {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "job_id": 1022,
        "status": "applied",
        "created_at": "2026-05-08T12:00:00Z",
    }

    captured: dict = {}

    chain = MagicMock()

    def _eq(col, val):
        captured.setdefault("eqs", []).append((col, val))
        return chain

    chain.eq.side_effect = _eq
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[fake_row])
    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value = chain

    monkeypatch.setattr(server, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get("/applications/by-job/1022")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert body["job_id"] == 1022
    # The load-bearing guarantee: the query filtered by the caller's user_id.
    assert ("user_id", SEED_ID) in captured["eqs"]
    assert ("job_id", 1022) in captured["eqs"]


def test_applications_by_job_returns_404_when_no_row(client, monkeypatch):
    """No applications row for this job → 404 with `no_application_for_job`."""
    from api import server

    chain = MagicMock()
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value = chain

    monkeypatch.setattr(server, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get("/applications/by-job/9999")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no_application_for_job"
