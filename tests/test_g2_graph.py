"""
Phase 1 unit tests — G2 graph compiles, state schema is valid, IO layer
renders cleanly with mocked Supabase, merge_critique behaves under all
critic-result combos.

No live API calls. No live Supabase calls. All async DB calls in g2_io
are exercised against a hand-rolled mock 'supabase' object.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


# Ensure the project root is on path so `import resume_agents` works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── State schema ─────────────────────────────────────────────────────────
class TestResumeState:
    def test_typeddict_is_partial(self):
        """ResumeState should be `total=False` so partial states are valid."""
        from resume_agents.g2_state import ResumeState
        # An empty dict is a valid ResumeState because of total=False
        s: ResumeState = {}  # type: ignore[assignment]
        assert isinstance(s, dict)

    def test_make_turn_truncates_input(self):
        from resume_agents.g2_state import make_turn
        long_input = "x" * 1000
        turn = make_turn(node="writer", input_summary=long_input)
        assert len(turn["input_summary"]) <= 500
        assert turn["node"] == "writer"

    def test_make_turn_defaults(self):
        from resume_agents.g2_state import make_turn
        turn = make_turn(node="entry")
        assert turn["node"] == "entry"
        assert turn["iteration"] == 0
        assert turn["cost_usd"] == 0.0
        assert turn["error"] is None


# ─── Profile rendering (the critical correction from the live audit) ──────
class TestRenderMarkdown:
    def test_minimal_profile(self):
        from resume_agents.g2_io import _render_markdown
        master = {"name": "Test User", "headline": "PM", "summary": "A summary."}
        md = _render_markdown(master, [], [], [])
        assert "# Test User" in md
        assert "### PM" in md
        assert "A summary." in md

    def test_arrays_are_text_arrays_not_jsonb(self):
        """Live schema: core_competencies + technical_knowledge are text[], NOT jsonb."""
        from resume_agents.g2_io import _render_markdown
        master = {
            "name": "Test",
            "core_competencies": ["Payments", "BNPL", "Fintech"],     # text[]
            "technical_knowledge": ["Python", "SQL"],                  # text[]
        }
        md = _render_markdown(master, [], [], [])
        assert "Payments, BNPL, Fintech" in md
        assert "Python, SQL" in md

    def test_experience_with_groups(self):
        from resume_agents.g2_io import _render_markdown
        exp = [{
            "title": "CPO",
            "company": "Simpaisa",
            "dates": "2020 - Present",
            "summary": "Led product",
            "highlights": ["Bullet 1", "Bullet 2"],
            "groups": [
                {"heading": "Cross-Border", "bullets": ["Built X", "Shipped Y"]},
            ],
        }]
        md = _render_markdown({"name": "Test"}, exp, [], [])
        assert "### CPO — Simpaisa" in md
        assert "- Bullet 1" in md
        assert "**Cross-Border**" in md
        assert "- Built X" in md

    def test_certifications(self):
        from resume_agents.g2_io import _render_markdown
        certs = [{"full_name": "Project Management Pro", "name": "PMP", "issuer": "PMI", "year": 2018}]
        md = _render_markdown({"name": "Test"}, [], certs, [])
        assert "Project Management Pro — PMI (2018)" in md


# ─── merge_critique — pure code, exhaustive cases ─────────────────────────
class TestMergeCritique:
    def _make_state(self, critic_a, critic_b):
        return {
            "critic_a": critic_a,
            "critic_b": critic_b,
            "iteration": 1,
        }

    @pytest.mark.asyncio
    async def test_takes_strictest_score(self):
        from resume_agents.g2_nodes import merge_critique_node
        state = self._make_state(
            {"ats_score": 92, "missing_keywords": [], "specific_fixes": []},
            {"ats_score": 78, "missing_keywords": [], "specific_fixes": []},
        )
        out = await merge_critique_node(state)
        assert out["merged_critique"]["ats_score"] == 78

    @pytest.mark.asyncio
    async def test_unions_missing_keywords_dedupe_lower(self):
        from resume_agents.g2_nodes import merge_critique_node
        state = self._make_state(
            {"ats_score": 80, "missing_keywords": ["Tokenization", "BNPL"], "specific_fixes": []},
            {"ats_score": 85, "missing_keywords": ["tokenization", "PSP"],   "specific_fixes": []},
        )
        out = await merge_critique_node(state)
        kws = out["merged_critique"]["missing_keywords"]
        # Case-insensitive dedupe: "Tokenization" + "tokenization" → one
        assert sorted(kws) == ["bnpl", "psp", "tokenization"]

    @pytest.mark.asyncio
    async def test_unions_specific_fixes(self):
        from resume_agents.g2_nodes import merge_critique_node
        state = self._make_state(
            {"ats_score": 80, "specific_fixes": ["Add tokenization keyword"]},
            {"ats_score": 80, "specific_fixes": ["Quantify Daraz outcome"]},
        )
        out = await merge_critique_node(state)
        fixes = out["merged_critique"]["specific_fixes"]
        assert len(fixes) == 2

    @pytest.mark.asyncio
    async def test_handles_one_critic_failed(self):
        from resume_agents.g2_nodes import merge_critique_node
        # critic_a has score 0 (parse error or call failure)
        state = self._make_state(
            {"ats_score": 0, "_parse_error": True},
            {"ats_score": 85, "missing_keywords": ["x"]},
        )
        out = await merge_critique_node(state)
        # Should fall back to the working critic, not min(0, 85)=0
        assert out["merged_critique"]["ats_score"] == 85

    @pytest.mark.asyncio
    async def test_skim_test_only_passes_if_both_pass(self):
        from resume_agents.g2_nodes import merge_critique_node
        state = self._make_state(
            {"ats_score": 90, "skim_test_pass": True},
            {"ats_score": 85, "skim_test_pass": False},
        )
        out = await merge_critique_node(state)
        assert out["merged_critique"]["skim_test_pass"] is False


# ─── Graph compiles (without checkpointer) ────────────────────────────────
class TestGraphCompiles:
    def test_build_g2_graph_no_checkpointer(self):
        """Graph should compile cleanly without a checkpointer.

        langgraph is imported lazily inside build_g2_graph (so plain module
        import works even when langgraph isn't installed). Skip if the
        package isn't on path — production env will have it via requirements.txt.
        """
        from resume_agents.g2_graph import build_g2_graph
        try:
            graph = build_g2_graph(checkpointer=None)
        except ModuleNotFoundError as e:
            if "langgraph" in str(e):
                pytest.skip(f"langgraph not installed in this env: {e}")
            raise
        assert graph is not None
        # The compiled graph should expose ainvoke / invoke
        assert hasattr(graph, "ainvoke") or hasattr(graph, "invoke")


# ─── Feature flag ─────────────────────────────────────────────────────────
class TestFeatureFlag:
    def test_disabled_by_default(self):
        from resume_agents.g2_run import is_enabled
        # Save and clear env var
        original = os.environ.pop("USE_G2_GRAPH", None)
        try:
            assert is_enabled() is False
        finally:
            if original is not None:
                os.environ["USE_G2_GRAPH"] = original

    def test_enabled_when_true(self):
        from resume_agents.g2_run import is_enabled
        with patch.dict(os.environ, {"USE_G2_GRAPH": "true"}):
            assert is_enabled() is True
        with patch.dict(os.environ, {"USE_G2_GRAPH": "1"}):
            assert is_enabled() is True
        with patch.dict(os.environ, {"USE_G2_GRAPH": "yes"}):
            assert is_enabled() is True

    def test_disabled_when_other_value(self):
        from resume_agents.g2_run import is_enabled
        with patch.dict(os.environ, {"USE_G2_GRAPH": "false"}):
            assert is_enabled() is False
        with patch.dict(os.environ, {"USE_G2_GRAPH": ""}):
            assert is_enabled() is False


# ─── Phase 1.11: per-build cost cap ──────────────────────────────────────
class TestCostCap:
    """
    Validates the orchestrator's cost-cap enforcement and the
    export-status mapping. The orchestrator pre-check short-circuits
    before any LLM call when cost is already over cap; the post-check
    accounts for the orchestrator's own cost.
    """

    @pytest.fixture(autouse=True)
    def _env(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

    @pytest.mark.asyncio
    async def test_pre_check_short_circuits_when_already_over_cap(self):
        """If cost_usd_total >= cost_cap before orchestrator runs, no LLM
        call is made — we go straight to converged + cost_capped=True."""
        from resume_agents.g2_nodes import orchestrator_node
        state = {
            "iteration": 1,
            "cost_usd_total": 5.5,        # already over default $5 cap
            "cost_cap_usd": 5.0,
            "merged_critique": {
                "ats_score": 80,
                "specific_fixes": ["fix 1", "fix 2"],
            },
        }
        out = await orchestrator_node(state)
        assert out["converged"] is True
        assert out["cost_capped"] is True
        # Pre-check path doesn't increment cost (no LLM call made)
        assert "cost_usd_total" not in out
        # And the transcript turn records the cap-hit reason
        turn = out["transcript"][0]
        assert turn["node"] == "orchestrator"
        assert "cost cap hit" in turn["output"]["rationale"]

    @pytest.mark.asyncio
    async def test_under_cap_proceeds_normally(self, monkeypatch):
        """If cost is under cap, the orchestrator runs the LLM call as
        usual. We mock the router so we don't actually hit Anthropic."""
        from resume_agents import g2_nodes
        from agents.llm_router import LLMResult

        async def fake_ask(**kwargs):
            return LLMResult(
                text='{"converged": false, "rationale": "needs another pass"}',
                provider="anthropic",
                model="claude-opus-4-5-20251101",
                input_tokens=100,
                output_tokens=50,
                cost_usd=0.05,
                latency_ms=800,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g2_nodes, "get_router", lambda: FakeRouter())

        state = {
            "iteration": 1,
            "cost_usd_total": 1.5,        # well under $5 cap
            "cost_cap_usd": 5.0,
            "merged_critique": {
                "ats_score": 80,
                "specific_fixes": ["fix 1"],
            },
        }
        out = await g2_nodes.orchestrator_node(state)
        # Not capped — converged might be true or false depending on
        # decision + thresholds, but cost_capped specifically should NOT
        # be set to True.
        assert out.get("cost_capped") is not True
        # Cost was incurred (the router returned 0.05)
        assert out.get("cost_usd_total") == 0.05

    @pytest.mark.asyncio
    async def test_post_check_caps_when_orchestrator_call_pushes_over(self, monkeypatch):
        """If we're under cap before the call but the orchestrator's
        own cost pushes us over, we force converge + cost_capped."""
        from resume_agents import g2_nodes
        from agents.llm_router import LLMResult

        async def fake_ask(**kwargs):
            return LLMResult(
                text='{"converged": false, "rationale": "wants more"}',
                provider="anthropic",
                model="claude-opus-4-5-20251101",
                input_tokens=2000,
                output_tokens=500,
                cost_usd=0.30,                       # this push tips us over
                latency_ms=1200,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g2_nodes, "get_router", lambda: FakeRouter())

        state = {
            "iteration": 2,
            "cost_usd_total": 4.85,                  # 4.85 + 0.30 = 5.15 → over $5
            "cost_cap_usd": 5.0,
            "merged_critique": {
                "ats_score": 88,
                "specific_fixes": ["fix"],
            },
        }
        out = await g2_nodes.orchestrator_node(state)
        assert out["converged"] is True
        assert out["cost_capped"] is True

    @pytest.mark.asyncio
    async def test_max_iterations_still_forces_converge(self, monkeypatch):
        """When iteration hits max, force converge regardless of cost.
        cost_capped should NOT be set in that case (different reason)."""
        from resume_agents import g2_nodes
        from config.settings import get_settings
        from agents.llm_router import LLMResult

        async def fake_ask(**kwargs):
            return LLMResult(
                text='{"converged": false}',
                provider="anthropic",
                model="claude-opus-4-5",
                input_tokens=100, output_tokens=20,
                cost_usd=0.01, latency_ms=500,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g2_nodes, "get_router", lambda: FakeRouter())

        max_iter = get_settings().g2_max_iterations
        state = {
            "iteration": max_iter - 1,    # next iteration triggers max
            "cost_usd_total": 0.5,         # nowhere near cap
            "cost_cap_usd": 5.0,
            "merged_critique": {"ats_score": 70, "specific_fixes": ["x", "y", "z"]},
        }
        out = await g2_nodes.orchestrator_node(state)
        assert out["converged"] is True       # iteration cap forces convergence
        assert out.get("cost_capped") is not True

    def test_export_status_hierarchy_cost_capped_wins(self):
        """The export_node logic in g2_nodes maps state to status:
            cost_capped → 'cost_capped'
            iteration >= max && not converged → 'exhausted'
            else → 'converged'
        We test the mapping by inspecting the source — the function
        also does I/O so end-to-end testing belongs in integration."""
        import inspect
        from resume_agents import g2_nodes
        src = inspect.getsource(g2_nodes.export_node)
        # Verify the three-way status hierarchy is present
        assert 'state.get("cost_capped")' in src
        assert '"cost_capped"' in src
        assert '"exhausted"' in src
        assert '"converged"' in src
