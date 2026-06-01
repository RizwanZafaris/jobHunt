"""Phase 1.2 unit tests — story_bank_agent CRUD + semantic search.

Covers:
  - _row_to_story coercion (handles missing Phase-1.2 columns)
  - list_stories / get_story (mocked Supabase chain)
  - update_outcome_score atomic-ish increment
  - search_stories:
      * empty query short-circuits
      * embedding-call failure degrades to chronological fallback
      * cosine ranking returns highest similarity first
      * outcome_score boost breaks ties
  - _cosine numerical correctness
  - upsert_story_from_dict integration with mocked embed + upsert

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


# ─── _row_to_story ─────────────────────────────────────────────────────────
class TestRowToStory:
    def test_full_row_coerces_cleanly(self):
        from agents.story_bank_agent import _row_to_story
        row = {
            "id": "abc", "user_id": RIZWAN_UUID,
            "title": "Scaled TPV", "situation": "S", "task": "T",
            "action": "A", "result": "R", "reflection": "RR",
            "competencies": ["leadership"], "archetypes": ["CPO"],
            "quantified_metrics": ["$50M"], "outcome_score": 7,
            "source_cv_hash": "h", "source_experience_id": 4,
            "source_text": "src",
            "created_at": "2026-05-12T00:00:00+00:00",
            "updated_at": "2026-05-12T00:00:00+00:00",
        }
        s = _row_to_story(row)
        assert s.id == "abc"
        assert s.competencies == ["leadership"]
        assert s.outcome_score == 7

    def test_legacy_row_with_missing_phase_12_columns(self):
        """A row from before migration 015 should still coerce safely."""
        from agents.story_bank_agent import _row_to_story
        row = {"id": "x", "user_id": RIZWAN_UUID, "title": "Old"}
        s = _row_to_story(row)
        assert s.competencies == []
        assert s.outcome_score == 0
        assert s.source_text is None


# ─── _cosine ────────────────────────────────────────────────────────────────
class TestCosine:
    def test_identical_vectors_score_close_to_one(self):
        from agents.story_bank_agent import _cosine
        a = [1.0, 0.0, 0.0]
        # OpenAI returns unit vectors so the dot product IS the cosine.
        assert _cosine(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        from agents.story_bank_agent import _cosine
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_empty_returns_zero(self):
        from agents.story_bank_agent import _cosine
        assert _cosine([], []) == 0.0
        assert _cosine([1.0], []) == 0.0


# ─── list_stories ──────────────────────────────────────────────────────────
class TestListStories:
    @pytest.mark.asyncio
    async def test_returns_typed_rows(self):
        from agents import story_bank_agent

        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.order.return_value.order.return_value.limit \
             .return_value.execute.return_value.data = [
                {"id": "1", "user_id": RIZWAN_UUID, "title": "A",
                 "outcome_score": 3, "competencies": ["leadership"]},
                {"id": "2", "user_id": RIZWAN_UUID, "title": "B",
                 "outcome_score": 1},
             ]

        with patch("agents.story_bank_agent.get_supabase", return_value=fake_db) \
                if hasattr(story_bank_agent, "get_supabase") \
                else patch("db.client.get_supabase", return_value=fake_db):
            rows = await story_bank_agent.list_stories(RIZWAN_UUID, limit=20)

        assert len(rows) == 2
        assert rows[0].title == "A"
        assert rows[0].outcome_score == 3

    @pytest.mark.asyncio
    async def test_competency_filter_invokes_contains(self):
        from agents import story_bank_agent

        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.order.return_value.order.return_value.limit.return_value \
             .contains.return_value.execute.return_value.data = []
        chain.eq.return_value.order.return_value.order.return_value.limit.return_value \
             .execute.return_value.data = []

        with patch("db.client.get_supabase", return_value=fake_db):
            await story_bank_agent.list_stories(
                RIZWAN_UUID, limit=10, competency="leadership"
            )

        # Confirm the `.contains` call was made on the chained query — the
        # GIN pre-filter path was exercised.
        contains_calls = chain.eq.return_value.order.return_value.order.return_value.limit \
                              .return_value.contains.call_args_list
        # We don't strictly require the exact call shape; just that the
        # method was reached.
        assert chain.eq.return_value.order.return_value.order.return_value.limit.return_value.contains.called


# ─── get_story ──────────────────────────────────────────────────────────────
class TestGetStory:
    @pytest.mark.asyncio
    async def test_found_returns_story(self):
        from agents import story_bank_agent

        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.limit.return_value.execute \
             .return_value.data = [
                {"id": "abc", "user_id": RIZWAN_UUID, "title": "T"}
             ]
        with patch("db.client.get_supabase", return_value=fake_db):
            row = await story_bank_agent.get_story(RIZWAN_UUID, "abc")
        assert row is not None
        assert row.id == "abc"

    @pytest.mark.asyncio
    async def test_missing_returns_none(self):
        from agents import story_bank_agent

        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.limit.return_value.execute \
             .return_value.data = []
        with patch("db.client.get_supabase", return_value=fake_db):
            row = await story_bank_agent.get_story(RIZWAN_UUID, "missing")
        assert row is None


# ─── update_outcome_score ───────────────────────────────────────────────────
class TestUpdateOutcomeScore:
    @pytest.mark.asyncio
    async def test_increment_clamps_at_zero(self):
        from agents import story_bank_agent

        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.limit.return_value.execute.return_value.data = [
            {"outcome_score": 0}
        ]
        upd_call = (
            fake_db.table.return_value.update.return_value
            .eq.return_value.execute
        )
        with patch("db.client.get_supabase", return_value=fake_db):
            await story_bank_agent.update_outcome_score("s1", -5)

        # update().eq().execute() was called — confirm the new value was 0
        # (clamped) by inspecting the call args to update().
        update_call_args = fake_db.table.return_value.update.call_args
        assert update_call_args.args[0] == {"outcome_score": 0}

    @pytest.mark.asyncio
    async def test_increment_adds_delta(self):
        from agents import story_bank_agent

        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.limit.return_value.execute.return_value.data = [
            {"outcome_score": 3}
        ]
        with patch("db.client.get_supabase", return_value=fake_db):
            await story_bank_agent.update_outcome_score("s1", 2)

        update_call_args = fake_db.table.return_value.update.call_args
        assert update_call_args.args[0] == {"outcome_score": 5}


# ─── search_stories ─────────────────────────────────────────────────────────
class TestSearchStories:
    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        from agents import story_bank_agent
        assert await story_bank_agent.search_stories(RIZWAN_UUID, "") == []
        assert await story_bank_agent.search_stories(RIZWAN_UUID, "   ") == []

    @pytest.mark.asyncio
    async def test_embedding_failure_falls_back_to_list(self):
        """If embed() raises, we degrade to a chronological list."""
        from agents import story_bank_agent

        # Make embed raise; ensure list_stories path is taken.
        with patch("db.client.embed", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("agents.story_bank_agent.list_stories",
                   new=AsyncMock(return_value=[
                       story_bank_agent.StoryBankRow(
                           id="1", user_id=RIZWAN_UUID, title="T",
                           situation="", task="", action="", result="", reflection="",
                       )
                   ])):
            result = await story_bank_agent.search_stories(
                RIZWAN_UUID, "leadership story"
            )

        assert len(result) == 1
        assert result[0].story.title == "T"
        assert result[0].similarity == 0.0

    @pytest.mark.asyncio
    async def test_cosine_ranking_orders_by_similarity(self):
        from agents import story_bank_agent

        # Build two stories with embeddings, query embedding identical to story B.
        story_a_emb = [1.0, 0.0]
        story_b_emb = [0.0, 1.0]
        query_emb = [0.0, 1.0]

        fake_db = MagicMock()
        # RPC layer (migration 018) returns no rows here, so the client-side
        # cosine fallback that this test sets up below is what runs.
        fake_db.rpc.return_value.execute.return_value.data = []
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.not_.is_.return_value.execute.return_value.data = [
            {"id": "a", "user_id": RIZWAN_UUID, "title": "A",
             "outcome_score": 0, "embedding": story_a_emb},
            {"id": "b", "user_id": RIZWAN_UUID, "title": "B",
             "outcome_score": 0, "embedding": story_b_emb},
        ]

        with patch("db.client.embed", new=AsyncMock(return_value=query_emb)), \
             patch("db.client.get_supabase", return_value=fake_db):
            # Patch the helper's _cosine to be the actual implementation
            # so we exercise the real ranking logic.
            matches = await story_bank_agent.search_stories(
                RIZWAN_UUID, "the B topic", k=2
            )

        assert len(matches) == 2
        # Story B is identical to the query → wins. Story A is orthogonal → 0.
        assert matches[0].story.id == "b"
        assert matches[0].similarity == pytest.approx(1.0, abs=1e-3)
        assert matches[1].story.id == "a"

    @pytest.mark.asyncio
    async def test_outcome_score_breaks_ties(self):
        from agents import story_bank_agent

        emb = [1.0, 0.0]
        fake_db = MagicMock()
        # RPC layer (migration 018) returns no rows here, so the client-side
        # cosine fallback that this test sets up below is what runs.
        fake_db.rpc.return_value.execute.return_value.data = []
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.not_.is_.return_value.execute.return_value.data = [
            {"id": "a", "user_id": RIZWAN_UUID, "title": "A",
             "outcome_score": 0, "embedding": emb},
            {"id": "b", "user_id": RIZWAN_UUID, "title": "B (more credit)",
             "outcome_score": 10, "embedding": emb},
        ]
        with patch("db.client.embed", new=AsyncMock(return_value=emb)), \
             patch("db.client.get_supabase", return_value=fake_db):
            matches = await story_bank_agent.search_stories(
                RIZWAN_UUID, "tie story", k=2
            )

        # Tie on cosine — outcome_score boost wins.
        assert matches[0].story.id == "b"


# ─── upsert_story_from_dict ────────────────────────────────────────────────
class TestUpsertStoryFromDict:
    @pytest.mark.asyncio
    async def test_returns_id_from_underlying_upsert(self):
        from agents import story_bank_agent

        async def fake_upsert(**kwargs):
            assert kwargs["user_id"] == RIZWAN_UUID
            assert kwargs["title"] == "Scaled TPV"
            assert kwargs["embedding"] is not None
            return {"id": "new-id"}

        async def fake_embed_text(_s):
            return [0.0] * 1536

        with patch("agents.g9_io.upsert_story", new=fake_upsert), \
             patch("agents.g9_io.embed_story_text", new=fake_embed_text):
            sid = await story_bank_agent.upsert_story_from_dict(
                RIZWAN_UUID,
                {
                    "title": "Scaled TPV",
                    "situation": "S", "task": "T", "action": "A",
                    "result": "R", "reflection": "RR",
                    "competencies": ["leadership"],
                    "quantified_metrics": ["$50M"],
                    "source_text": "Grew TPV to $50M",
                },
            )
        assert sid == "new-id"
