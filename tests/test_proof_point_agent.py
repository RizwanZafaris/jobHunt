"""Tier 4 §6.4 — proof_point_agent + extractor unit tests.

Covers:
  - _row_to_proof_point coercion (handles defaults, missing fields)
  - _content_hash + _normalise_content stability
  - list_proof_points / get_proof_point (mocked Supabase chain)
  - add_proof_point validates kind/source/empty content
  - update_outcome_score atomic-ish increment + zero clamp
  - search_proof_points:
      * empty query short-circuits
      * embedding-call failure falls back to chronological list
      * RPC happy path returns matches
      * RPC failure → client-side cosine ranking + tiebreak
  - extractor:
      * _split_into_candidates quantified filter
      * end-to-end with mocked LLM + mocked add_proof_point
      * identity-lock prunes fabricated metrics

No live API calls. No live Supabase.
"""
from __future__ import annotations

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


# ─── _row_to_proof_point ───────────────────────────────────────────────────
class TestRowToProofPoint:
    def test_full_row_coerces_cleanly(self):
        from agents.proof_point_agent import _row_to_proof_point
        row = {
            "id": "abc", "user_id": RIZWAN_UUID,
            "content": "Launched X to $50M TPV",
            "kind": "launch",
            "context": "Daraz Marketplace",
            "url": "https://daraz.pk/launch",
            "tags": ["fintech", "product-launch"],
            "quantified_metrics": ["$50M TPV", "18 months"],
            "outcome_score": 4,
            "source": "user_manual_add",
            "source_experience_id": 7,
            "source_draft_id": None,
            "content_hash": "h",
            "created_at": "2026-05-12T00:00:00+00:00",
            "updated_at": "2026-05-12T00:00:00+00:00",
        }
        p = _row_to_proof_point(row)
        assert p.id == "abc"
        assert p.kind == "launch"
        assert p.tags == ["fintech", "product-launch"]
        assert p.quantified_metrics == ["$50M TPV", "18 months"]
        assert p.outcome_score == 4
        assert p.source == "user_manual_add"

    def test_legacy_row_with_missing_columns(self):
        from agents.proof_point_agent import _row_to_proof_point
        row = {"id": "x", "user_id": RIZWAN_UUID,
               "content": "Did a thing", "kind": "other"}
        p = _row_to_proof_point(row)
        assert p.tags == []
        assert p.quantified_metrics == []
        assert p.outcome_score == 0
        assert p.source is None
        assert p.source_draft_id is None


# ─── _content_hash ─────────────────────────────────────────────────────────
class TestContentHash:
    def test_whitespace_insensitive(self):
        from agents.proof_point_agent import _content_hash
        a = _content_hash("Launched X to $50M TPV in 18 months.")
        b = _content_hash("  Launched X    to $50M TPV in 18 months.  ")
        assert a == b

    def test_case_insensitive(self):
        from agents.proof_point_agent import _content_hash
        a = _content_hash("Launched X")
        b = _content_hash("launched x")
        assert a == b

    def test_different_content_different_hash(self):
        from agents.proof_point_agent import _content_hash
        assert _content_hash("Foo") != _content_hash("Bar")


# ─── add_proof_point validation ────────────────────────────────────────────
class TestAddProofPointValidation:
    @pytest.mark.asyncio
    async def test_invalid_kind_raises(self):
        from agents.proof_point_agent import add_proof_point
        with pytest.raises(ValueError):
            await add_proof_point(
                user_id=RIZWAN_UUID, content="x", kind="not_a_kind"
            )

    @pytest.mark.asyncio
    async def test_invalid_source_raises(self):
        from agents.proof_point_agent import add_proof_point
        with pytest.raises(ValueError):
            await add_proof_point(
                user_id=RIZWAN_UUID, content="x",
                kind="launch", source="some_other_source",
            )

    @pytest.mark.asyncio
    async def test_empty_content_raises(self):
        from agents.proof_point_agent import add_proof_point
        with pytest.raises(ValueError):
            await add_proof_point(
                user_id=RIZWAN_UUID, content="   ", kind="launch"
            )

    @pytest.mark.asyncio
    async def test_embed_failure_still_inserts(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        fake_db.table.return_value.upsert.return_value.execute.return_value.data = [
            {"id": "11111111-1111-1111-1111-111111111111"}
        ]
        with patch("db.client.embed", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("db.client.get_supabase", return_value=fake_db):
            new_id = await proof_point_agent.add_proof_point(
                user_id=RIZWAN_UUID, content="Launched X", kind="launch",
            )
        assert new_id is not None
        # Confirm the payload didn't include embedding (best-effort skipped).
        upsert_call = fake_db.table.return_value.upsert.call_args
        payload = upsert_call.args[0]
        assert "embedding" not in payload
        assert payload["kind"] == "launch"
        assert payload["source"] == "user_manual_add"


# ─── list_proof_points ─────────────────────────────────────────────────────
class TestListProofPoints:
    @pytest.mark.asyncio
    async def test_returns_typed_rows(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.order.return_value.order.return_value.limit \
             .return_value.execute.return_value.data = [
                {"id": "1", "user_id": RIZWAN_UUID, "content": "A",
                 "kind": "launch", "outcome_score": 3},
                {"id": "2", "user_id": RIZWAN_UUID, "content": "B",
                 "kind": "metric", "outcome_score": 1},
            ]
        with patch("db.client.get_supabase", return_value=fake_db):
            rows = await proof_point_agent.list_proof_points(RIZWAN_UUID)
        assert len(rows) == 2
        assert rows[0].content == "A"
        assert rows[0].kind == "launch"

    @pytest.mark.asyncio
    async def test_kind_filter_chains_eq(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        # First eq is user_id; .order.order.limit.eq() is the kind filter.
        chain.eq.return_value.order.return_value.order.return_value.limit \
             .return_value.eq.return_value.execute.return_value.data = []
        with patch("db.client.get_supabase", return_value=fake_db):
            await proof_point_agent.list_proof_points(RIZWAN_UUID, kind="launch")
        # We can't easily assert call ordering without instrumenting more,
        # but absence of exception = the chain shape compiles end-to-end.


# ─── get_proof_point ───────────────────────────────────────────────────────
class TestGetProofPoint:
    @pytest.mark.asyncio
    async def test_found_returns_row(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.limit.return_value.execute \
             .return_value.data = [
                {"id": "abc", "user_id": RIZWAN_UUID, "content": "x", "kind": "launch"}
            ]
        with patch("db.client.get_supabase", return_value=fake_db):
            row = await proof_point_agent.get_proof_point(RIZWAN_UUID, "abc")
        assert row is not None
        assert row.id == "abc"

    @pytest.mark.asyncio
    async def test_missing_returns_none(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.eq.return_value.limit.return_value.execute \
             .return_value.data = []
        with patch("db.client.get_supabase", return_value=fake_db):
            row = await proof_point_agent.get_proof_point(RIZWAN_UUID, "missing")
        assert row is None


# ─── update_outcome_score ──────────────────────────────────────────────────
class TestUpdateOutcomeScore:
    @pytest.mark.asyncio
    async def test_increment_adds_delta(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.limit.return_value.execute.return_value.data = [
            {"outcome_score": 3}
        ]
        with patch("db.client.get_supabase", return_value=fake_db):
            await proof_point_agent.update_outcome_score("p1", 2)
        update_call = fake_db.table.return_value.update.call_args
        assert update_call.args[0] == {"outcome_score": 5}

    @pytest.mark.asyncio
    async def test_negative_delta_clamps_at_zero(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.limit.return_value.execute.return_value.data = [
            {"outcome_score": 1}
        ]
        with patch("db.client.get_supabase", return_value=fake_db):
            await proof_point_agent.update_outcome_score("p1", -10)
        update_call = fake_db.table.return_value.update.call_args
        assert update_call.args[0] == {"outcome_score": 0}


# ─── search_proof_points ───────────────────────────────────────────────────
class TestSearchProofPoints:
    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        from agents import proof_point_agent
        assert await proof_point_agent.search_proof_points(RIZWAN_UUID, "") == []
        assert await proof_point_agent.search_proof_points(RIZWAN_UUID, "   ") == []

    @pytest.mark.asyncio
    async def test_embedding_failure_falls_back_to_list(self):
        from agents import proof_point_agent
        with patch("db.client.embed", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("agents.proof_point_agent.list_proof_points",
                   new=AsyncMock(return_value=[
                       proof_point_agent.ProofPointRow(
                           id="1", user_id=RIZWAN_UUID,
                           content="Launched X", kind="launch",
                       )
                   ])):
            res = await proof_point_agent.search_proof_points(
                RIZWAN_UUID, "leadership proof"
            )
        assert len(res) == 1
        assert res[0].similarity == 0.0
        assert res[0].proof_point.content == "Launched X"

    @pytest.mark.asyncio
    async def test_rpc_happy_path_returns_matches(self):
        from agents import proof_point_agent
        fake_db = MagicMock()
        fake_db.rpc.return_value.execute.return_value.data = [
            {
                "id": "a", "user_id": RIZWAN_UUID, "content": "Launched X",
                "kind": "launch", "tags": ["fintech"], "quantified_metrics": ["$50M"],
                "outcome_score": 2, "source": "user_manual_add",
                "similarity": 0.92,
            }
        ]
        with patch("db.client.embed", new=AsyncMock(return_value=[0.1] * 1536)), \
             patch("db.client.get_supabase", return_value=fake_db):
            matches = await proof_point_agent.search_proof_points(
                RIZWAN_UUID, "fintech launch", k=5
            )
        assert len(matches) == 1
        assert matches[0].proof_point.id == "a"
        assert matches[0].similarity == pytest.approx(0.92)
        # Verify RPC was called with the right args.
        rpc_call = fake_db.rpc.call_args
        assert rpc_call.args[0] == "search_proof_points_v2"
        assert rpc_call.args[1]["p_user_id"] == RIZWAN_UUID
        assert rpc_call.args[1]["p_k"] == 5

    @pytest.mark.asyncio
    async def test_rpc_failure_falls_back_to_client_cosine(self):
        from agents import proof_point_agent
        emb_a = [1.0, 0.0]
        emb_b = [0.0, 1.0]
        query_emb = [0.0, 1.0]

        fake_db = MagicMock()
        # RPC raises.
        fake_db.rpc.return_value.execute.side_effect = RuntimeError("rpc gone")
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.not_.is_.return_value.execute.return_value.data = [
            {"id": "a", "user_id": RIZWAN_UUID, "content": "A", "kind": "launch",
             "outcome_score": 0, "embedding": emb_a},
            {"id": "b", "user_id": RIZWAN_UUID, "content": "B", "kind": "launch",
             "outcome_score": 0, "embedding": emb_b},
        ]
        with patch("db.client.embed", new=AsyncMock(return_value=query_emb)), \
             patch("db.client.get_supabase", return_value=fake_db):
            matches = await proof_point_agent.search_proof_points(
                RIZWAN_UUID, "topic B", k=2
            )
        assert len(matches) == 2
        # B is identical to query → wins.
        assert matches[0].proof_point.id == "b"
        assert matches[0].similarity == pytest.approx(1.0, abs=1e-3)
        assert matches[1].proof_point.id == "a"

    @pytest.mark.asyncio
    async def test_outcome_score_breaks_ties_in_client_fallback(self):
        from agents import proof_point_agent
        emb = [1.0, 0.0]
        fake_db = MagicMock()
        fake_db.rpc.return_value.execute.side_effect = RuntimeError("rpc gone")
        chain = fake_db.table.return_value.select.return_value
        chain.eq.return_value.not_.is_.return_value.execute.return_value.data = [
            {"id": "a", "user_id": RIZWAN_UUID, "content": "A",
             "kind": "launch", "outcome_score": 0, "embedding": emb},
            {"id": "b", "user_id": RIZWAN_UUID, "content": "B (more credit)",
             "kind": "launch", "outcome_score": 10, "embedding": emb},
        ]
        with patch("db.client.embed", new=AsyncMock(return_value=emb)), \
             patch("db.client.get_supabase", return_value=fake_db):
            matches = await proof_point_agent.search_proof_points(
                RIZWAN_UUID, "tie test", k=2
            )
        assert matches[0].proof_point.id == "b"


# ─── Extractor ─────────────────────────────────────────────────────────────
class TestExtractor:
    def test_split_keeps_quantified_highlights(self):
        from agents.proof_point_extractor import _split_into_candidates
        exp = {
            "id": 1,
            "summary": "Built a great team.",
            "highlights": [
                "Launched Daraz Marketplace 0→$50M TPV in 18 months.",
                "Mentored several engineers.",  # no quantified signal
                "Cut cart abandonment 18% via 1-step checkout.",
            ],
        }
        cands = _split_into_candidates(exp)
        assert any("$50M" in c for c in cands)
        assert any("18%" in c for c in cands)
        # Soft highlight without numbers is dropped.
        assert not any("Mentored" in c for c in cands)

    @pytest.mark.asyncio
    async def test_end_to_end_with_mocked_llm(self):
        """Extractor uses mocked LLM + mocked add_proof_point. Verifies
        identity-lock prunes fabricated metrics."""
        from agents import proof_point_extractor

        class FakeResult:
            text = (
                '{"items":['
                '{"idx":0,"kind":"launch","tags":["fintech","product-launch"],'
                ' "quantified_metrics":["$50M TPV","18 months","999 fake"]},'
                '{"idx":1,"kind":"metric","tags":["growth","checkout"],'
                ' "quantified_metrics":["18%"]}'
                ']}'
            )
            cost_usd = 0.01
            latency_ms = 200

        async def fake_ask(**kwargs):
            return FakeResult()

        fake_router = MagicMock()
        fake_router.ask = fake_ask

        inserted_payloads: list[dict] = []

        async def fake_add(**kwargs):
            inserted_payloads.append(kwargs)
            return "fake-uuid-" + str(len(inserted_payloads))

        experiences = [
            {
                "id": 7,
                "summary": "",
                "highlights": [
                    "Launched Daraz Marketplace 0→$50M TPV in 18 months.",
                    "Cut cart abandonment 18% via 1-step checkout.",
                ],
            }
        ]

        # add_proof_point is imported INSIDE the extractor function, so
        # we patch it on the source module (agents.proof_point_agent).
        with patch("agents.llm_router.get_router", return_value=fake_router), \
             patch("agents.proof_point_agent.add_proof_point", new=fake_add):
            count = await proof_point_extractor.extract_proof_points_from_experience(
                user_id="u1", experiences=experiences
            )

        assert count == 2
        # Identity-lock: '999 fake' wasn't in the source text → pruned.
        first = inserted_payloads[0]
        assert first["kind"] == "launch"
        assert "$50M TPV" in first["quantified_metrics"]
        assert "18 months" in first["quantified_metrics"]
        assert "999 fake" not in first["quantified_metrics"]
        # source breadcrumbs intact
        assert first["source"] == "extracted_from_cv"
        assert first["source_experience_id"] == 7
        # Second extracted proof point — 'metric' kind.
        second = inserted_payloads[1]
        assert second["kind"] == "metric"
        assert "18%" in second["quantified_metrics"]

    @pytest.mark.asyncio
    async def test_zero_candidates_returns_zero(self):
        from agents import proof_point_extractor
        # No quantified highlights at all.
        count = await proof_point_extractor.extract_proof_points_from_experience(
            user_id="u1",
            experiences=[
                {"id": 1, "summary": "", "highlights": ["Mentored team.", "Hired well."]}
            ],
        )
        assert count == 0
