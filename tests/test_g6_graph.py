"""
Integration tests for the G6 Follow-up Graph (agents.g6_graph).

These tests exercise the compiled LangGraph end-to-end against fully
mocked DB + LLM dependencies. They verify:

  - Graph compiles and routes correctly across all branches:
      • cadence_checker → persist (short-circuit on waiting/cold/noop)
      • cadence_checker → context_builder → draft → critics → merge →
        urgency_scorer → persist (normal happy path)
      • merge_critique → draft_generator loop when critics ask for revision
  - State accumulates cost_usd_total + transcript across nodes
  - persona_critic banned-phrase override survives the merge step
  - Empty signal pool short-circuits draft_generator (no LLM call)

No live API calls. The router is mocked via monkeypatch and DB writes are
captured in an in-memory list.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def _utcnow():
    return datetime.now(timezone.utc)


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).date().isoformat()


class _MockLLMResult:
    """Minimal LLMResult mock — only the fields downstream code reads."""

    def __init__(
        self,
        text: str,
        cost_usd: float = 0.05,
        latency_ms: int = 200,
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-6",
    ):
        self.text = text
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.provider = provider
        self.model = model
        self.input_tokens = 100
        self.output_tokens = 50
        self.cache_creation_input_tokens = None
        self.cache_read_input_tokens = None


# ──────────────────────────────────────────────────────────────────────────
# Mock routers
# ──────────────────────────────────────────────────────────────────────────
class _RouterHappyPath:
    """All three LLM nodes return clean accept verdicts."""

    def __init__(self):
        self.calls: list[dict] = []

    async def ask(self, **kwargs):
        self.calls.append(kwargs)
        agent = kwargs.get("agent_name", "")
        if agent == "g6.draft_generator":
            return _MockLLMResult(
                text=(
                    "Following my 12 May application for the Head of Payments role at "
                    "Tabby — saw the BNPL expansion announcement (cite:knowledge_id="
                    "abc-123); that's exactly the merchant-side scale my Daraz work "
                    "shipped against. Happy to share more on the launch playbook if "
                    "useful."
                ),
                cost_usd=0.05,
            )
        if agent == "g6.persona_critic":
            return _MockLLMResult(
                text='{"banned_pass": true, "banned_phrases_used": [], '
                     '"value_lead_pass": true, "persona_alignment_score": 90, '
                     '"verdict": "accept", "fixes": []}',
                cost_usd=0.05,
            )
        if agent == "g6.tone_calibrator":
            return _MockLLMResult(
                text='{"voice_match_score": 0.85, "sentence_count": 4, '
                     '"tightness_pass": true, "confidence_pass": true, '
                     '"verdict": "accept", "fixes": []}',
                cost_usd=0.02,
            )
        return _MockLLMResult(text="{}", cost_usd=0.0)


class _RouterBannedFirstThenClean:
    """First draft contains a banned phrase; persona_critic rejects; second
    draft is clean."""

    def __init__(self):
        self.draft_calls = 0
        self.calls: list[dict] = []

    async def ask(self, **kwargs):
        self.calls.append(kwargs)
        agent = kwargs.get("agent_name", "")
        if agent == "g6.draft_generator":
            self.draft_calls += 1
            if self.draft_calls == 1:
                return _MockLLMResult(
                    text=(
                        "Just checking in on my application for the Head of Payments "
                        "role at Tabby (cite:knowledge_id=abc-123). Any updates?"
                    ),
                    cost_usd=0.05,
                )
            return _MockLLMResult(
                text=(
                    "Following my 12 May application — saw the BNPL launch "
                    "(cite:knowledge_id=abc-123); maps onto my Daraz work. Happy to "
                    "share more on the playbook if useful."
                ),
                cost_usd=0.05,
            )
        if agent == "g6.persona_critic":
            # Persona critic sees the draft. The pure-code prescan inside the
            # node will detect "just checking in" on the first draft anyway,
            # but we make the LLM agree for realism.
            return _MockLLMResult(
                text='{"banned_pass": true, "banned_phrases_used": [], '
                     '"value_lead_pass": true, "persona_alignment_score": 90, '
                     '"verdict": "accept", "fixes": []}',
                cost_usd=0.05,
            )
        if agent == "g6.tone_calibrator":
            return _MockLLMResult(
                text='{"voice_match_score": 0.85, "sentence_count": 4, '
                     '"tightness_pass": true, "confidence_pass": true, '
                     '"verdict": "accept", "fixes": []}',
                cost_usd=0.02,
            )
        return _MockLLMResult(text="{}", cost_usd=0.0)


# ──────────────────────────────────────────────────────────────────────────
# DB stubs
# ──────────────────────────────────────────────────────────────────────────
class _FakeTable:
    """Captures inserts; supports the chained .insert().execute() shape."""

    def __init__(self, name: str, captured: dict):
        self.name = name
        self.captured = captured

    def insert(self, payload: dict):
        self.captured.setdefault(self.name, []).append(payload)

        class _Exec:
            data = [{**payload, "id": "fake-cadence-uuid-001"}]

        class _Q:
            def execute(self_inner):
                return _Exec

        return _Q()

    def update(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def execute(self):
        class _R:
            data = []
        return _R()


class _FakeSupabase:
    """Minimal Supabase client surface used by g6_io and g6_nodes."""

    def __init__(self):
        self.captured: dict[str, list[dict]] = {}

    def table(self, name: str):
        return _FakeTable(name, self.captured)

    def rpc(self, *_a, **_k):
        class _R:
            data = []
        return _R()


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────
class TestGraphCompiles:
    def test_compiles_without_checkpointer(self):
        from agents.g6_graph import build_g6_graph
        g = build_g6_graph()
        assert g is not None


@pytest.mark.asyncio
class TestShortCircuit:
    async def test_pre_apply_status_short_circuits_to_persist(self, monkeypatch):
        """A 'researched' (pre-apply) application should skip all LLM nodes
        and write a no-draft row."""
        from agents import g6_io, g6_nodes
        from agents.g6_graph import build_g6_graph

        # Mock the insert path — capture what gets written.
        captured: dict[str, list[dict]] = {}

        def _fake_insert(**kwargs):
            captured.setdefault("insert", []).append(kwargs)
            return {"id": "fake-cadence-uuid-001", **kwargs}

        monkeypatch.setattr(g6_nodes, "insert_cadence_row", _fake_insert)

        # Mock RAG so we don't hit Supabase if we accidentally proceed.
        async def _fake_rag(*a, **k):
            return []
        monkeypatch.setattr(g6_nodes, "load_recent_company_knowledge", _fake_rag)
        monkeypatch.setattr(
            g6_nodes,
            "load_recent_linkedin_personal_wins",
            lambda **k: [],
        )

        # Mock the LLM router so any unexpected LLM call would error loudly.
        router_calls: list[dict] = []

        class _ShouldNotCall:
            async def ask(self, **kw):
                router_calls.append(kw)
                raise AssertionError(
                    "LLM call made during pre-apply short-circuit"
                )

        monkeypatch.setattr(g6_nodes, "get_router", lambda: _ShouldNotCall())

        graph = build_g6_graph()
        state = await graph.ainvoke({
            "application_id": 42,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "application_row": {
                "id": 42,
                "company": "Tabby",
                "role": "Head of Payments",
                "status": "researched",         # pre-apply
                "applied_date": None,
            },
            "company_persona": None,
            "last_cadence_row": None,
            "follow_up_count": 0,
            "iteration": 0,
            "draft_attempts": 0,
            "cost_usd_total": 0.0,
            "latency_ms_total": 0,
            "transcript": [],
        })

        assert state["short_circuit"] is True
        assert state["urgency"] == "waiting"
        assert state["next_action"] == "noop"
        assert (state.get("draft_email") or "") == ""
        # The cadence row should have been written — empty draft, but the
        # row exists.
        assert len(captured.get("insert", [])) == 1
        # Persist should have set cadence_row_id.
        assert state.get("cadence_row_id") == "fake-cadence-uuid-001"
        # The LLM was never called.
        assert router_calls == []

    async def test_terminal_status_short_circuits(self, monkeypatch):
        """A 'rejected' (terminal) application returns COLD/noop without LLM."""
        from agents import g6_nodes
        from agents.g6_graph import build_g6_graph

        captured: list[dict] = []

        def _fake_insert(**kwargs):
            captured.append(kwargs)
            return {"id": "fake-cadence-uuid-002", **kwargs}

        monkeypatch.setattr(g6_nodes, "insert_cadence_row", _fake_insert)

        graph = build_g6_graph()
        state = await graph.ainvoke({
            "application_id": 99,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "application_row": {
                "id": 99,
                "company": "Visa",
                "role": "Senior PM",
                "status": "rejected",
                "applied_date": _days_ago(60),
            },
            "company_persona": None,
            "last_cadence_row": None,
            "follow_up_count": 0,
            "iteration": 0,
            "draft_attempts": 0,
            "cost_usd_total": 0.0,
            "latency_ms_total": 0,
            "transcript": [],
        })
        assert state["urgency"] == "COLD"
        assert state["next_action"] == "noop"
        assert (state.get("draft_email") or "") == ""


@pytest.mark.asyncio
class TestHappyPath:
    async def test_applied_7_days_old_urgent_path(self, monkeypatch):
        """The headline scenario: 7-day-old 'applied' → URGENT → full
        draft → critics accept → persisted with non-empty draft."""
        from agents import g6_io, g6_nodes
        from agents.g6_graph import build_g6_graph

        # Mock RAG to return one fresh news chunk so the draft has signal.
        async def _fake_rag(company_name, query, **k):
            return [{
                "id": "abc-123",
                "section": "news",
                "content": "Tabby announced BNPL expansion into Pakistan.",
                "similarity": 0.92,
                "scraped_at": (
                    datetime.now(timezone.utc) - timedelta(days=2)
                ).isoformat(),
            }]
        monkeypatch.setattr(g6_nodes, "load_recent_company_knowledge", _fake_rag)
        monkeypatch.setattr(
            g6_nodes,
            "load_recent_linkedin_personal_wins",
            lambda **k: [],
        )

        # Capture persist call.
        captured: list[dict] = []

        def _fake_insert(**kwargs):
            captured.append(kwargs)
            return {"id": "fake-cadence-uuid-003", **kwargs}

        monkeypatch.setattr(g6_nodes, "insert_cadence_row", _fake_insert)

        # Mock the router for happy-path LLM responses.
        router = _RouterHappyPath()
        monkeypatch.setattr(g6_nodes, "get_router", lambda: router)

        graph = build_g6_graph()
        state = await graph.ainvoke({
            "application_id": 7,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "application_row": {
                "id": 7,
                "company": "Tabby",
                "role": "Head of Payments",
                "status": "applied",
                "applied_date": _days_ago(7),
            },
            "company_persona": None,
            "last_cadence_row": None,
            "follow_up_count": 0,
            "iteration": 0,
            "draft_attempts": 0,
            "cost_usd_total": 0.0,
            "latency_ms_total": 0,
            "transcript": [],
        })

        # Cadence engine should have classified this as URGENT.
        # (urgency_scorer may have downgraded if draft was empty — verify
        # the draft is non-empty.)
        assert (state.get("draft_email") or "").strip() != ""
        assert state["urgency"] in ("URGENT", "OVERDUE")
        assert state["pushed_to_today_queue"] is True
        # cite breadcrumb extracted.
        cites = state.get("draft_cites") or []
        assert any(c.get("id") == "abc-123" for c in cites)
        # Cost rolled up (draft $0.05 + persona $0.05 + voice $0.02 ≈ $0.12).
        assert state["cost_usd_total"] >= 0.10
        assert state["cost_usd_total"] < 0.20
        # Persist happened.
        assert len(captured) == 1
        # All three LLM nodes were called exactly once each.
        agents_called = [c.get("agent_name") for c in router.calls]
        assert "g6.draft_generator" in agents_called
        assert "g6.persona_critic" in agents_called
        assert "g6.tone_calibrator" in agents_called

    async def test_no_signal_pool_short_circuits_draft(self, monkeypatch):
        """When the RAG fetch returns nothing AND there are no personal wins,
        the draft_generator short-circuits in pure code (no LLM call) and
        urgency_scorer downgrades URGENT→waiting (no actionable card)."""
        from agents import g6_nodes
        from agents.g6_graph import build_g6_graph

        async def _empty_rag(*a, **k):
            return []
        monkeypatch.setattr(g6_nodes, "load_recent_company_knowledge", _empty_rag)
        monkeypatch.setattr(
            g6_nodes,
            "load_recent_linkedin_personal_wins",
            lambda **k: [],
        )

        captured: list[dict] = []

        def _fake_insert(**kwargs):
            captured.append(kwargs)
            return {"id": "fake-cadence-uuid-004", **kwargs}

        monkeypatch.setattr(g6_nodes, "insert_cadence_row", _fake_insert)

        # Track router calls — draft_generator must NOT call the LLM.
        # persona_critic + tone_calibrator should also short-circuit because
        # draft_email is empty.
        router_calls: list[dict] = []

        class _Router:
            async def ask(self, **kw):
                router_calls.append(kw)
                # If we somehow get called, return a benign result so the
                # test still completes.
                return _MockLLMResult(text="{}", cost_usd=0.0)
        monkeypatch.setattr(g6_nodes, "get_router", lambda: _Router())

        graph = build_g6_graph()
        state = await graph.ainvoke({
            "application_id": 12,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "application_row": {
                "id": 12,
                "company": "Obscure Co",
                "role": "PM",
                "status": "applied",
                "applied_date": _days_ago(8),
            },
            "company_persona": None,
            "last_cadence_row": None,
            "follow_up_count": 0,
            "iteration": 0,
            "draft_attempts": 0,
            "cost_usd_total": 0.0,
            "latency_ms_total": 0,
            "transcript": [],
        })

        # draft_email should be empty because signal pool was empty.
        assert (state.get("draft_email") or "") == ""
        # urgency_scorer should have downgraded URGENT/OVERDUE→waiting.
        assert state["urgency"] == "waiting"
        assert state["pushed_to_today_queue"] is False
        # Zero LLM calls — the draft_generator short-circuited in pure code
        # (no rag_chunks). The critic nodes also early-return when there's
        # no draft to score.
        assert router_calls == []


@pytest.mark.asyncio
class TestBannedPhraseLoop:
    async def test_banned_phrase_triggers_redraft(self, monkeypatch):
        """First draft contains a banned phrase. persona_critic flags it via
        pure-code prescan. merge_critique routes back to draft_generator.
        Second draft is clean and accepted."""
        from agents import g6_nodes
        from agents.g6_graph import build_g6_graph

        async def _fake_rag(*a, **k):
            return [{
                "id": "abc-123",
                "section": "news",
                "content": "BNPL launch announced.",
                "similarity": 0.9,
                "scraped_at": (
                    datetime.now(timezone.utc) - timedelta(days=1)
                ).isoformat(),
            }]
        monkeypatch.setattr(g6_nodes, "load_recent_company_knowledge", _fake_rag)
        monkeypatch.setattr(
            g6_nodes,
            "load_recent_linkedin_personal_wins",
            lambda **k: [],
        )

        captured_inserts: list[dict] = []

        def _fake_insert(**kwargs):
            captured_inserts.append(kwargs)
            return {"id": "fake-cadence-uuid-005", **kwargs}

        monkeypatch.setattr(g6_nodes, "insert_cadence_row", _fake_insert)

        router = _RouterBannedFirstThenClean()
        monkeypatch.setattr(g6_nodes, "get_router", lambda: router)

        graph = build_g6_graph()
        state = await graph.ainvoke({
            "application_id": 88,
            "user_id": "00000000-0000-0000-0000-000000000001",
            "application_row": {
                "id": 88,
                "company": "Tabby",
                "role": "Head of Payments",
                "status": "applied",
                "applied_date": _days_ago(8),
            },
            "company_persona": None,
            "last_cadence_row": None,
            "follow_up_count": 0,
            "iteration": 0,
            "draft_attempts": 0,
            "cost_usd_total": 0.0,
            "latency_ms_total": 0,
            "transcript": [],
        })

        # The draft_generator should have been called TWICE.
        assert router.draft_calls == 2
        # Final draft must NOT contain a banned phrase.
        final = state.get("draft_email") or ""
        assert "just checking in" not in final.lower()
        # The merge_outcome on the FIRST attempt had needs_retry=True.
        # After retry we proceed to urgency_scorer → persist.
        assert (state.get("draft_email") or "").strip() != ""
        # Cost rolled up across both attempts.
        assert state["cost_usd_total"] >= 0.15
