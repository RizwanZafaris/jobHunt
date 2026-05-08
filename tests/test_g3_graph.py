"""
Phase 2 unit tests — G3 Interview Prep graph compiles, state schema is
valid, merge_questions Jaccard dedupe behaves under multiple cases,
star_story_matcher handles empty story_bank, mock_interview_loop
converges + cost-caps correctly, persona quality gate is reused.

No live API calls. No live Supabase calls. The router is mocked via
monkeypatch and the supabase package is stubbed at the top of the file.
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch, MagicMock

import pytest


# Ensure the project root is on path so `import interview_agents` works
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub the `supabase` package so db.client's top-level import succeeds even
# when the package isn't pip-installed locally.
sys.modules.setdefault("supabase", MagicMock())


# ─── State schema ─────────────────────────────────────────────────────────
class TestInterviewPrepState:
    def test_typeddict_is_partial(self):
        """InterviewPrepState should be `total=False` so partial states are valid."""
        from interview_agents.g3_state import InterviewPrepState
        s: InterviewPrepState = {}  # type: ignore[assignment]
        assert isinstance(s, dict)

    def test_make_turn_truncates_input(self):
        from interview_agents.g3_state import make_turn
        long_input = "x" * 1000
        turn = make_turn(node="behavioral_predictor", input_summary=long_input)
        assert len(turn["input_summary"]) <= 500
        assert turn["node"] == "behavioral_predictor"

    def test_make_turn_defaults(self):
        from interview_agents.g3_state import make_turn
        turn = make_turn(node="entry")
        assert turn["node"] == "entry"
        assert turn["iteration"] == 0
        assert turn["cost_usd"] == 0.0
        assert turn["error"] is None

    def test_truncate_helper(self):
        from interview_agents.g3_state import truncate
        assert truncate("") == ""
        assert truncate("abc") == "abc"
        assert truncate("a" * 600, max_chars=100).endswith("...")
        assert len(truncate("a" * 600, max_chars=100)) == 100


# ─── merge_questions: Jaccard dedupe at 0.7 threshold ─────────────────────
class TestMergeQuestions:
    @pytest.fixture(autouse=True)
    def _env(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

    @pytest.mark.asyncio
    async def test_unions_three_predictors(self):
        from interview_agents.g3_nodes import merge_questions_node
        state = {
            "behavioral_questions": [
                {"question": "Tell me about a time you led under uncertainty",
                 "competency": "leadership", "importance": 5},
            ],
            "technical_questions": [
                {"question": "Design a tokenisation service for cross-border payments",
                 "competency": "system_design", "importance": 4},
            ],
            "domain_questions": [
                {"question": "Walk me through the lifecycle of a B2B settlement",
                 "competency": "domain_depth", "importance": 4},
            ],
        }
        out = await merge_questions_node(state)
        likely = out["likely_questions"]
        assert len(likely) == 3
        sources = {q["source"] for q in likely}
        assert sources == {"behavioral", "technical", "domain"}

    @pytest.mark.asyncio
    async def test_dedupe_jaccard_threshold(self):
        """Two near-duplicate questions should collapse to one."""
        from interview_agents.g3_nodes import merge_questions_node
        # Two questions with very high overlap — should dedupe to one
        state = {
            "behavioral_questions": [
                {"question": "Tell me about a time you led a difficult cross-functional initiative",
                 "competency": "leadership", "importance": 5},
            ],
            "technical_questions": [],
            "domain_questions": [
                {"question": "Describe a difficult cross-functional initiative you led",
                 "competency": "leadership", "importance": 4},
            ],
        }
        out = await merge_questions_node(state)
        likely = out["likely_questions"]
        # Both questions have token sets like {led, difficult, cross-functional, initiative}
        # → Jaccard ≥ 0.7 → dedupe
        assert len(likely) == 1
        # Behavioral should win because it has higher importance (5 vs 4)
        assert likely[0]["importance"] == 5

    @pytest.mark.asyncio
    async def test_dedupe_keeps_distinct_questions(self):
        """Truly distinct questions should NOT be deduped."""
        from interview_agents.g3_nodes import merge_questions_node
        state = {
            "behavioral_questions": [
                {"question": "Tell me about a leadership conflict",
                 "competency": "leadership", "importance": 5},
            ],
            "technical_questions": [
                {"question": "Design a payment fraud detection system at scale",
                 "competency": "system_design", "importance": 5},
            ],
            "domain_questions": [
                {"question": "What are the regulatory differences between PSD2 and CCPA",
                 "competency": "regulatory", "importance": 4},
            ],
        }
        out = await merge_questions_node(state)
        # Three distinct questions — none should dedupe
        assert len(out["likely_questions"]) == 3

    @pytest.mark.asyncio
    async def test_caps_at_20(self):
        """Even with 30 distinct questions, output is capped at 20.

        Use truly diverse vocabulary per question so token-set Jaccard
        doesn't dedupe them. (Earlier test attempts shared too many tokens
        like 'behavioral', 'question', 'number' and got deduped to 1.)
        """
        from interview_agents.g3_nodes import merge_questions_node
        # 15 behavioral + 15 technical, each with diverse vocabulary
        bhv_words = [
            "stakeholder negotiation",
            "team retention crisis",
            "exec board misalignment",
            "vendor escalation deadlock",
            "regulatory friction Tabby",
            "headcount budget cut",
            "post-acquisition merger integration",
            "PMF pivot decision Pakistan",
            "underperformer turnaround six months",
            "customer churn Daraz cohort",
            "platform consolidation Saudi Arabia",
            "fraud incident response runbook",
            "compliance audit Standard Chartered",
            "OKR misalignment q3 leadership",
            "cross-border treasury reconciliation",
        ]
        tech_words = [
            "tokenisation vault PCI scope",
            "real-time fraud risk scoring",
            "distributed cache invalidation strategy",
            "settlement reconciliation pipeline",
            "fraud detection feature store",
            "FX hedge mark-to-market",
            "card switch routing logic",
            "ATM cassette replenishment optimisation",
            "BNPL underwriting model A/B",
            "kyc orchestration vendor lattice",
            "merchant onboarding queue throughput",
            "wallet ledger eventual consistency",
            "instant payments QoS sla",
            "webhook delivery retry backoff",
            "rate limiting subscriber tier",
        ]
        state = {
            "behavioral_questions": [
                {"question": f"Tell me about {w}", "competency": "leadership", "importance": 3}
                for w in bhv_words
            ],
            "technical_questions": [
                {"question": f"Design a {w}", "competency": "system_design", "importance": 3}
                for w in tech_words
            ],
            "domain_questions": [],
        }
        out = await merge_questions_node(state)
        assert len(out["likely_questions"]) == 20

    @pytest.mark.asyncio
    async def test_sorts_by_importance_desc(self):
        from interview_agents.g3_nodes import merge_questions_node
        state = {
            "behavioral_questions": [
                {"question": "Low importance behavioral question", "importance": 1},
                {"question": "High importance behavioral question", "importance": 5},
                {"question": "Mid importance behavioral question", "importance": 3},
            ],
            "technical_questions": [],
            "domain_questions": [],
        }
        out = await merge_questions_node(state)
        scores = [q["importance"] for q in out["likely_questions"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_empty_inputs(self):
        from interview_agents.g3_nodes import merge_questions_node
        state = {
            "behavioral_questions": [],
            "technical_questions": [],
            "domain_questions": [],
        }
        out = await merge_questions_node(state)
        assert out["likely_questions"] == []


# ─── star_story_matcher: empty story_bank fallback ────────────────────────
class TestStarStoryMatcher:
    @pytest.fixture(autouse=True)
    def _env(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

    @pytest.mark.asyncio
    async def test_empty_story_bank_flags_all_questions(self):
        """When story_bank is empty (cold start), every question gets
        flagged with needs_rizwan_input=True. No LLM call is made."""
        from interview_agents.g3_nodes import star_story_matcher_node
        state = {
            "company_name": "Stripe",
            "likely_questions": [
                {"question": "Tell me about leadership", "importance": 5},
                {"question": "Describe a stakeholder conflict", "importance": 4},
            ],
            "story_bank": [],
        }
        out = await star_story_matcher_node(state)
        stars = out["star_stories"]
        assert len(stars) == 2
        # Every question flagged needing input
        assert all(s["needs_rizwan_input"] for s in stars)
        assert all(s["match_quality"] == "missing" for s in stars)
        assert all(s["story_id"] is None for s in stars)
        # No LLM cost incurred — empty story bank short-circuits
        assert "cost_usd_total" not in out

    @pytest.mark.asyncio
    async def test_no_questions_returns_empty(self):
        """When likely_questions is empty, no work and no LLM call."""
        from interview_agents.g3_nodes import star_story_matcher_node
        state = {
            "company_name": "Stripe",
            "likely_questions": [],
            "story_bank": [{"id": "abc", "title": "Test"}],
        }
        out = await star_story_matcher_node(state)
        assert out["star_stories"] == []

    @pytest.mark.asyncio
    async def test_with_stories_calls_llm(self, monkeypatch):
        """When stories exist, an LLM call is made for matching."""
        from interview_agents import g3_nodes
        from agents.llm_router import LLMResult

        async def fake_ask(**kwargs):
            return LLMResult(
                text='{"matches": [{"question": "Tell me about leadership", '
                     '"competency": "leadership", "story_id": "uuid-abc", '
                     '"match_quality": "strong", "rationale": "great fit", '
                     '"needs_rizwan_input": false}]}',
                provider="anthropic",
                model="claude-opus-4-5-20251101",
                input_tokens=1000,
                output_tokens=200,
                cost_usd=0.10,
                latency_ms=1500,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        state = {
            "company_name": "Stripe",
            "likely_questions": [{"question": "Tell me about leadership", "importance": 5}],
            "story_bank": [{
                "id": "uuid-abc",
                "title": "Daraz turnaround",
                "themes": ["leadership", "turnaround"],
                "situation": "Daraz Pakistan was losing $X/mo",
                "task": "Stop the bleed in 90 days",
                "action": "Restructured team",
                "result": "$Y saved",
                "companies_used": [],
            }],
        }
        out = await g3_nodes.star_story_matcher_node(state)
        assert len(out["star_stories"]) == 1
        assert out["star_stories"][0]["match_quality"] == "strong"
        assert out["star_stories"][0]["story_id"] == "uuid-abc"
        assert out["cost_usd_total"] == 0.10


# ─── mock_interview_loop: convergence + cost cap ──────────────────────────
class TestMockInterviewLoop:
    @pytest.fixture(autouse=True)
    def _env(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

    def _make_router_fake(self, interviewer_text: str, critic_score: int):
        """Returns a FakeRouter that alternates interviewer + critic responses."""
        from agents.llm_router import LLMResult

        call_state = {"is_interviewer": True}

        async def fake_ask(**kwargs):
            if call_state["is_interviewer"]:
                call_state["is_interviewer"] = False
                return LLMResult(
                    text=interviewer_text,
                    provider="anthropic",
                    model="claude-opus-4-5-20251101",
                    input_tokens=1500,
                    output_tokens=600,
                    cost_usd=0.20,
                    latency_ms=2000,
                )
            call_state["is_interviewer"] = True
            return LLMResult(
                text='{"score": ' + str(critic_score) + ', '
                     '"axes": {"clarity": 80, "relevance": 80, "specificity": 80, "reflection": 80, "brevity": 80}, '
                     '"improvements": ["be more specific"]}',
                provider="deepseek",
                model="deepseek-reasoner",
                input_tokens=800,
                output_tokens=200,
                cost_usd=0.02,
                latency_ms=1000,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        return FakeRouter

    @pytest.mark.asyncio
    async def test_converges_when_score_meets_target(self, monkeypatch):
        from interview_agents import g3_nodes
        from config.settings import get_settings

        target = get_settings().g3_target_answer_score
        FakeRouter = self._make_router_fake(
            interviewer_text="### A strong answer here",
            critic_score=target + 5,
        )
        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        state = {
            "company_name": "Stripe",
            "job": {"title": "Head of Product"},
            "round_type": "hm",
            "round_number": 1,
            "likely_questions": [
                {"question": "Tell me about a time...", "importance": 5},
            ],
            "cost_usd_total": 0.0,
            "cost_cap_usd": 3.0,
        }
        out = await g3_nodes.mock_interview_loop_node(state)
        assert out["converged"] is True
        assert out["mock_critic_score"] >= target
        assert out["mock_iteration"] == 1
        assert out["cost_capped"] is False

    @pytest.mark.asyncio
    async def test_max_iterations_caps_loop(self, monkeypatch):
        """If the critic never gives a passing score, loop terminates at
        g3_max_iterations and converged=False."""
        from interview_agents import g3_nodes
        from config.settings import get_settings

        target = get_settings().g3_target_answer_score
        max_iter = get_settings().g3_max_iterations
        FakeRouter = self._make_router_fake(
            interviewer_text="### Mediocre answer",
            critic_score=target - 30,  # always below threshold
        )
        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        state = {
            "company_name": "Stripe",
            "job": {"title": "Head of Product"},
            "round_type": "hm",
            "round_number": 1,
            "likely_questions": [
                {"question": "A hard question", "importance": 5},
            ],
            "cost_usd_total": 0.0,
            "cost_cap_usd": 3.0,
        }
        out = await g3_nodes.mock_interview_loop_node(state)
        assert out["converged"] is False
        assert out["mock_iteration"] == max_iter

    @pytest.mark.asyncio
    async def test_cost_cap_forces_termination(self, monkeypatch):
        """If cumulative cost is already at/over cap, loop exits without
        any LLM calls. Mirrors G2 Phase 1.11 orchestrator pre-check."""
        from interview_agents import g3_nodes

        # Sentinel router that fails if called — proves no LLM call was made
        async def fake_ask(**kwargs):
            raise AssertionError("LLM should not have been called when cost cap is hit before loop")

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        state = {
            "company_name": "Stripe",
            "job": {"title": "Head of Product"},
            "round_type": "hm",
            "round_number": 1,
            "likely_questions": [
                {"question": "Anything", "importance": 5},
            ],
            "cost_usd_total": 3.5,        # already over $3 default cap
            "cost_cap_usd": 3.0,
        }
        out = await g3_nodes.mock_interview_loop_node(state)
        assert out["cost_capped"] is True
        assert out["converged"] is False

    @pytest.mark.asyncio
    async def test_no_questions_skips_loop(self, monkeypatch):
        """When likely_questions is empty (e.g. all predictors failed),
        the loop returns empty without calling the LLM."""
        from interview_agents import g3_nodes

        async def fake_ask(**kwargs):
            raise AssertionError("LLM should not have been called when no questions exist")

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        state = {
            "company_name": "Stripe",
            "likely_questions": [],
            "cost_usd_total": 0.0,
            "cost_cap_usd": 3.0,
        }
        out = await g3_nodes.mock_interview_loop_node(state)
        assert out["mock_target_question"] is None
        assert out["mock_answer_md"] == ""
        assert out["mock_critic_score"] == 0


# ─── compile_prep_pack: pure code rendering ───────────────────────────────
class TestCompilePrepPack:
    @pytest.fixture(autouse=True)
    def _env(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

    @pytest.mark.asyncio
    async def test_renders_minimal_pack(self):
        from interview_agents.g3_nodes import compile_prep_pack_node
        state = {
            "company_name": "Stripe",
            "job": {"title": "Head of Product Operations"},
            "round_type": "hm",
            "round_number": 1,
            "likely_questions": [
                {"question": "Tell me about leadership", "competency": "leadership",
                 "importance": 5, "source": "behavioral"},
            ],
            "star_stories": [],
            "company_persona": None,
            "mock_target_question": {"question": "Tell me about leadership", "importance": 5},
            "mock_answer_md": "### A model answer goes here.",
            "mock_critic_score": 85,
            "mock_iteration": 1,
        }
        out = await compile_prep_pack_node(state)
        md = out["prep_pack_md"]
        assert "# Interview Prep — Stripe" in md
        assert "Head of Product Operations" in md
        assert "Tell me about leadership" in md
        assert "## Mock Answer" in md
        assert "85/100" in md
        # Salary section is always rendered
        assert "Salary & Negotiation Notes" in md

    @pytest.mark.asyncio
    async def test_extracts_company_hooks_from_persona(self):
        from interview_agents.g3_nodes import compile_prep_pack_node
        state = {
            "company_name": "Stripe",
            "job": {"title": "Head of Product"},
            "round_type": "hm",
            "round_number": 1,
            "likely_questions": [],
            "star_stories": [],
            "company_persona": {
                "metadata": {
                    "company_hooks": ["Just shipped Stripe Atlas", "Recent IPO buzz"],
                    "red_flag_questions": ["Why are you leaving X?"],
                }
            },
            "mock_target_question": None,
            "mock_answer_md": "",
        }
        out = await compile_prep_pack_node(state)
        assert "Just shipped Stripe Atlas" in out["company_hooks"]
        assert "Why are you leaving X?" in out["red_flag_questions"]
        md = out["prep_pack_md"]
        assert "Just shipped Stripe Atlas" in md
        assert "Why are you leaving X?" in md


# ─── Graph compiles (without checkpointer) ────────────────────────────────
class TestGraphCompiles:
    def test_build_g3_graph_no_checkpointer(self):
        """Graph should compile cleanly without a checkpointer.

        langgraph is imported lazily inside build_g3_graph (so plain module
        import works even when langgraph isn't installed). Skip if the
        package isn't on path — production env will have it via requirements.txt.
        """
        from interview_agents.g3_graph import build_g3_graph
        try:
            graph = build_g3_graph(checkpointer=None)
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
        from interview_agents.g3_run import is_enabled
        original = os.environ.pop("USE_G3_GRAPH", None)
        try:
            assert is_enabled() is False
        finally:
            if original is not None:
                os.environ["USE_G3_GRAPH"] = original

    def test_enabled_when_true(self):
        from interview_agents.g3_run import is_enabled
        with patch.dict(os.environ, {"USE_G3_GRAPH": "true"}):
            assert is_enabled() is True
        with patch.dict(os.environ, {"USE_G3_GRAPH": "1"}):
            assert is_enabled() is True
        with patch.dict(os.environ, {"USE_G3_GRAPH": "yes"}):
            assert is_enabled() is True
        with patch.dict(os.environ, {"USE_G3_GRAPH": "on"}):
            assert is_enabled() is True

    def test_disabled_when_other_value(self):
        from interview_agents.g3_run import is_enabled
        with patch.dict(os.environ, {"USE_G3_GRAPH": "false"}):
            assert is_enabled() is False
        with patch.dict(os.environ, {"USE_G3_GRAPH": ""}):
            assert is_enabled() is False


# ─── Persona quality gate is REUSED from g2_run, not duplicated ───────────
class TestPersonaGateReuse:
    """G3 must reuse `check_persona_quality_gate` from `resume_agents.g2_run`
    rather than copy-pasting the function. Verifying the import path is
    the simplest test that catches duplication."""

    @pytest.fixture(autouse=True)
    def _env(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

    def test_check_persona_quality_gate_imports_from_g2_run(self):
        """The function lives in resume_agents.g2_run — G3 imports it."""
        from resume_agents.g2_run import check_persona_quality_gate
        assert callable(check_persona_quality_gate)

    def test_g3_does_not_redefine_function(self):
        """Confirm interview_agents/* doesn't redefine the gate function."""
        import inspect
        from interview_agents import g3_run, g3_nodes
        assert not hasattr(g3_run, "check_persona_quality_gate")
        assert not hasattr(g3_nodes, "check_persona_quality_gate")
        # Also confirm no re-definition by reading the source
        src_run = inspect.getsource(g3_run)
        src_nodes = inspect.getsource(g3_nodes)
        assert "def check_persona_quality_gate" not in src_run
        assert "def check_persona_quality_gate" not in src_nodes

    def test_force_with_g3_min_quality_works(self):
        """Calling the reused gate with min_quality=settings.g3_min_persona_quality
        works the same as the G2 caller."""
        from resume_agents.g2_run import check_persona_quality_gate
        from config.settings import get_settings
        result = check_persona_quality_gate(
            "Visa",
            force=True,
            min_quality=get_settings().g3_min_persona_quality,
        )
        assert result.verdict == "pass"


# ─── Cost cap export status hierarchy ─────────────────────────────────────
class TestExportStatusHierarchy:
    """Mirror G2 test_export_status_hierarchy_cost_capped_wins. The
    export_node maps state to status:
        cost_capped → 'cost_capped'
        iteration >= max && not converged → 'exhausted'
        else → 'converged'
    """

    def test_export_status_hierarchy_in_source(self):
        import inspect
        from interview_agents import g3_nodes
        src = inspect.getsource(g3_nodes.export_node)
        assert 'state.get("cost_capped")' in src
        assert '"cost_capped"' in src
        assert '"exhausted"' in src
        assert '"converged"' in src


# ─── Settings slots exist ─────────────────────────────────────────────────
class TestSettingsSlots:
    @pytest.fixture(autouse=True)
    def _env(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

    def test_g3_model_slots_present(self):
        from config.settings import get_settings
        s = get_settings()
        # All six G3 model slots
        assert s.g3_behavioral_predictor_model
        assert s.g3_technical_predictor_model
        assert s.g3_domain_predictor_model
        assert s.g3_star_matcher_model
        assert s.g3_mock_interviewer_model
        assert s.g3_mock_critic_model

    def test_g3_control_slots_have_safe_defaults(self):
        from config.settings import get_settings
        s = get_settings()
        assert s.g3_max_iterations == 2
        assert s.g3_target_answer_score == 80
        assert s.g3_max_cost_usd == 3.0
        assert s.g3_min_persona_quality in ("low", "medium", "high")
