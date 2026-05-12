"""Phase 1.2 unit tests — G9 STAR+R extractor.

Covers:
  - State schema (TypedDict total=False, helpers truncate / make_turn)
  - experience_parser_node:
      * empty CV degrades gracefully (no LLM call needed for the empty path)
      * mocked LLM returns 3+ candidates
      * malformed JSON falls back to []
  - star_formatter_node:
      * batched JSON → typed list of StoryJSON
      * source_text carries forward to the persist step
  - persona_critic_node:
      * high-severity warnings annotate stories with _persona_drop
      * empty input → no warnings
  - persist_node:
      * identity-lock drops fabricated metrics
      * persona drops keep audit trail (stories_dropped)
      * upsert called with required fields
  - Graph compile: build_g9_graph produces a callable graph

NO live API calls. Router is mocked; Supabase is mocked. The `supabase`
package is stubbed at top of file so db.client imports work without it
being pip-installed locally.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Ensure the project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub the supabase package so db.client's top-level import succeeds.
sys.modules.setdefault("supabase", MagicMock())


@pytest.fixture(autouse=True)
def _env():
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")
    os.environ.setdefault("SUPABASE_URL", "http://test")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
    os.environ.setdefault("RIZWAN_USER_ID", "00000000-0000-0000-0000-000000000001")


# ─── State schema ─────────────────────────────────────────────────────────
class TestStoryExtractionState:
    def test_typeddict_is_partial(self):
        from agents.g9_state import StoryExtractionState
        s: StoryExtractionState = {}  # type: ignore[assignment]
        assert isinstance(s, dict)

    def test_make_turn_truncates_input(self):
        from agents.g9_state import make_turn
        long_input = "x" * 1000
        turn = make_turn(node="experience_parser", input_summary=long_input)
        assert len(turn["input_summary"]) <= 500
        assert turn["node"] == "experience_parser"

    def test_make_turn_defaults(self):
        from agents.g9_state import make_turn
        turn = make_turn(node="entry")
        assert turn["node"] == "entry"
        assert turn["iteration"] == 0
        assert turn["cost_usd"] == 0.0
        assert turn["error"] is None

    def test_truncate_helper(self):
        from agents.g9_state import truncate
        assert truncate("") == ""
        assert truncate("abc") == "abc"
        assert truncate("a" * 600, max_chars=100).endswith("...")
        assert len(truncate("a" * 600, max_chars=100)) == 100


# ─── identity-lock unit ────────────────────────────────────────────────────
class TestIdentityLock:
    def test_metric_in_source_passes(self):
        from agents.g9_nodes import _identity_lock
        kept, dropped = _identity_lock(
            ["$50M TPV", "3x conversion"],
            "Grew TPV to $50M and 3x conversion in 18 months",
        )
        assert set(kept) == {"$50M TPV", "3x conversion"}
        assert dropped == []

    def test_fabricated_metric_dropped(self):
        from agents.g9_nodes import _identity_lock
        kept, dropped = _identity_lock(
            ["$50M TPV", "$200M TPV"],   # second is fabricated
            "Grew TPV to $50M in 18 months",
        )
        assert kept == ["$50M TPV"]
        assert dropped == ["$200M TPV"]

    def test_empty_metrics_returns_empty(self):
        from agents.g9_nodes import _identity_lock
        assert _identity_lock([], "anything") == ([], [])

    def test_case_insensitive_match(self):
        from agents.g9_nodes import _identity_lock
        kept, _ = _identity_lock(
            ["$50m tpv"],
            "Grew TPV to $50M",
        )
        assert kept == ["$50m tpv"]

    def test_token_match_for_rearranged_phrasing(self):
        """Source: 'TPV grew by $50M' — metric: '$50M TPV' — should still match."""
        from agents.g9_nodes import _identity_lock
        kept, dropped = _identity_lock(
            ["$50M TPV"],
            "TPV grew by $50M over two quarters",
        )
        assert kept == ["$50M TPV"]
        assert dropped == []


# ─── experience_parser_node ────────────────────────────────────────────────
class TestExperienceParserNode:
    @pytest.mark.asyncio
    async def test_empty_cv_returns_no_candidates(self):
        from agents.g9_nodes import experience_parser_node
        out = await experience_parser_node({"master_cv_md": ""})
        assert out["candidate_passages"] == []
        # No cost / latency because we short-circuited.
        assert "total_cost_usd" not in out or out.get("total_cost_usd", 0) == 0

    @pytest.mark.asyncio
    async def test_mocked_llm_returns_three_candidates(self):
        from agents.g9_nodes import experience_parser_node
        from agents.llm_router import LLMResult

        fake_router = MagicMock()
        fake_router.ask = AsyncMock(return_value=LLMResult(
            text=json.dumps({
                "candidates": [
                    {
                        "passage_id": "p1",
                        "source_experience_id": 7,
                        "role_title": "CPO",
                        "company": "Simpaisa",
                        "dates": "2020-Present",
                        "source_text": "Grew TPV to $50M in 18 months",
                        "rough_title": "Scaled cross-border TPV",
                    },
                    {
                        "passage_id": "p2",
                        "source_experience_id": None,
                        "role_title": "Head of Product",
                        "company": "Daraz",
                        "dates": "2018-2020",
                        "source_text": "Launched BNPL category, 3x conversion uplift",
                        "rough_title": "BNPL launch at Daraz",
                    },
                    {
                        "passage_id": "p3",
                        "source_experience_id": None,
                        "role_title": "PM",
                        "company": "Careem",
                        "dates": "2016-2018",
                        "source_text": "Owned merchant onboarding flow used by 200 SMBs",
                        "rough_title": "Merchant onboarding scale",
                    },
                ]
            }),
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.012,
            latency_ms=4200,
        ))

        with patch("agents.g9_nodes.get_router", return_value=fake_router), \
             patch("agents.g9_nodes.load_experiences", return_value=[]):
            out = await experience_parser_node({
                "master_cv_md": "# Test\n## Experience\n### CPO @ Simpaisa\nGrew TPV...",
            })

        assert len(out["candidate_passages"]) == 3
        c1 = out["candidate_passages"][0]
        assert c1["passage_id"] == "p1"
        assert c1["source_experience_id"] == 7
        assert c1["source_text"].startswith("Grew TPV")
        assert out["total_cost_usd"] == pytest.approx(0.012, rel=1e-3)

    @pytest.mark.asyncio
    async def test_malformed_json_returns_no_candidates(self):
        from agents.g9_nodes import experience_parser_node
        from agents.llm_router import LLMResult

        fake_router = MagicMock()
        fake_router.ask = AsyncMock(return_value=LLMResult(
            text="this is not JSON at all",
            provider="anthropic",
            model="claude-sonnet-4-6",
        ))

        with patch("agents.g9_nodes.get_router", return_value=fake_router), \
             patch("agents.g9_nodes.load_experiences", return_value=[]):
            out = await experience_parser_node({
                "master_cv_md": "# Some CV\nWith some text",
            })
        assert out["candidate_passages"] == []


# ─── star_formatter_node ───────────────────────────────────────────────────
class TestStarFormatterNode:
    @pytest.mark.asyncio
    async def test_no_candidates_short_circuits(self):
        from agents.g9_nodes import star_formatter_node
        out = await star_formatter_node({"candidate_passages": []})
        assert out["stories"] == []

    @pytest.mark.asyncio
    async def test_batched_json_decoded_and_typed(self):
        from agents.g9_nodes import star_formatter_node
        from agents.llm_router import LLMResult

        candidates = [{
            "passage_id": "p1",
            "source_text": "Grew TPV to $50M in 18 months",
            "rough_title": "Scaled TPV",
            "role_title": "CPO",
            "company": "Simpaisa",
            "dates": "2020-",
            "source_experience_id": 7,
        }]
        fake_router = MagicMock()
        fake_router.ask = AsyncMock(return_value=LLMResult(
            text=json.dumps({
                "stories": [
                    {
                        "passage_id": "p1",
                        "title": "Scaled cross-border TPV to $50M",
                        "situation": "S",
                        "task": "T",
                        "action": "A",
                        "result": "R, $50M TPV",
                        "reflection": "RR",
                        "competencies": ["leadership", "execution"],
                        "archetypes": ["CPO", "PM"],
                        "quantified_metrics": ["$50M TPV", "18 months"],
                    }
                ]
            }),
            provider="anthropic",
            model="claude-sonnet-4-6",
            input_tokens=1500,
            output_tokens=800,
            cost_usd=0.018,
            latency_ms=5200,
        ))

        with patch("agents.g9_nodes.get_router", return_value=fake_router):
            out = await star_formatter_node({"candidate_passages": candidates})

        assert len(out["stories"]) == 1
        story = out["stories"][0]
        assert story["title"].startswith("Scaled cross-border")
        assert story["competencies"] == ["leadership", "execution"]
        # source_text MUST carry forward for the persist node's identity-lock
        assert story["source_text"] == "Grew TPV to $50M in 18 months"


# ─── persona_critic_node ───────────────────────────────────────────────────
class TestPersonaCriticNode:
    @pytest.mark.asyncio
    async def test_empty_stories_returns_zero_warnings(self):
        from agents.g9_nodes import persona_critic_node
        out = await persona_critic_node({"stories": [], "profile_competencies": []})
        assert out["persona_warnings"] == []

    @pytest.mark.asyncio
    async def test_high_severity_warning_drops_story(self):
        from agents.g9_nodes import persona_critic_node
        from agents.llm_router import LLMResult

        stories = [
            {"passage_id": "p1", "title": "OK story", "competencies": ["leadership"],
             "archetypes": ["PM"], "action": ""},
            {"passage_id": "p2", "title": "ML claim", "competencies": ["ml_ai"],
             "archetypes": ["CPO"], "action": ""},
        ]
        fake_router = MagicMock()
        fake_router.ask = AsyncMock(return_value=LLMResult(
            text=json.dumps({
                "warnings": [
                    {
                        "passage_id": "p2",
                        "title": "ML claim",
                        "reason": "ml_ai tag but no ML systems in CV",
                        "severity": "high",
                    }
                ]
            }),
            provider="anthropic",
            model="claude-sonnet-4-6",
            cost_usd=0.004,
            latency_ms=1100,
        ))

        with patch("agents.g9_nodes.get_router", return_value=fake_router):
            out = await persona_critic_node({
                "stories": stories,
                "profile_competencies": ["leadership", "product_strategy"],
            })

        # Two stories returned, p2 marked _persona_drop=True, p1 not.
        out_stories = out["stories"]
        assert len(out_stories) == 2
        drops = {s["passage_id"]: s.get("_persona_drop", False) for s in out_stories}
        assert drops["p2"] is True
        assert drops["p1"] is False
        # Warning surfaced for audit
        assert any("p2" in w for w in out["persona_warnings"])
        assert any("ml_ai" in w for w in out["persona_warnings"])


# ─── persist_node ──────────────────────────────────────────────────────────
class TestPersistNode:
    @pytest.mark.asyncio
    async def test_identity_lock_drops_fabricated_metrics(self):
        from agents.g9_nodes import persist_node

        captured = {}

        async def fake_upsert(**kwargs):
            captured.update(kwargs)
            return {"id": "story-1", "title": kwargs["title"]}

        async def fake_embed(_story):
            return [0.0] * 1536

        state = {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "source_cv_hash": "abc",
            "candidate_passages": [],
            "stories": [{
                "passage_id": "p1",
                "title": "Scaled TPV",
                "situation": "S", "task": "T", "action": "A", "result": "R",
                "reflection": "RR",
                "competencies": ["leadership"], "archetypes": ["PM"],
                "quantified_metrics": ["$50M TPV", "$200M TPV"],   # 2nd fabricated
                "source_text": "Grew TPV to $50M in 18 months",
                "_persona_drop": False,
            }],
        }

        with patch("agents.g9_nodes.upsert_story", new=fake_upsert), \
             patch("agents.g9_nodes.embed_story_text", new=fake_embed):
            out = await persist_node(state)

        assert out["persisted_count"] == 1
        assert out["dropped_count"] == 0
        # The fabricated $200M was scrubbed before upsert
        assert captured["quantified_metrics"] == ["$50M TPV"]
        # Audit trail records the bad metric
        assert "$200M TPV" in out["stories_persisted"][0]["dropped_metrics"]

    @pytest.mark.asyncio
    async def test_persona_drop_does_not_persist(self):
        from agents.g9_nodes import persist_node

        upserts: list = []

        async def fake_upsert(**kwargs):
            upserts.append(kwargs)
            return {"id": "story-x"}

        async def fake_embed(_story):
            return [0.0] * 1536

        state = {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "candidate_passages": [],
            "stories": [{
                "passage_id": "p1",
                "title": "Fabricated", "situation": "S", "task": "T",
                "action": "A", "result": "R",
                "competencies": ["ml_ai"],
                "quantified_metrics": [],
                "source_text": "...",
                "_persona_drop": True,
            }],
        }

        with patch("agents.g9_nodes.upsert_story", new=fake_upsert), \
             patch("agents.g9_nodes.embed_story_text", new=fake_embed):
            out = await persist_node(state)

        assert out["persisted_count"] == 0
        assert out["dropped_count"] == 1
        assert upserts == []  # never called
        assert "persona_critic" in out["stories_dropped"][0]["reason"]

    @pytest.mark.asyncio
    async def test_incomplete_star_dropped(self):
        from agents.g9_nodes import persist_node

        async def fake_upsert(**kwargs):
            return {"id": "x"}

        async def fake_embed(_):
            return [0.0] * 1536

        state = {
            "user_id": "00000000-0000-0000-0000-000000000001",
            "candidate_passages": [],
            "stories": [{
                "passage_id": "p1",
                "title": "Empty Action",
                "situation": "S", "task": "T",
                "action": "",  # missing!
                "result": "R",
                "competencies": [], "quantified_metrics": [],
                "source_text": "x", "_persona_drop": False,
            }],
        }
        with patch("agents.g9_nodes.upsert_story", new=fake_upsert), \
             patch("agents.g9_nodes.embed_story_text", new=fake_embed):
            out = await persist_node(state)
        assert out["persisted_count"] == 0
        assert out["dropped_count"] == 1
        assert "incomplete STAR" in out["stories_dropped"][0]["reason"]

    @pytest.mark.asyncio
    async def test_missing_user_id_raises(self):
        from agents.g9_nodes import persist_node
        with pytest.raises(RuntimeError, match="user_id"):
            await persist_node({"stories": []})


# ─── Graph compile ─────────────────────────────────────────────────────────
class TestGraphCompile:
    def test_build_g9_graph_returns_callable_graph(self):
        from agents.g9_graph import build_g9_graph
        graph = build_g9_graph(checkpointer=None)
        assert graph is not None
        # langgraph compiled graphs have `ainvoke` for async execution
        assert hasattr(graph, "ainvoke") or hasattr(graph, "invoke")
