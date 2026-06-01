"""
DB-1 — composite-key tenancy regression tests.

Context (docs/SCALABILITY.md §3 P0, docs/SCALABILITY_BUILD_PLAN.md PR DB-1):
five tables carried GLOBAL UNIQUE constraints that silently assumed one user
(`companies.name`, `jobs.url`, `company_knowledge(company_name,section)`,
`company_personas.company_name`, `rizwan_profile.section`). With the matching
`on_conflict` upserts targeting the bare column, tenant B upserting the same
company / job url / section OVERWROTE tenant A's row (cross-tenant data
corruption). DB-1 makes each uniqueness composite with `user_id` and points
every upsert's `on_conflict` at the composite key — AND writes `user_id` on
the four writers that didn't set it before.

These tests pin the *contract that prevents the clobber*, using the repo's
mock-db style (see tests/test_phantom_filter.py): a fake Supabase client whose
`.upsert(row, on_conflict=...)` reproduces Postgres ON CONFLICT semantics by
keying stored rows on EXACTLY the columns named in `on_conflict`, read out of
the row payload. That makes the mock sensitive to the two things DB-1 changed:

  1. the on_conflict target string (must include user_id), and
  2. whether user_id is actually present in the written row.

If either regresses, two tenants collapse to one row and the coexistence
tests fail — the same failure the live global UNIQUE produced.

Test groups:
  (a) two distinct user_ids upserting the SAME company name / jobs.url /
      profile section / knowledge section / persona company → BOTH rows
      coexist (fails under the old global unique / a missing user_id).
  (b) on_conflict round-trip — upserting the same (user_id, key) twice
      yields exactly one row, no error.
  (c) a guard proving the mock would CATCH a regression: simulating the old
      single-column conflict target collapses the two tenants to one row.

The live composite UNIQUE indexes + the DROP of the global uniques are
validated separately against a real Postgres (see the PR description's
local-PG validation); these unit tests don't need a DB.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# supabase is import-mocked the same way the rest of the suite does it, so the
# module under test can `from supabase import create_client` without the real
# package / network.
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

USER_A = "00000000-0000-0000-0000-00000000000a"
USER_B = "00000000-0000-0000-0000-00000000000b"


# ── A fake Supabase client that honours ON CONFLICT semantics ──────────────
class FakeTable:
    """Reproduces Postgres upsert: rows are keyed by the on_conflict columns
    (pulled from the row payload). Same key → UPDATE (replace); new key →
    INSERT. A row whose on_conflict columns are all NULL/absent is treated as
    a fresh INSERT every time (mirrors UNIQUE allowing multiple NULLs / the
    partial jobs index)."""

    def __init__(self, store: dict):
        self._store = store  # {(table, keytuple_or_unique): row}
        self._table = None

    def _bind(self, name: str):
        self._table = name
        return self

    def upsert(self, row, on_conflict: str | None = None):
        rows = row if isinstance(row, list) else [row]
        for r in rows:
            if on_conflict:
                cols = [c.strip() for c in on_conflict.split(",")]
                key_vals = tuple(r.get(c) for c in cols)
                # All-NULL conflict key → behaves like a non-conflicting insert
                # (Postgres UNIQUE permits multiple NULLs; the jobs index is
                # partial on url IS NOT NULL). Give it a unique surrogate key.
                if all(v is None for v in key_vals):
                    key = (self._table, "____null____", len(self._store))
                else:
                    key = (self._table, tuple(cols), key_vals)
            else:
                key = (self._table, "____noconflict____", len(self._store))
            self._store[key] = dict(r)
        # supabase-py upsert is chained with .execute(); return a builder that
        # exposes it (and is itself harmless if .execute() is skipped).
        return _UpsertBuilder([dict(r) for r in rows])

    # read chain used by upsert_job's discovered_at lookup + persona lookups
    def select(self, *a, **k):
        return _ReadChain(self._store, self._table)


class _ReadChain:
    def __init__(self, store, table):
        self._store = store
        self._table = table
        self._eq = {}

    def eq(self, col, val):
        self._eq[col] = val
        return self

    def limit(self, n):
        return self

    def single(self):
        return self

    def execute(self):
        # Return any stored rows for this table matching all eq() filters.
        out = []
        for (tbl, _k, _v), r in self._store.items():
            if tbl != self._table:
                continue
            if all(r.get(c) == val for c, val in self._eq.items()):
                out.append(r)
        return _Exec(out)


class _Exec:
    def __init__(self, data):
        self.data = data


class _UpsertBuilder:
    """Return value of FakeTable.upsert — supports the `.execute()` chain that
    supabase-py uses (`db.table(t).upsert(...).execute()`)."""

    def __init__(self, data):
        self._data = data

    def execute(self):
        return _Exec(self._data)


class FakeClient:
    def __init__(self):
        self.store: dict = {}

    def table(self, name):
        return FakeTable(self.store)._bind(name)

    def rows_for(self, table: str) -> list[dict]:
        return [r for (t, _k, _v), r in self.store.items() if t == table]


@pytest.fixture
def fake_db(monkeypatch):
    client = FakeClient()
    import db.client as dbc

    monkeypatch.setattr(dbc, "get_supabase", lambda: client, raising=True)
    # Make embeddings free + offline for the async knowledge/profile writers.

    async def _fake_embed(_text):
        return [0.0] * 8

    monkeypatch.setattr(dbc, "embed", _fake_embed, raising=True)
    return client


# ══════════════════════════════════════════════════════════════════════════
# (a) Two distinct tenants can hold the SAME natural key — rows COEXIST
# ══════════════════════════════════════════════════════════════════════════
def test_two_tenants_same_company_name_coexist(fake_db):
    from db.client import upsert_company

    upsert_company({"name": "Stripe"}, user_id=USER_A)
    upsert_company({"name": "Stripe"}, user_id=USER_B)

    rows = fake_db.rows_for("companies")
    assert len(rows) == 2, "tenant B must NOT clobber tenant A's company row"
    assert {r["user_id"] for r in rows} == {USER_A, USER_B}
    assert all(r["name"] == "Stripe" for r in rows)


def test_two_tenants_same_job_url_coexist(fake_db):
    from db.client import upsert_job

    url = "https://stripe.com/jobs/eng-1"
    upsert_job({"title": "PM", "url": url, "source": "greenhouse"}, user_id=USER_A)
    upsert_job({"title": "PM", "url": url, "source": "greenhouse"}, user_id=USER_B)

    rows = fake_db.rows_for("jobs")
    assert len(rows) == 2, "tenant B must NOT clobber tenant A's job row on a shared url"
    assert {r["user_id"] for r in rows} == {USER_A, USER_B}


@pytest.mark.asyncio
async def test_two_tenants_same_profile_section_coexist(fake_db):
    from db.client import upsert_rizwan_profile

    await upsert_rizwan_profile("summary", "A's summary", user_id=USER_A)
    await upsert_rizwan_profile("summary", "B's summary", user_id=USER_B)

    rows = fake_db.rows_for("rizwan_profile")
    assert len(rows) == 2, "tenant B must NOT clobber tenant A's profile section"
    assert {r["user_id"] for r in rows} == {USER_A, USER_B}
    assert {r["content"] for r in rows} == {"A's summary", "B's summary"}


@pytest.mark.asyncio
async def test_two_tenants_same_knowledge_section_coexist(fake_db):
    from db.client import upsert_company_knowledge

    await upsert_company_knowledge("Stripe", None, "overview", "A text", user_id=USER_A)
    await upsert_company_knowledge("Stripe", None, "overview", "B text", user_id=USER_B)

    rows = fake_db.rows_for("company_knowledge")
    assert len(rows) == 2, "tenant B must NOT clobber tenant A's (company, section) row"
    assert {r["user_id"] for r in rows} == {USER_A, USER_B}
    # the writer that previously set NO user_id must now stamp it
    assert all(r.get("user_id") for r in rows), "user_id must be written on every row"


# ══════════════════════════════════════════════════════════════════════════
# (b) on_conflict round-trip — same (user_id, key) twice → ONE row, no error
# ══════════════════════════════════════════════════════════════════════════
def test_company_round_trip_dedups_within_tenant(fake_db):
    from db.client import upsert_company

    upsert_company({"name": "Stripe", "country": "US"}, user_id=USER_A)
    upsert_company({"name": "Stripe", "country": "IE"}, user_id=USER_A)

    rows = fake_db.rows_for("companies")
    assert len(rows) == 1, "same (user_id, name) upserted twice must collapse to one row"
    assert rows[0]["country"] == "IE", "second upsert should win (UPDATE branch)"


def test_job_round_trip_dedups_within_tenant(fake_db):
    from db.client import upsert_job

    url = "https://stripe.com/jobs/eng-1"
    upsert_job({"title": "PM", "url": url, "source": "greenhouse"}, user_id=USER_A)
    upsert_job({"title": "Senior PM", "url": url, "source": "greenhouse"}, user_id=USER_A)

    rows = fake_db.rows_for("jobs")
    assert len(rows) == 1, "same (user_id, url) upserted twice must collapse to one row"
    assert rows[0]["title"] == "Senior PM"


@pytest.mark.asyncio
async def test_knowledge_round_trip_dedups_within_tenant(fake_db):
    from db.client import upsert_company_knowledge

    await upsert_company_knowledge("Stripe", None, "overview", "v1", user_id=USER_A)
    await upsert_company_knowledge("Stripe", None, "overview", "v2", user_id=USER_A)

    rows = fake_db.rows_for("company_knowledge")
    assert len(rows) == 1, "same (user_id, company, section) must collapse to one row"
    assert rows[0]["content"] == "v2"


# ══════════════════════════════════════════════════════════════════════════
# (c) the on_conflict target IS the composite key (pins the exact change) +
#     proof the mock would CATCH a single-column regression
# ══════════════════════════════════════════════════════════════════════════
def test_upsert_passes_composite_conflict_targets(fake_db, monkeypatch):
    """Capture the on_conflict string each writer hands to supabase and assert
    it leads with user_id. This is the precise edit DB-1 makes; a revert to the
    bare column would fail here."""
    captured: list[str] = []

    real_table = fake_db.table

    def spy_table(name):
        t = real_table(name)
        orig_upsert = t.upsert

        def upsert(row, on_conflict=None):
            captured.append(on_conflict)
            return orig_upsert(row, on_conflict=on_conflict)

        t.upsert = upsert
        return t

    monkeypatch.setattr(fake_db, "table", spy_table)

    from db.client import upsert_company, upsert_job

    upsert_company({"name": "Stripe"}, user_id=USER_A)
    upsert_job({"title": "PM", "url": "https://x/y", "source": "web"}, user_id=USER_A)

    assert "user_id,name" in captured
    assert "user_id,url" in captured
    for tgt in captured:
        assert tgt and tgt.split(",")[0] == "user_id", (
            f"on_conflict {tgt!r} must lead with user_id (composite tenancy)"
        )


def test_old_single_column_conflict_would_clobber(fake_db):
    """Guardrail for the test harness itself: prove the FakeTable reproduces a
    clobber under the OLD behavior. With on_conflict='name' (no user_id), two
    tenants collapse to ONE row — exactly the corruption DB-1 removed. If this
    ever passes with 2 rows, the mock isn't actually enforcing uniqueness and
    the coexistence tests above would be vacuous."""
    db = fake_db
    db.table("companies").upsert({"user_id": USER_A, "name": "Stripe"}, on_conflict="name")
    db.table("companies").upsert({"user_id": USER_B, "name": "Stripe"}, on_conflict="name")
    rows = db.rows_for("companies")
    assert len(rows) == 1, "sanity: old single-column conflict target collapses tenants"
