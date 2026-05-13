"""
Regression tests for the new admin + lookup endpoints shipped 2026-05-13:
  - /admin/scheduler-status (BUG-053)
  - /applications/by-job/{job_id} (E3 / BUG-058)

Both endpoints are gated by verify_secret (single-user-mode admin pattern).
No live Supabase or APScheduler — supabase is monkey-patched and the
scheduler is replaced on app.state.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

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
    """FastAPI TestClient against the real app. Skips the scheduler
    startup hook by setting _scheduler_started=True before the lifespan
    fires (we test the endpoint surface, not the actual cron)."""
    from fastapi.testclient import TestClient
    from api import server

    # Prevent the real start_scheduler_background() from being called
    # during TestClient lifespan startup.
    server._scheduler_started = True
    return TestClient(server.app)


# ── /admin/scheduler-status ────────────────────────────────────────────


def test_scheduler_status_returns_jobs_when_scheduler_present(client, monkeypatch):
    """Endpoint returns running=true + the job list when scheduler is set."""
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

    resp = client.get(
        "/admin/scheduler-status",
        headers={"x-secret-key": server.settings.secret_key},
    )
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

    resp = client.get(
        "/admin/scheduler-status",
        headers={"x-secret-key": server.settings.secret_key},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["running"] is False
    assert data["jobs"] == []


def test_scheduler_status_rejects_without_secret(client):
    """No x-secret-key header → 401/403 from verify_secret."""
    resp = client.get("/admin/scheduler-status")
    # verify_secret raises HTTPException(401) on missing header in the
    # current implementation; older shims used 403. Accept either.
    assert resp.status_code in (401, 403), resp.text


# ── /applications/by-job/{job_id} ──────────────────────────────────────


def test_applications_by_job_returns_uuid_when_row_exists(client, monkeypatch):
    """Happy path: job_id with an applications row → 200 + the row dict
    containing applications.id (UUID)."""
    from api import server

    fake_row = {
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "job_id": 1022,
        "status": "applied",
        "created_at": "2026-05-08T12:00:00Z",
    }

    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[fake_row]
    )

    monkeypatch.setattr(server, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get(
        "/applications/by-job/1022",
        headers={"x-secret-key": server.settings.secret_key},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert body["job_id"] == 1022


def test_applications_by_job_returns_404_when_no_row(client, monkeypatch):
    """No applications row for this job → 404 with `no_application_for_job`."""
    from api import server

    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[]
    )

    monkeypatch.setattr(server, "get_supabase", lambda: fake_db, raising=False)

    resp = client.get(
        "/applications/by-job/9999",
        headers={"x-secret-key": server.settings.secret_key},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no_application_for_job"


def test_applications_by_job_rejects_without_secret(client):
    """No x-secret-key → 401/403 from verify_secret gate."""
    resp = client.get("/applications/by-job/1022")
    assert resp.status_code in (401, 403)
