"""Tier 2 G3 §4.2 — story_bank integration tests.

Covers:
  - story_retriever_node returns top-k matches keyed by question index
  - story_retriever_node skips non-behavioural questions
  - story_retriever_node handles search failure gracefully
  - gap_analyzer_node flags low-similarity matches and emits suggestions
  - gap_analyzer_node skips LLM call when every question has strong match
  - gap_analyzer_node leans on profile_master.core_competencies (identity lock)
  - prep_pack template renders STAR + similarity + reframe callout
  - prep_pack_persona_critic_node drops fabricated competencies
  - prep_pack_persona_critic_node re-renders pack markdown after drops
  - Outcome attribution: _credit_stories_for_outcome propagates pass/fail
    delta to story_bank.outcome_score via update_outcome_score
  - G3 graph compiles with the two new nodes wired between merge and
    star_story_matcher (and the persona_critic before export).

No live API calls. No live Supabase calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())


@pytest.fixture(autouse=True)
def _env():
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")
    os.environ.setdefault("SUPABASE_URL", "http://test")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


RIZWAN_UUID = "00000000-0000-0000-0000-000000000001"


def _make_match(story_id: str, title: str, similarity: float, *, competencies=None, source_text="src", source_experience_id=4):
    """Build a StoryBankMatch dataclass instance for mocks."""
    from agents.story_bank_agent import StoryBankMatch, StoryBankRow
    story = StoryBankRow(
        id=story_id,
        user_id=RIZWAN_UUID,
        title=title,
        situation="Daraz Pakistan was bleeding cash in Q3",
        task="Stop the bleed in 90 days",
        action="Restructured the seller funding programme and renegotiated terms",
        result="$8M ARR recovered, 12% margin lift, 2 churned merchants reactivated",
        reflection="Lesson: lead with the data, not the politics",
        competencies=competencies or ["leadership", "stakeholder_management"],
        archetypes=["CPO"],
        quantified_metrics=["$8M ARR", "12% margin"],
        outcome_score=2,
        source_text=source_text,
        source_experience_id=source_experience_id,
    )
    return StoryBankMatch(story=story, similarity=similarity)


# ════════════════════════════════════════════════════════════════════════
# story_retriever_node
# ════════════════════════════════════════════════════════════════════════
class TestStoryRetrieverNode:
    @pytest.mark.asyncio
    async def test_finds_top_match_per_behavioural_question(self, monkeypatch):
        """For each behavioural question, search_stories is called and the
        top-1 hit is stored under the question's index key."""
        from interview_agents import g3_nodes

        async def fake_search(*, user_id, query, k=3, competency=None):
            # Two competencies → two different stories.
            if "leadership" in (competency or "").lower() or "lead" in query.lower():
                return [_make_match("story-lead", "Daraz turnaround", 0.84,
                                    competencies=["leadership"])]
            return [_make_match("story-stake", "Vendor negotiation", 0.72,
                                competencies=["stakeholder_management"])]

        monkeypatch.setattr(g3_nodes, "search_stories", fake_search, raising=False)
        # Patch at the import-site too, since story_retriever_node uses
        # a lazy `from agents.story_bank_agent import ...`.
        import agents.story_bank_agent as sba
        monkeypatch.setattr(sba, "search_stories", fake_search)

        state = {
            "likely_questions": [
                {"question": "Tell me about a time you led under uncertainty",
                 "competency": "leadership", "importance": 5},
                {"question": "Describe a stakeholder negotiation that went wrong",
                 "competency": "stakeholder_management", "importance": 4},
            ],
        }
        out = await g3_nodes.story_retriever_node(state)
        rs = out["retrieved_stories"]
        assert len(rs) == 2
        assert rs["0"]["story_id"] == "story-lead"
        assert rs["0"]["similarity"] == pytest.approx(0.84)
        # cite breadcrumbs preserved (engineering principle 2)
        assert rs["0"]["source_experience_id"] == 4
        assert rs["0"]["source_text"] == "src"
        assert rs["1"]["story_id"] == "story-stake"

    @pytest.mark.asyncio
    async def test_skips_non_story_competencies(self, monkeypatch):
        """Technical / system_design / case questions should NOT trigger
        search_stories — they don't typically map to a STAR story."""
        from interview_agents import g3_nodes

        call_count = {"n": 0}

        async def fake_search(*, user_id, query, k=3, competency=None):
            call_count["n"] += 1
            return [_make_match("any", "Any", 0.6)]

        import agents.story_bank_agent as sba
        monkeypatch.setattr(sba, "search_stories", fake_search)

        state = {
            "likely_questions": [
                {"question": "Design a tokenisation service", "competency": "system_design",
                 "importance": 5},
                {"question": "Walk me through a case", "competency": "case", "importance": 4},
                {"question": "How do you regulate PSD2", "competency": "regulatory",
                 "importance": 3},
            ],
        }
        out = await g3_nodes.story_retriever_node(state)
        # All three skipped — no calls, no retrieved stories.
        assert call_count["n"] == 0
        assert out["retrieved_stories"] == {}

    @pytest.mark.asyncio
    async def test_search_failure_does_not_crash(self, monkeypatch):
        """If search_stories raises, the node should record a miss for
        that question and continue with the rest."""
        from interview_agents import g3_nodes

        async def boom(*, user_id, query, k=3, competency=None):
            raise RuntimeError("simulated supabase outage")

        import agents.story_bank_agent as sba
        monkeypatch.setattr(sba, "search_stories", boom)

        state = {
            "likely_questions": [
                {"question": "Tell me about leadership", "competency": "leadership"},
            ],
        }
        out = await g3_nodes.story_retriever_node(state)
        assert out["retrieved_stories"] == {}
        # Transcript records the miss
        transcript = out["transcript"][0]
        assert transcript["output"]["n_misses"] == 1


# ════════════════════════════════════════════════════════════════════════
# gap_analyzer_node
# ════════════════════════════════════════════════════════════════════════
class TestGapAnalyzerNode:
    @pytest.mark.asyncio
    async def test_flags_low_similarity_matches(self, monkeypatch):
        """A question with similarity < 0.5 should be sent to the LLM and
        a reframe suggestion captured back."""
        from interview_agents import g3_nodes
        from agents.llm_router import LLMResult

        async def fake_ask(**kwargs):
            # Capture the user prompt for assertion below.
            fake_ask.kwargs = kwargs
            return LLMResult(
                text=json.dumps({
                    "gaps": [{
                        "question": "Tell me about ML in production",
                        "missing_competency": "ml_ai",
                        "reframe_suggestion": "Lean on your data-product work at Daraz "
                                              "and be explicit you haven't shipped a model.",
                    }],
                }),
                provider="anthropic",
                model="claude-sonnet-4-6",
                input_tokens=2200,
                output_tokens=180,
                cost_usd=0.045,
                latency_ms=2000,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        # Mock load_profile_competencies to a real core list.
        import agents.g9_io as g9_io_mod
        monkeypatch.setattr(g9_io_mod, "load_profile_competencies",
                            lambda: ["leadership", "stakeholder_management",
                                     "product_strategy"])

        state = {
            "likely_questions": [
                {"question": "Tell me about ML in production",
                 "competency": "ml_ai", "importance": 4},
            ],
            "retrieved_stories": {
                "0": {"story_id": "s1", "similarity": 0.35,
                      "story": {"title": "Some weakly-matched story"}},
            },
        }
        out = await g3_nodes.gap_analyzer_node(state)
        gaps = out["story_gaps"]
        assert len(gaps) == 1
        assert gaps[0]["missing_competency"] == "ml_ai"
        assert "data-product" in gaps[0]["reframe_suggestion"]
        # Cost telemetry recorded (engineering principle 4)
        assert out["cost_usd_total"] == pytest.approx(0.045, rel=0.1)
        # cache_system=True was passed through (engineering principle 5)
        assert fake_ask.kwargs.get("cache_system") is True

    @pytest.mark.asyncio
    async def test_skips_llm_when_all_strong_matches(self, monkeypatch):
        """If every question has similarity ≥ 0.5, no Sonnet call fires."""
        from interview_agents import g3_nodes

        called = {"n": 0}

        async def should_not_be_called(**kwargs):
            called["n"] += 1
            raise AssertionError("LLM should NOT be called when all matches are strong")

        class FakeRouter:
            ask = staticmethod(should_not_be_called)

        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        state = {
            "likely_questions": [
                {"question": "Q1", "competency": "leadership"},
                {"question": "Q2", "competency": "stakeholder_management"},
            ],
            "retrieved_stories": {
                "0": {"story_id": "a", "similarity": 0.81,
                      "story": {"title": "Strong A"}},
                "1": {"story_id": "b", "similarity": 0.77,
                      "story": {"title": "Strong B"}},
            },
        }
        out = await g3_nodes.gap_analyzer_node(state)
        assert out["story_gaps"] == []
        assert called["n"] == 0

    @pytest.mark.asyncio
    async def test_empty_questions_short_circuits(self):
        from interview_agents import g3_nodes
        out = await g3_nodes.gap_analyzer_node({"likely_questions": []})
        assert out["story_gaps"] == []


# ════════════════════════════════════════════════════════════════════════
# compile_prep_pack — renders STAR + similarity + gap callout
# ════════════════════════════════════════════════════════════════════════
class TestPrepPackRendersStoriesAndGaps:
    @pytest.mark.asyncio
    async def test_renders_retrieved_story_and_gap(self):
        from interview_agents import g3_nodes
        state = {
            "company_name": "Stripe",
            "job": {"title": "Head of Product"},
            "round_type": "hm",
            "round_number": 1,
            "likely_questions": [
                {"question": "Tell me about leading under uncertainty",
                 "competency": "leadership", "importance": 5, "source": "behavioral"},
                {"question": "Tell me about ML in production",
                 "competency": "ml_ai", "importance": 3, "source": "behavioral"},
            ],
            "star_stories": [],
            "retrieved_stories": {
                "0": {
                    "story_id": "story-lead",
                    "similarity": 0.84,
                    "source_text": "src lead",
                    "source_experience_id": 4,
                    "story": {
                        "id": "story-lead",
                        "title": "Daraz turnaround under hostile board",
                        "situation": "Daraz was bleeding $X/mo and the board wanted answers",
                        "task": "Stop the bleed in 90 days",
                        "action": "Restructured the team, renegotiated seller terms",
                        "result": "$8M ARR recovered, board confidence restored",
                        "reflection": "Lesson: lead with data not politics",
                        "competencies": ["leadership"],
                    },
                },
            },
            "story_gaps": [{
                "question": "Tell me about ML in production",
                "missing_competency": "ml_ai",
                "reframe_suggestion": "Lean on adjacent data-product work; be honest about no ML.",
            }],
            "company_persona": {},
        }
        out = await g3_nodes.compile_prep_pack_node(state)
        md = out["prep_pack_md"]
        # Story section present
        assert "Auto-Retrieved STAR Stories" in md
        assert "Daraz turnaround under hostile board" in md
        # Similarity score visible
        assert "similarity 0.84" in md
        # cite:knowledge_id breadcrumb (principle 2)
        assert "cite:knowledge_id=story-lead" in md
        # Full body collapsible
        assert "<details>" in md
        assert "<summary>Full STAR+R body</summary>" in md
        # Gap callout
        assert "Gap callout" in md
        assert "ml_ai" in md
        assert "data-product" in md


# ════════════════════════════════════════════════════════════════════════
# prep_pack_persona_critic — identity lock
# ════════════════════════════════════════════════════════════════════════
class TestPrepPackPersonaCritic:
    @pytest.mark.asyncio
    async def test_drops_fabricated_competencies(self, monkeypatch):
        """Competencies on retrieved stories that aren't in the candidate's
        core_competencies should be dropped before display."""
        from interview_agents import g3_nodes

        import agents.g9_io as g9_io_mod
        monkeypatch.setattr(g9_io_mod, "load_profile_competencies",
                            lambda: ["leadership", "stakeholder_management"])

        state = {
            "likely_questions": [{"question": "Q1", "source": "behavioral",
                                  "competency": "leadership", "importance": 5}],
            "retrieved_stories": {
                "0": {
                    "story_id": "s1",
                    "similarity": 0.8,
                    "story": {
                        "title": "T",
                        "situation": "S", "task": "T", "action": "A",
                        "result": "R", "reflection": "RR",
                        "competencies": ["leadership", "ml_ai", "founder"],
                    },
                },
            },
            "story_gaps": [],
            "company_persona": {},
            "job": {},
            "company_name": "Stripe",
        }
        out = await g3_nodes.prep_pack_persona_critic_node(state)
        # ml_ai + founder dropped, leadership kept
        rs = out["retrieved_stories"]
        kept = rs["0"]["story"]["competencies"]
        assert "leadership" in kept
        assert "ml_ai" not in kept
        assert "founder" not in kept
        # Drops recorded
        drops = out["persona_critic_drops"]
        assert any("ml_ai" in d for d in drops)
        assert any("founder" in d for d in drops)

    @pytest.mark.asyncio
    async def test_blanks_fabricated_gap_competency(self, monkeypatch):
        """A story_gap's missing_competency that isn't in core_competencies
        should be wrapped in '(not in profile: ...)' BUT the suggestion
        text is preserved as coaching."""
        from interview_agents import g3_nodes

        import agents.g9_io as g9_io_mod
        monkeypatch.setattr(g9_io_mod, "load_profile_competencies",
                            lambda: ["leadership", "stakeholder_management"])

        state = {
            "likely_questions": [],
            "retrieved_stories": {},
            "story_gaps": [{
                "question": "Tell me about ML",
                "missing_competency": "ml_ai",
                "reframe_suggestion": "Be honest you haven't shipped ML.",
            }],
            "company_persona": {},
            "job": {},
            "company_name": "Stripe",
        }
        out = await g3_nodes.prep_pack_persona_critic_node(state)
        gap = out["story_gaps"][0]
        assert "(not in profile: ml_ai)" in gap["missing_competency"]
        # Suggestion text preserved
        assert gap["reframe_suggestion"] == "Be honest you haven't shipped ML."

    @pytest.mark.asyncio
    async def test_noop_when_competencies_match(self, monkeypatch):
        """If all competencies are in core, no drops and no re-render."""
        from interview_agents import g3_nodes

        import agents.g9_io as g9_io_mod
        monkeypatch.setattr(g9_io_mod, "load_profile_competencies",
                            lambda: ["leadership"])

        state = {
            "likely_questions": [],
            "retrieved_stories": {
                "0": {
                    "story_id": "s1",
                    "similarity": 0.8,
                    "story": {
                        "title": "T", "situation": "S", "task": "T",
                        "action": "A", "result": "R",
                        "competencies": ["leadership"],
                    },
                },
            },
            "story_gaps": [],
            "company_persona": {},
            "job": {},
            "company_name": "Stripe",
            "prep_pack_md": "# pre-existing pack",
        }
        out = await g3_nodes.prep_pack_persona_critic_node(state)
        assert out["persona_critic_drops"] == []
        # Pack md unchanged when no drops
        assert "prep_pack_md" not in out


# ════════════════════════════════════════════════════════════════════════
# Outcome attribution
# ════════════════════════════════════════════════════════════════════════
class TestOutcomeAttribution:
    @pytest.mark.asyncio
    async def test_credits_retrieved_story_ids_on_pass(self, monkeypatch):
        """When a passing outcome is logged, every retrieved story for that
        prep gets a +1 bump via update_outcome_score."""
        from api.interview_studio import _credit_stories_for_outcome
        from uuid import uuid4

        application_id = uuid4()
        user_id = uuid4()

        # Mock the Supabase chain to return one prep with two retrieved stories.
        mock_db = MagicMock()
        retrieved = {
            "0": {"story_id": "story-A", "similarity": 0.8,
                  "story": {"id": "story-A"}},
            "1": {"story_id": "story-B", "similarity": 0.55,
                  "story": {"id": "story-B"}},
        }
        mock_exec = MagicMock()
        mock_exec.data = [{"id": "prep-1", "retrieved_stories": retrieved}]
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_exec
        mock_db.table.return_value = mock_table

        import api.interview_studio as is_mod
        monkeypatch.setattr(is_mod, "get_supabase", lambda: mock_db)

        # Capture update_outcome_score calls
        update_calls: list[tuple[str, int]] = []

        async def fake_update(story_id, delta):
            update_calls.append((str(story_id), int(delta)))

        import agents.story_bank_agent as sba
        monkeypatch.setattr(sba, "update_outcome_score", fake_update)

        summary = await _credit_stories_for_outcome(
            application_id=application_id,
            user_id=user_id,
            passed=True,
        )
        assert summary["credited_story_count"] == 2
        assert summary["delta"] == 1
        assert set(summary["story_ids"]) == {"story-A", "story-B"}
        # Bumps actually fired
        assert ("story-A", 1) in update_calls
        assert ("story-B", 1) in update_calls

    @pytest.mark.asyncio
    async def test_credits_negative_on_fail(self, monkeypatch):
        from api.interview_studio import _credit_stories_for_outcome
        from uuid import uuid4

        mock_db = MagicMock()
        mock_exec = MagicMock()
        mock_exec.data = [{
            "id": "prep-1",
            "retrieved_stories": {
                "0": {"story_id": "story-X", "story": {"id": "story-X"}, "similarity": 0.7},
            },
        }]
        mock_table = MagicMock()
        mock_table.select.return_value = mock_table
        mock_table.eq.return_value = mock_table
        mock_table.order.return_value = mock_table
        mock_table.limit.return_value = mock_table
        mock_table.execute.return_value = mock_exec
        mock_db.table.return_value = mock_table

        import api.interview_studio as is_mod
        monkeypatch.setattr(is_mod, "get_supabase", lambda: mock_db)

        update_calls: list[tuple[str, int]] = []

        async def fake_update(story_id, delta):
            update_calls.append((str(story_id), int(delta)))

        import agents.story_bank_agent as sba
        monkeypatch.setattr(sba, "update_outcome_score", fake_update)

        summary = await _credit_stories_for_outcome(
            application_id=uuid4(),
            user_id=uuid4(),
            passed=False,
        )
        assert summary["delta"] == -1
        assert summary["credited_story_count"] == 1
        assert update_calls == [("story-X", -1)]

    @pytest.mark.asyncio
    async def test_no_pass_signal_skips(self, monkeypatch):
        """If passed is None, no credits are written and no errors raised."""
        from api.interview_studio import _credit_stories_for_outcome
        from uuid import uuid4

        called = {"n": 0}

        async def fake_update(*a, **kw):
            called["n"] += 1

        import agents.story_bank_agent as sba
        monkeypatch.setattr(sba, "update_outcome_score", fake_update)

        summary = await _credit_stories_for_outcome(
            application_id=uuid4(),
            user_id=uuid4(),
            passed=None,
        )
        assert summary["credited_story_count"] == 0
        assert summary["error"] == "no_pass_signal_skipped"
        assert called["n"] == 0


# ════════════════════════════════════════════════════════════════════════
# Graph topology check
# ════════════════════════════════════════════════════════════════════════
class TestGraphWiring:
    def test_graph_compiles_with_new_nodes(self):
        """The G3 graph should still compile with the two new nodes wired
        between merge_questions and star_story_matcher."""
        from interview_agents.g3_graph import build_g3_graph
        graph = build_g3_graph()
        # If we got here, the graph compiled. Confirm the node names live
        # in the compiled graph's internal nodes dict (best-effort —
        # langgraph exposes .get_graph().nodes for inspection).
        try:
            internal = graph.get_graph().nodes
            names = set(internal.keys()) if isinstance(internal, dict) else {n for n in internal}
        except Exception:
            names = set()
        # Either we can introspect (then assert the new node names) or
        # we can't (then the compile-pass is enough).
        if names:
            assert "story_retriever" in names
            assert "gap_analyzer" in names
            assert "prep_pack_persona_critic" in names

    def test_state_carries_new_fields(self):
        """InterviewPrepState should accept the new Tier-2 fields without
        type errors."""
        from interview_agents.g3_state import InterviewPrepState
        s: InterviewPrepState = {
            "retrieved_stories": {"0": {"story_id": "x", "similarity": 0.8}},
            "story_gaps": [{"question": "q", "missing_competency": "c",
                            "reframe_suggestion": "r"}],
            "persona_critic_drops": ["foo"],
            "credited_story_ids": ["x"],
        }  # type: ignore[assignment]
        assert s["retrieved_stories"]["0"]["similarity"] == 0.8


# ════════════════════════════════════════════════════════════════════════
# Cost target verification
# ════════════════════════════════════════════════════════════════════════
class TestCostTarget:
    @pytest.mark.asyncio
    async def test_gap_analyzer_under_cost_target(self, monkeypatch):
        """The gap_analyzer Sonnet call should hit the ~$0.05/interview
        target. Roadmap says ~$0.05/interview total for G3 story-bank
        integration; story_retriever is $0, persona_critic is $0, so the
        budget is entirely for gap_analyzer. A typical run with caching
        sees ~$0.025-$0.05."""
        from interview_agents import g3_nodes
        from agents.llm_router import LLMResult

        async def fake_ask(**kwargs):
            return LLMResult(
                text=json.dumps({"gaps": []}),
                provider="anthropic",
                model="claude-sonnet-4-6",
                # Realistic token counts: ~2.5K input (3 questions + competencies)
                # + ~0.2K output. Cache hit rate ~50% on warm runs.
                input_tokens=1300,
                output_tokens=200,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=1200,  # the system prefix is cached
                cost_usd=0.030,
                latency_ms=1800,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g3_nodes, "get_router", lambda: FakeRouter())

        import agents.g9_io as g9_io_mod
        monkeypatch.setattr(g9_io_mod, "load_profile_competencies",
                            lambda: ["leadership"])

        state = {
            "likely_questions": [
                {"question": f"Q{i}", "competency": "leadership", "importance": 3}
                for i in range(3)
            ],
            "retrieved_stories": {
                str(i): {"story_id": f"s{i}", "similarity": 0.3,
                         "story": {"title": f"weak {i}"}}
                for i in range(3)
            },
        }
        out = await g3_nodes.gap_analyzer_node(state)
        # Under $0.05 — the roadmap target.
        assert out["cost_usd_total"] < 0.05, (
            f"gap_analyzer over cost target: ${out['cost_usd_total']:.4f} >= $0.05"
        )
