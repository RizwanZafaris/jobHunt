"""
Phase 1, finding 5 — TWO-TENANT isolation (behavioural, app-level).

Where the static guardrail (tests/test_tenant_scoping_guardrail.py) proves the
``.eq("user_id")`` predicate is *present* in source, this test proves it actually
*isolates* at runtime: it drives the real FastAPI handlers (via TestClient) with
two different authenticated users against a Supabase fake that genuinely HONORS
the query filters, and asserts each tenant sees only its own rows.

The fake DB is the crux: unlike the recorder in test_endpoint_user_scoping.py
(which records ``.eq`` calls but returns every row), ``FilteringFakeClient`` here
applies the eq / gte / in_ / is_ predicates to a row store. So if a handler ever
dropped its ``.eq("user_id", ...)``, this fake would return the *other* tenant's
rows and the assertion would fail — i.e. the test can actually catch a leak, it's
not vacuous.

Auth: ``dependency_overrides[get_current_user]`` is flipped between user A and
user B per call (mirrors how multi-tenant JWT mode resolves a different User per
request). Single-user mode is irrelevant here because the override replaces the
resolver entirely.

Covered:
  * GET /jobs          — A sees only A's jobs;          B sees only B's.
  * GET /companies     — A sees only A's companies;     B sees only B's.
  * GET /applications  — A sees only A's applications;  B sees only B's.
  * POST /applications cross-tenant attach — B posting A's job_id → 404, and no
    row is inserted (the load-bearing cross-tenant write guard).
  * GET /jobs/{id} cross-tenant fetch — B fetching A's job id → 404.
"""
from __future__ import annotations

import copy
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

USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


# ── A Supabase fake that actually FILTERS (so a leak would be observable) ─────


class _FilteringQuery:
    """Chainable query that records predicates and applies them on execute().

    Supports the builder surface the jobs/companies/applications endpoints use:
    select, eq, neq, gte, lte, gt, lt, in_, is_, order, limit, single, insert,
    update. Filters are AND-combined and applied to the parent store's rows for
    the bound table at execute() time. .is_(col, "null") matches rows where col
    is None (PostgREST semantics for IS NULL)."""

    def __init__(self, client, table):
        self._client = client
        self._table = table
        self._filters = []          # list of (op, col, value)
        self._limit = None
        self._insert_payload = None
        self._update_payload = None

    # — filter ops —
    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def gte(self, col, val):
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col, val):
        self._filters.append(("lte", col, val))
        return self

    def gt(self, col, val):
        self._filters.append(("gt", col, val))
        return self

    def lt(self, col, val):
        self._filters.append(("lt", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    # — passthrough builder ops —
    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def single(self):
        return self

    @property
    def not_(self):
        # supports `.not_.is_(...)`; treat as a no-op filter wrapper
        return self

    # — writes —
    def insert(self, payload):
        self._insert_payload = payload
        self._client.insert_calls.append((self._table, payload))
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._insert_payload = payload
        self._client.insert_calls.append((self._table, payload))
        return self

    # — terminal —
    def _matches(self, row) -> bool:
        for op, col, val in self._filters:
            cur = row.get(col)
            if op == "eq" and cur != val:
                return False
            if op == "neq" and cur == val:
                return False
            if op == "in" and cur not in val:
                return False
            if op == "is":
                # PostgREST: .is_(col, "null") → IS NULL
                want_null = val in ("null", None)
                if want_null and cur is not None:
                    return False
                if not want_null and cur != val:
                    return False
            if op == "gte" and not (cur is not None and cur >= val):
                return False
            if op == "lte" and not (cur is not None and cur <= val):
                return False
            if op == "gt" and not (cur is not None and cur > val):
                return False
            if op == "lt" and not (cur is not None and cur < val):
                return False
        return True

    def execute(self):
        # Writes
        if self._insert_payload is not None:
            payloads = (
                self._insert_payload
                if isinstance(self._insert_payload, list)
                else [self._insert_payload]
            )
            stored = []
            for p in payloads:
                row = copy.deepcopy(p)
                # mimic DB-assigned id when the writer didn't supply one
                row.setdefault("id", f"{self._table}-{len(self._client.rows_by_table.get(self._table, []))+1}")
                self._client.rows_by_table.setdefault(self._table, []).append(row)
                stored.append(copy.deepcopy(row))
            return MagicMock(data=stored)
        if self._update_payload is not None:
            updated = []
            for row in self._client.rows_by_table.get(self._table, []):
                if self._matches(row):
                    row.update(self._update_payload)
                    updated.append(copy.deepcopy(row))
            return MagicMock(data=updated)
        # Reads — apply filters
        rows = [
            copy.deepcopy(r)
            for r in self._client.rows_by_table.get(self._table, [])
            if self._matches(r)
        ]
        if self._limit is not None:
            rows = rows[: self._limit]
        return MagicMock(data=rows)


class FilteringFakeClient:
    def __init__(self, rows_by_table=None):
        self.rows_by_table = {k: list(v) for k, v in (rows_by_table or {}).items()}
        self.insert_calls = []

    def table(self, name):
        return _FilteringQuery(self, name)

    def rpc(self, *a, **k):  # pragma: no cover - not exercised here
        return _FilteringQuery(self, "_rpc")


def _make_user(uid: str):
    from api.users import User

    return User(
        id=UUID(uid),
        email=f"{uid[:8]}@example.com",
        full_name=f"User {uid[:4]}",
        plan="free",
        is_admin=False,
        created_at=datetime.now(timezone.utc),
    )


# Two jobs (one per tenant), two companies, two applications.
def _seed_rows():
    return {
        "jobs": [
            {"id": 1, "title": "PM A", "company": "Acme", "location": "Dubai",
             "match_score": 90, "status": "new", "url": "https://a/1",
             "discovered_at": "2026-05-01", "archetype": None,
             "legitimacy_tier": None, "resume_generated_at": None,
             "posting_closed_at": None, "validation_failed": None,
             "letter_grade": None, "fit_score_breakdown": None,
             "user_id": USER_A},
            {"id": 2, "title": "PM B", "company": "Beta", "location": "London",
             "match_score": 88, "status": "new", "url": "https://b/2",
             "discovered_at": "2026-05-02", "archetype": None,
             "legitimacy_tier": None, "resume_generated_at": None,
             "posting_closed_at": None, "validation_failed": None,
             "letter_grade": None, "fit_score_breakdown": None,
             "user_id": USER_B},
        ],
        "companies": [
            {"id": "c-a", "name": "Acme", "is_phantom": False,
             "is_target": True, "priority": "high", "user_id": USER_A},
            {"id": "c-b", "name": "Beta", "is_phantom": False,
             "is_target": True, "priority": "high", "user_id": USER_B},
        ],
        "applications": [
            {"id": "app-a", "job_id": 1, "status": "applied",
             "created_at": "2026-05-03", "user_id": USER_A},
            {"id": "app-b", "job_id": 2, "status": "applied",
             "created_at": "2026-05-04", "user_id": USER_B},
        ],
    }


@pytest.fixture
def server_mod(monkeypatch):
    from api import server

    server._scheduler_started = True
    yield server
    server.app.dependency_overrides.clear()


def _as_user(server, uid: str):
    """Point get_current_user at `uid` for subsequent TestClient calls."""
    from api.context import get_current_user

    server.app.dependency_overrides[get_current_user] = lambda: _make_user(uid)


def _client(server):
    from fastapi.testclient import TestClient

    return TestClient(server.app)


# ── GET /jobs ────────────────────────────────────────────────────────────────


def test_jobs_isolated_between_tenants(server_mod, monkeypatch):
    server = server_mod
    rec = FilteringFakeClient(_seed_rows())
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    _as_user(server, USER_A)
    a = _client(server).get("/jobs")
    assert a.status_code == 200, a.text
    a_jobs = a.json()["jobs"]
    assert {j["id"] for j in a_jobs} == {1}, "A must see only A's job"
    assert all(j["user_id"] == USER_A for j in a_jobs)

    _as_user(server, USER_B)
    b = _client(server).get("/jobs")
    assert b.status_code == 200, b.text
    b_jobs = b.json()["jobs"]
    assert {j["id"] for j in b_jobs} == {2}, "B must see only B's job"
    assert all(j["user_id"] == USER_B for j in b_jobs)


# ── GET /companies ───────────────────────────────────────────────────────────


def test_companies_isolated_between_tenants(server_mod, monkeypatch):
    server = server_mod
    rec = FilteringFakeClient(_seed_rows())
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    _as_user(server, USER_A)
    a = _client(server).get("/companies").json()["companies"]
    assert {c["id"] for c in a} == {"c-a"}, "A sees only A's company"

    _as_user(server, USER_B)
    b = _client(server).get("/companies").json()["companies"]
    assert {c["id"] for c in b} == {"c-b"}, "B sees only B's company"


# ── GET /applications ────────────────────────────────────────────────────────


def test_applications_isolated_between_tenants(server_mod, monkeypatch):
    server = server_mod
    rec = FilteringFakeClient(_seed_rows())
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    _as_user(server, USER_A)
    a = _client(server).get("/applications").json()
    assert {x["id"] for x in a["applications"]} == {"app-a"}
    # the jobs enrichment join must not leak B's job either
    for x in a["applications"]:
        if x.get("job"):
            assert x["job"]["id"] == 1

    _as_user(server, USER_B)
    b = _client(server).get("/applications").json()
    assert {x["id"] for x in b["applications"]} == {"app-b"}
    for x in b["applications"]:
        if x.get("job"):
            assert x["job"]["id"] == 2


# ── POST /applications cross-tenant attach guard ─────────────────────────────


def test_cross_tenant_application_attach_404s(server_mod, monkeypatch):
    """B tries to attach an application to A's job (job_id=1) → 404, no insert."""
    server = server_mod
    rec = FilteringFakeClient(_seed_rows())
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    _as_user(server, USER_B)
    resp = _client(server).post("/applications", json={"job_id": 1})
    assert resp.status_code == 404, resp.text
    # nothing inserted into applications
    assert not [p for (t, p) in rec.insert_calls if t == "applications"], (
        "a cross-tenant attach must not insert any application row"
    )


def test_same_tenant_application_attach_succeeds(server_mod, monkeypatch):
    """Control: B attaching to B's OWN job (job_id=2) succeeds and stamps B."""
    server = server_mod
    rec = FilteringFakeClient(_seed_rows())
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    _as_user(server, USER_B)
    resp = _client(server).post("/applications", json={"job_id": 2})
    assert resp.status_code == 200, resp.text
    inserts = [p for (t, p) in rec.insert_calls if t == "applications"]
    assert inserts and inserts[0].get("user_id") == USER_B, (
        "own-tenant attach must insert a row carrying the caller's user_id"
    )


# ── GET /jobs/{id} cross-tenant fetch guard ──────────────────────────────────


def test_cross_tenant_job_fetch_404s(server_mod, monkeypatch):
    """B fetching A's job by id → 404 (scoped single-row read)."""
    server = server_mod
    rec = FilteringFakeClient(_seed_rows())
    monkeypatch.setattr(server, "get_supabase", lambda: rec, raising=False)

    _as_user(server, USER_B)
    assert _client(server).get("/jobs/1").status_code == 404  # A's job
    _as_user(server, USER_A)
    assert _client(server).get("/jobs/1").status_code == 200  # own job
