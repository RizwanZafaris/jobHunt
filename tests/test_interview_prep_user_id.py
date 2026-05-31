"""
Regression tests for the G3 interview-prep ``user_id`` persistence bug
(root-caused 2026-06-01).

Root cause
----------
``interview_prep.user_id`` is ``UUID NOT NULL`` with NO default after the
multi-tenancy migration (``db/migrations/2026_05_10_001_multi_tenancy.sql``),
but ``interview_agents.g3_io.create_interview_prep`` never set it. Every
INSERT therefore violated the NOT-NULL constraint, so the G3 graph raised at
its very first node (``entry_node``), NO ``interview_prep`` row was ever
persisted (the live table held 0 rows), and every user-scoped retrieval —
``GET /interview-studio/{application_id}``, the ``GET /jobs/{id}/detail``
artifacts serializer, ``_credit_stories_for_outcome`` — filters
``.eq("user_id", ...)`` and so had nothing to surface. Net effect: "interview
generation is not working".

This is the same class of bug (and the same fix) as
``resume_agents.g2_io.create_resume_build`` (2026-05-12) and the
``upsert_job`` / ``upsert_company`` / ``upsert_rizwan_profile`` writers: set
``user_id`` on the write, defaulting to the seed UUID via env override.

What these tests pin
--------------------
  1. ``create_interview_prep`` ALWAYS writes a non-null ``user_id`` (the
     NOT-NULL contract the old writer violated).
  2. it defaults to the seed UUID (``RIZWAN_USER_ID`` override honoured) when
     no ``user_id`` is supplied.
  3. an explicit ``user_id`` is passed through unchanged.
  4. ``entry_node`` forwards ``state['user_id']`` to the writer.
  5. ``run_g3_graph`` seeds ``initial_state['user_id']`` from the tenant it
     resolves off the application row.
  6. ``InterviewPrepState`` declares ``user_id`` so LangGraph preserves it
     into ``entry_node`` rather than dropping the un-declared key.

No live Supabase and no live LLM: a tiny fake client records the INSERT
payload (mirrors the mock-db style in tests/test_composite_tenancy.py).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest


# Project root on path so `import interview_agents` works, and stub the
# `supabase` package so db.client's top-level import succeeds offline — same
# bootstrap the rest of the suite uses.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

SEED_UUID = "00000000-0000-0000-0000-000000000001"
USER_A = "00000000-0000-0000-0000-00000000000a"


# ── A minimal fake Supabase client that records insert payloads ────────────
class _Exec:
    def __init__(self, data):
        self.data = data


class _InsertBuilder:
    def __init__(self, sink: list, table: str, payload: dict):
        self._sink = sink
        self._table = table
        self._payload = payload

    def execute(self):
        self._sink.append((self._table, dict(self._payload)))
        # supabase-py returns the inserted row(s); echo back with a fake id so
        # create_interview_prep's `if not rows: raise` path is not taken.
        row = dict(self._payload)
        row.setdefault("id", f"fake-{self._table}-{len(self._sink)}")
        return _Exec([row])


class _FakeTable:
    def __init__(self, sink: list, name: str):
        self._sink = sink
        self._name = name

    def insert(self, payload):
        return _InsertBuilder(self._sink, self._name, payload)


class FakeClient:
    def __init__(self):
        self.inserts: list[tuple[str, dict]] = []

    def table(self, name):
        return _FakeTable(self.inserts, name)

    def payload_for(self, table: str) -> list[dict]:
        return [p for (t, p) in self.inserts if t == table]


@pytest.fixture
def fake_db(monkeypatch):
    """Patch db.client.get_supabase to return a recording fake.

    Hardened against suite-wide state leaks: some other test modules (e.g.
    tests/test_g8.py) permanently rebind the real ``db`` package's ``client``
    attribute to a MagicMock via
    ``sys.modules.setdefault("db", MagicMock()).client = MagicMock(...)`` —
    which is never undone, so a later ``from db.client import get_supabase``
    resolves the stale stub instead of the real module. We repair both the
    ``sys.modules['db.client']`` entry and the ``db.client`` package attribute
    to the real module for the duration of this test (monkeypatch restores
    them afterwards), then patch ``get_supabase`` on it. This makes the test
    pass regardless of collection order.
    """
    import importlib

    client = FakeClient()
    db_pkg = importlib.import_module("db")
    dbc = importlib.import_module("db.client")

    monkeypatch.setitem(sys.modules, "db.client", dbc)
    monkeypatch.setattr(db_pkg, "client", dbc, raising=False)
    monkeypatch.setattr(dbc, "get_supabase", lambda: client, raising=True)
    return client


# ══════════════════════════════════════════════════════════════════════════
# 1-4. create_interview_prep writes a non-null user_id
# ══════════════════════════════════════════════════════════════════════════
class TestCreateInterviewPrepUserId:
    def test_defaults_to_seed_user(self, fake_db, monkeypatch):
        monkeypatch.delenv("RIZWAN_USER_ID", raising=False)
        from interview_agents.g3_io import create_interview_prep

        create_interview_prep(
            application_id="app-1",
            job_id=1022,
            company_name="Adyen",
            round_type="hm",
            round_number=1,
        )
        rows = fake_db.payload_for("interview_prep")
        assert len(rows) == 1
        assert rows[0]["user_id"] == SEED_UUID
        # The rest of the row is unchanged (backward compatible).
        assert rows[0]["status"] == "running"
        assert rows[0]["company_name"] == "Adyen"

    def test_honours_rizwan_user_id_env_override(self, fake_db, monkeypatch):
        monkeypatch.setenv("RIZWAN_USER_ID", USER_A)
        from interview_agents.g3_io import create_interview_prep

        create_interview_prep(
            application_id="app-1",
            job_id=1022,
            company_name="Adyen",
            round_type="hm",
        )
        assert fake_db.payload_for("interview_prep")[0]["user_id"] == USER_A

    def test_explicit_user_id_passed_through(self, fake_db, monkeypatch):
        # An explicit arg beats the env default (multi-tenant path).
        monkeypatch.delenv("RIZWAN_USER_ID", raising=False)
        from interview_agents.g3_io import create_interview_prep

        create_interview_prep(
            application_id="app-1",
            job_id=1022,
            company_name="Adyen",
            round_type="hm",
            user_id=USER_A,
        )
        assert fake_db.payload_for("interview_prep")[0]["user_id"] == USER_A

    def test_payload_always_carries_non_null_user_id(self, fake_db, monkeypatch):
        """The NOT-NULL regression pin: no matter the inputs, the INSERT row
        must carry a non-null user_id — the exact constraint the old writer
        violated, leaving interview_prep permanently empty."""
        monkeypatch.delenv("RIZWAN_USER_ID", raising=False)
        from interview_agents.g3_io import create_interview_prep

        create_interview_prep(
            application_id="app-1",
            job_id=None,          # nullable
            company_name="",      # nullable / empty
            round_type="hm",
        )
        row = fake_db.payload_for("interview_prep")[0]
        assert "user_id" in row and row["user_id"], (
            "interview_prep.user_id is NOT NULL — the INSERT payload must "
            "always set it or the row never persists"
        )


# ══════════════════════════════════════════════════════════════════════════
# 5. entry_node forwards the tenant to the writer
# ══════════════════════════════════════════════════════════════════════════
class TestEntryNodeForwardsUserId:
    @pytest.mark.asyncio
    async def test_entry_node_passes_state_user_id_to_create(self, monkeypatch):
        import interview_agents.g3_io as g3_io

        captured: dict = {}

        # entry_node imports these from interview_agents.g3_io at call time, so
        # patching the module attributes is sufficient.
        monkeypatch.setattr(
            g3_io, "load_application",
            lambda aid: {"job_id": 1022, "company": "Adyen", "user_id": USER_A},
        )
        monkeypatch.setattr(
            g3_io, "load_job",
            lambda jid: {"id": jid, "company": "Adyen", "title": "PM"},
        )
        monkeypatch.setattr(g3_io, "load_company_persona", lambda *a, **k: None)
        monkeypatch.setattr(g3_io, "load_story_bank", lambda *a, **k: [])
        monkeypatch.setattr(g3_io, "load_last_resume_build", lambda *a, **k: None)
        monkeypatch.setattr(g3_io, "load_interview_history", lambda *a, **k: [])

        def _fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "ip-1"}

        monkeypatch.setattr(g3_io, "create_interview_prep", _fake_create)

        from interview_agents.g3_nodes import entry_node

        out = await entry_node({
            "application_id": "app-1",
            "user_id": USER_A,
            "round_type": "hm",
            "round_number": 1,
        })
        assert captured.get("user_id") == USER_A
        assert out["interview_prep_id"] == "ip-1"

    @pytest.mark.asyncio
    async def test_entry_node_without_state_user_id_degrades(self, monkeypatch):
        """If state carries no user_id, entry_node forwards None and the
        writer falls back to the seed UUID — never a crash."""
        import interview_agents.g3_io as g3_io

        captured: dict = {}
        monkeypatch.setattr(
            g3_io, "load_application",
            lambda aid: {"job_id": 1022, "company": "Adyen"},
        )
        monkeypatch.setattr(
            g3_io, "load_job",
            lambda jid: {"id": jid, "company": "Adyen", "title": "PM"},
        )
        monkeypatch.setattr(g3_io, "load_company_persona", lambda *a, **k: None)
        monkeypatch.setattr(g3_io, "load_story_bank", lambda *a, **k: [])
        monkeypatch.setattr(g3_io, "load_last_resume_build", lambda *a, **k: None)
        monkeypatch.setattr(g3_io, "load_interview_history", lambda *a, **k: [])

        def _fake_create(**kwargs):
            captured.update(kwargs)
            return {"id": "ip-2"}

        monkeypatch.setattr(g3_io, "create_interview_prep", _fake_create)

        from interview_agents.g3_nodes import entry_node

        await entry_node({
            "application_id": "app-1",
            "round_type": "hm",
        })
        # Forwarded explicitly as None → g3_io applies the seed-UUID default.
        assert "user_id" in captured
        assert captured["user_id"] is None


# ══════════════════════════════════════════════════════════════════════════
# 6. run_g3_graph seeds initial_state['user_id'] from the application row
# ══════════════════════════════════════════════════════════════════════════
class TestRunG3GraphSeedsUserId:
    @pytest.mark.asyncio
    async def test_initial_state_carries_resolved_tenant(self, monkeypatch):
        import interview_agents.g3_run as g3_run
        import interview_agents.g3_io as g3_io

        captured: dict = {}

        class _FakeGraph:
            async def ainvoke(self, initial_state, config=None):
                captured["initial_state"] = dict(initial_state)
                captured["config"] = config
                return dict(initial_state)

        monkeypatch.setattr(g3_run, "_get_graph", lambda: _FakeGraph())
        # Avoid the companies-table lookup in canonicalisation.
        monkeypatch.setattr(g3_run, "_canonicalize_company", lambda name: name)
        monkeypatch.setattr(
            g3_io, "load_application",
            lambda aid: {"user_id": USER_A, "company": "Adyen", "job_id": 1022},
        )

        await g3_run.run_g3_graph(
            application_id="app-1",
            company_name="Adyen",
            max_cost_usd=1.0,
        )
        assert captured["initial_state"]["user_id"] == USER_A
        # The same tenant namespaces the checkpoint thread (Phase 1, Finding 4).
        assert USER_A in captured["config"]["configurable"]["thread_id"]


# ══════════════════════════════════════════════════════════════════════════
# 7. The state schema declares user_id (so LangGraph preserves it)
# ══════════════════════════════════════════════════════════════════════════
def test_interview_prep_state_declares_user_id():
    from interview_agents.g3_state import InterviewPrepState

    assert "user_id" in InterviewPrepState.__annotations__, (
        "InterviewPrepState must declare user_id or LangGraph drops it before "
        "entry_node can forward it to create_interview_prep"
    )
