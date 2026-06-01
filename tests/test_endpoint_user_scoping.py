"""
Phase-1 finding 2 (feat/endpoint-user-scoping) — targeted tenant-scoping tests.

Proves that representative tenant-table endpoints on api/server.py now filter
their DB queries by the authenticated user's id. We mock the Supabase client
with a chainable recorder and assert that ``.eq("user_id", <caller-id>)`` is
applied (the load-bearing tenant guarantee — we never rely on RLS alone).

Auth is supplied via FastAPI's ``dependency_overrides[get_current_user]`` so we
resolve a known user without touching the (mocked) Supabase user table. This
mirrors single-user mode, where get_current_user returns the seed owner and
every existing row already carries that user_id — so the added predicate is a
no-op on today's data.

Covered:
  - GET  /jobs           filters jobs by user_id
  - GET  /applications   filters applications by user_id (and the jobs join too)
  - POST /applications   scopes the pre-insert job lookup by user_id AND sets
                         user_id on the inserted row (cross-tenant attach guard)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
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

TENANT_ID = "33333333-3333-3333-3333-333333333333"


# ── chainable recorder ──────────────────────────────────────────────────────


class _RecordingQuery:
    """Chainable Supabase query stub that records .eq() and .insert() calls.

    Every builder method (select/eq/gte/order/limit/is_/lt/in_/not_/...) returns
    self so the fluent chain keeps working; .execute() returns canned rows for
    the current table. .eq() / .insert() payloads are appended to shared lists
    on the parent client so tests can assert on them.
    """

    def __init__(self, client, table):
        self._client = client
        self._table = table

    def eq(self, col, val):
        self._client.eq_calls.append((self._table, col, val))
        return self

    def insert(self, payload):
        self._client.insert_calls.append((self._table, payload))
        return self

    def __getattr__(self, name):
        # Any other builder call (select, gte, order, limit, is_, lt, in_, not_,
        # update, upsert, single, ...) just returns self.
        if name.startswith("__"):
            raise AttributeError(name)

        def _builder(*args, **kwargs):
            return self

        return _builder

    @property
    def not_(self):
        return self

    def execute(self):
        return MagicMock(data=list(self._client.rows_by_table.get(self._table, [])))


class _RecordingClient:
    def __init__(self, rows_by_table=None):
        self.rows_by_table = rows_by_table or {}
        self.eq_calls: list[tuple] = []
        self.insert_calls: list[tuple] = []

    def table(self, name):
        return _RecordingQuery(self, name)

    def rpc(self, *args, **kwargs):  # pragma: no cover - not used here
        return _RecordingQuery(self, "_rpc")


def _make_user(is_admin: bool = False):
    from api.users import User

    return User(
        id=UUID(TENANT_ID),
        email="tenant@example.com",
        full_name="Tenant User",
        plan="free",
        is_admin=is_admin,
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def client_and_db():
    """TestClient with get_current_user overridden + a recording Supabase stub.

    Returns (TestClient, recording_client). The recording client is installed
    by monkeypatching ``server.get_supabase`` for each test via the helper.
    """
    from fastapi.testclient import TestClient
    from api import server
    from api.context import get_current_user

    server._scheduler_started = True
    server.app.dependency_overrides[get_current_user] = lambda: _make_user()
    yield server
    server.app.dependency_overrides.clear()


def _user_id_eqs(rec: _RecordingClient, table: str) -> list:
    return [c for c in rec.eq_calls if c[0] == table and c[1] == "user_id"]


# ── GET /jobs ───────────────────────────────────────────────────────────────


def test_list_jobs_filters_by_user_id(client_and_db, monkeypatch):
    server = client_and_db
    rec = _RecordingClient(
        rows_by_table={
            "jobs": [
                {"id": 1, "title": "PM", "company": "Acme", "match_score": 90,
                 "status": "new", "url": "https://x", "user_id": TENANT_ID},
            ]
        }
    )
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    resp = TestClient_get(server, "/jobs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1
    # The load-bearing guarantee: jobs query filtered by the caller's user_id.
    eqs = _user_id_eqs(rec, "jobs")
    assert eqs, f"expected an .eq('user_id', ...) on jobs; got {rec.eq_calls}"
    assert eqs[0] == ("jobs", "user_id", TENANT_ID)


# ── GET /applications ────────────────────────────────────────────────────────


def test_list_applications_filters_by_user_id(client_and_db, monkeypatch):
    server = client_and_db
    rec = _RecordingClient(
        rows_by_table={
            "applications": [
                {"id": "a1", "job_id": 1, "status": "applied", "user_id": TENANT_ID},
            ],
            "jobs": [
                {"id": 1, "title": "PM", "company": "Acme", "location": "Dubai",
                 "match_score": 90, "url": "https://x"},
            ],
        }
    )
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    resp = TestClient_get(server, "/applications")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    # applications list scoped by user_id ...
    assert _user_id_eqs(rec, "applications"), (
        f"expected .eq('user_id') on applications; got {rec.eq_calls}"
    )
    # ... and the enrichment join on jobs is ALSO tenant-scoped.
    assert _user_id_eqs(rec, "jobs"), (
        f"expected .eq('user_id') on the jobs join; got {rec.eq_calls}"
    )
    assert ("applications", "user_id", TENANT_ID) in rec.eq_calls
    assert ("jobs", "user_id", TENANT_ID) in rec.eq_calls


# ── POST /applications (pre-insert lookup + insert scoping) ───────────────────


def test_create_application_scopes_job_lookup_and_sets_user_id(client_and_db, monkeypatch):
    """A 2nd tenant must not be able to attach an application to a 1st tenant's
    job: the job lookup is scoped by user_id, and the inserted row carries the
    caller's user_id."""
    server = client_and_db
    rec = _RecordingClient(
        rows_by_table={
            "jobs": [
                {"id": 7, "company": "Acme", "title": "PM", "match_score": 80,
                 "resume_path": None, "email_path": None, "interview_path": None,
                 "company_id": None, "user_id": TENANT_ID},
            ],
            "applications": [
                {"id": "new-app", "job_id": 7, "status": "evaluated",
                 "user_id": TENANT_ID},
            ],
        }
    )
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    resp = TestClient_post(server, "/applications", json={"job_id": 7})
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] is True

    # 1. The pre-insert job lookup was scoped by user_id (cross-tenant guard).
    assert ("jobs", "user_id", TENANT_ID) in rec.eq_calls, (
        f"job lookup not scoped by user_id; got {rec.eq_calls}"
    )
    # 2. The inserted applications row carries the caller's user_id.
    inserts = [p for (t, p) in rec.insert_calls if t == "applications"]
    assert inserts, f"expected an insert into applications; got {rec.insert_calls}"
    assert inserts[0].get("user_id") == TENANT_ID


def test_create_application_404_when_job_not_owned(client_and_db, monkeypatch):
    """If the job id isn't the caller's (scoped lookup returns no rows) → 404,
    so a cross-tenant attach attempt fails closed instead of inserting."""
    server = client_and_db
    rec = _RecordingClient(rows_by_table={"jobs": []})  # lookup finds nothing
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    resp = TestClient_post(server, "/applications", json={"job_id": 999})
    assert resp.status_code == 404, resp.text
    # Nothing was inserted.
    assert not [p for (t, p) in rec.insert_calls if t == "applications"]


# ── tiny TestClient helpers (build client lazily so overrides are in place) ──


def TestClient_get(server, path):
    from fastapi.testclient import TestClient

    return TestClient(server.app).get(path)


def TestClient_post(server, path, json=None):
    from fastapi.testclient import TestClient

    return TestClient(server.app).post(path, json=json)
