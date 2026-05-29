"""
Phase 1, Finding 4 — LangGraph checkpoint hygiene tests.

Two invariants, asserted without any live API / Supabase calls:

  1. **Tenant-namespaced thread_id.** Every graph that keys a checkpoint
     thread on a non-tenant id (job_id / application_id) must include the
     owning user_id in the thread_id, so two tenants who share a job_id /
     application_id never collide on the same checkpoint thread (which would
     leak resume/interview state across tenants once those ids stop being
     globally unique). We assert the constructed thread_id contains the
     user_id AND that two different user_ids with the SAME job_id produce
     two DIFFERENT thread_ids.

  2. **Explicit recursion_limit.** Every `ainvoke` must pass an explicit
     `recursion_limit` in its config so a routing bug yields a controlled
     GraphRecursionError instead of silently running to LangGraph's default
     of 25. We mock the graph and inspect the config dict handed to ainvoke.

The graphs are replaced with a fake that records the (initial_state, config)
of the single ainvoke call, so nothing real runs.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest


# Project root on path so `import resume_agents` etc. resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub the `supabase` package so db.client's top-level import succeeds even
# when the package isn't pip-installed locally (mirrors the other suites).
sys.modules.setdefault("supabase", MagicMock())


SEED_UUID = "00000000-0000-0000-0000-000000000001"
USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


class _FakeGraph:
    """Records the config of the single ainvoke call; returns a dummy state."""

    def __init__(self):
        self.last_config = None
        self.last_initial_state = None

    async def ainvoke(self, initial_state, config=None):
        self.last_initial_state = initial_state
        self.last_config = config
        # Echo a minimal "final state" so the caller's post-run logging /
        # dict() conversion doesn't blow up.
        return dict(initial_state)


@pytest.fixture(autouse=True)
def _env():
    # The run functions read settings / env; give them harmless defaults.
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")
    os.environ.setdefault("SUPABASE_URL", "http://test")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
    # Force single-user fallback to the canonical seed UUID so the
    # "no user_id on row" path is deterministic regardless of the host env.
    prev = os.environ.pop("RIZWAN_USER_ID", None)
    yield
    if prev is not None:
        os.environ["RIZWAN_USER_ID"] = prev


# ─── G2 (resume builder) ──────────────────────────────────────────────────
class TestG2ThreadId:
    def _patch(self, monkeypatch, fake, *, job_user_id):
        """Patch g2_io.load_job + g2_run._get_graph."""
        from resume_agents import g2_run, g2_io

        def fake_load_job(job_id, *args, **kwargs):
            return {"id": job_id, "company": "Visa", "user_id": job_user_id}

        monkeypatch.setattr(g2_io, "load_job", fake_load_job)
        monkeypatch.setattr(g2_run, "_get_graph", lambda: fake)
        # Avoid touching _canonicalize_company's live Supabase lookup.
        monkeypatch.setattr(g2_run, "_canonicalize_company", lambda n: n)

    @pytest.mark.asyncio
    async def test_thread_id_includes_user_id(self, monkeypatch):
        from resume_agents.g2_run import run_g2_graph

        fake = _FakeGraph()
        self._patch(monkeypatch, fake, job_user_id=USER_A)
        await run_g2_graph(job_id=4242, company_name="Visa")

        tid = fake.last_config["configurable"]["thread_id"]
        assert USER_A in tid
        assert "4242" in tid
        assert tid == f"g2-{USER_A}-job-4242"

    @pytest.mark.asyncio
    async def test_same_job_id_different_users_differ(self, monkeypatch):
        from resume_agents.g2_run import run_g2_graph

        fake_a = _FakeGraph()
        self._patch(monkeypatch, fake_a, job_user_id=USER_A)
        await run_g2_graph(job_id=999, company_name="Visa")
        tid_a = fake_a.last_config["configurable"]["thread_id"]

        fake_b = _FakeGraph()
        self._patch(monkeypatch, fake_b, job_user_id=USER_B)
        await run_g2_graph(job_id=999, company_name="Visa")
        tid_b = fake_b.last_config["configurable"]["thread_id"]

        # Same job_id, different tenants → MUST NOT collide on one thread.
        assert tid_a != tid_b
        assert "999" in tid_a and "999" in tid_b

    @pytest.mark.asyncio
    async def test_warm_start_thread_is_namespaced_and_distinct(self, monkeypatch):
        from resume_agents.g2_run import run_g2_graph

        fake = _FakeGraph()
        self._patch(monkeypatch, fake, job_user_id=USER_A)
        await run_g2_graph(job_id=7, company_name="Visa", warm_start_md="# CV")
        tid = fake.last_config["configurable"]["thread_id"]
        assert tid == f"g2-{USER_A}-job-7-rebuild"

    @pytest.mark.asyncio
    async def test_falls_back_to_seed_user_when_row_has_no_user_id(self, monkeypatch):
        from resume_agents.g2_run import run_g2_graph

        fake = _FakeGraph()
        self._patch(monkeypatch, fake, job_user_id=None)
        await run_g2_graph(job_id=5, company_name="Visa")
        tid = fake.last_config["configurable"]["thread_id"]
        # Single-user mode: deterministic seed UUID, behaviour unchanged.
        assert tid == f"g2-{SEED_UUID}-job-5"

    @pytest.mark.asyncio
    async def test_recursion_limit_is_explicit(self, monkeypatch):
        from resume_agents.g2_run import run_g2_graph, G2_RECURSION_LIMIT

        fake = _FakeGraph()
        self._patch(monkeypatch, fake, job_user_id=USER_A)
        await run_g2_graph(job_id=1, company_name="Visa")

        assert "recursion_limit" in fake.last_config
        assert fake.last_config["recursion_limit"] == G2_RECURSION_LIMIT
        # Must be a deliberate value above LangGraph's silent default of 25.
        assert fake.last_config["recursion_limit"] > 25


# ─── G3 (interview prep) ──────────────────────────────────────────────────
class TestG3ThreadId:
    def _patch(self, monkeypatch, fake, *, app_user_id):
        from interview_agents import g3_run, g3_io

        def fake_load_application(application_id, *a, **kw):
            return {"id": application_id, "company": "Visa", "user_id": app_user_id}

        monkeypatch.setattr(g3_io, "load_application", fake_load_application)
        monkeypatch.setattr(g3_run, "_get_graph", lambda: fake)
        monkeypatch.setattr(g3_run, "_canonicalize_company", lambda n: n)

    @pytest.mark.asyncio
    async def test_thread_id_includes_user_id(self, monkeypatch):
        from interview_agents.g3_run import run_g3_graph

        fake = _FakeGraph()
        self._patch(monkeypatch, fake, app_user_id=USER_A)
        await run_g3_graph("app-abc", company_name="Visa", round_number=2)

        tid = fake.last_config["configurable"]["thread_id"]
        assert USER_A in tid
        assert "app-abc" in tid
        assert tid == f"g3-{USER_A}-app-app-abc-r2"

    @pytest.mark.asyncio
    async def test_same_app_id_different_users_differ(self, monkeypatch):
        from interview_agents.g3_run import run_g3_graph

        fake_a = _FakeGraph()
        self._patch(monkeypatch, fake_a, app_user_id=USER_A)
        await run_g3_graph("shared-app", company_name="Visa")
        tid_a = fake_a.last_config["configurable"]["thread_id"]

        fake_b = _FakeGraph()
        self._patch(monkeypatch, fake_b, app_user_id=USER_B)
        await run_g3_graph("shared-app", company_name="Visa")
        tid_b = fake_b.last_config["configurable"]["thread_id"]

        assert tid_a != tid_b

    @pytest.mark.asyncio
    async def test_recursion_limit_is_explicit(self, monkeypatch):
        from interview_agents.g3_run import run_g3_graph, G3_RECURSION_LIMIT

        fake = _FakeGraph()
        self._patch(monkeypatch, fake, app_user_id=USER_A)
        await run_g3_graph("app-1", company_name="Visa")

        assert fake.last_config["recursion_limit"] == G3_RECURSION_LIMIT
        assert fake.last_config["recursion_limit"] > 25


# ─── G9 (story extractor) — already namespaced; assert it stays so + limit ─
class TestG9ThreadId:
    def _patch(self, monkeypatch, fake):
        from agents import g9_run

        monkeypatch.setattr(g9_run, "_get_graph", lambda: fake)
        # render_master_cv_md / hash_cv are imported lazily from g9_io.
        from agents import g9_io

        monkeypatch.setattr(g9_io, "render_master_cv_md", lambda: "# CV body")
        monkeypatch.setattr(g9_io, "hash_cv", lambda md: "deadbeefcafef00d")

    @pytest.mark.asyncio
    async def test_thread_id_includes_user_id(self, monkeypatch):
        from agents.g9_run import run_g9

        fake = _FakeGraph()
        self._patch(monkeypatch, fake)
        await run_g9(user_id=USER_A)
        tid = fake.last_config["configurable"]["thread_id"]
        # G9 namespaces by the first 8 chars of the user_id.
        assert tid.startswith(f"g9-{USER_A[:8]}-")

    @pytest.mark.asyncio
    async def test_recursion_limit_is_explicit(self, monkeypatch):
        from agents.g9_run import run_g9, G9_RECURSION_LIMIT

        fake = _FakeGraph()
        self._patch(monkeypatch, fake)
        await run_g9(user_id=USER_A)
        assert fake.last_config["recursion_limit"] == G9_RECURSION_LIMIT


# ─── G6 (follow-up) — not checkpointed, but ainvoke must carry the limit ───
class TestG6RecursionLimit:
    @pytest.mark.asyncio
    async def test_recursion_limit_is_explicit(self, monkeypatch):
        from agents import g6_io
        from agents.g6_io import run_g6_for_application, _G6_RECURSION_LIMIT

        fake = _FakeGraph()
        # Patch the loaders the entry point calls before ainvoke.
        monkeypatch.setattr(
            g6_io, "load_application",
            lambda app_id: {"id": app_id, "company": "Visa", "user_id": USER_A},
        )
        monkeypatch.setattr(g6_io, "load_company_persona", lambda uid, cn: {})
        monkeypatch.setattr(g6_io, "load_last_cadence_row", lambda app_id: None)
        # build_g6_graph is imported lazily from agents.g6_graph inside the
        # function — patch it at the source module.
        import agents.g6_graph as g6_graph
        monkeypatch.setattr(g6_graph, "build_g6_graph", lambda *a, **kw: fake)

        await run_g6_for_application(123, user_id=USER_A)
        assert fake.last_config is not None
        assert fake.last_config["recursion_limit"] == _G6_RECURSION_LIMIT


# ─── G4 (LinkedIn) — not checkpointed, but ainvoke must carry the limit ────
class TestG4RecursionLimit:
    @pytest.mark.asyncio
    async def test_recursion_limit_is_explicit(self, monkeypatch):
        from agents import g4_linkedin_graph as g4

        fake = _FakeGraph()

        # run_g4_graph calls get_supabase() for the voice profile + the
        # company_knowledge rows, then build_g4_graph(); patch both so
        # nothing real runs. The query builder is fully chainable (every
        # unknown method returns self) and execute() yields a non-empty
        # voice profile so the cold-start default branch is skipped.
        class _Result:
            data = [{"tone_directives": "x", "avoid_phrases": []}]

        class _Q:
            def execute(self): return _Result()
            def __getattr__(self, _name):
                # select / eq / limit / gte / order / ... → chain
                return lambda *a, **kw: self

        class _DB:
            def table(self, *a, **kw): return _Q()

        monkeypatch.setattr(g4, "get_supabase", lambda: _DB())
        monkeypatch.setattr(g4, "build_g4_graph", lambda *a, **kw: fake)

        await g4.run_g4_graph(USER_A)
        assert fake.last_config is not None
        assert "recursion_limit" in fake.last_config
        assert fake.last_config["recursion_limit"] >= 25
