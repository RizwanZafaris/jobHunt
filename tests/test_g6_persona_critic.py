"""
Tests for the G6 persona-critic's banned-phrase blocker.

The career-ops follow-up framework HARD-FAILS any draft that contains
phrases like "just checking in" / "circling back" / "touching base". The
blocker has two layers:

  1. Pure-code _scan_banned_phrases() — runs before the LLM call, lossless.
  2. The persona_critic LLM — re-scans and can spot soft variants.

Layer 1 (the structural guarantee) is tested here without any LLM call.
Layer 2 (the LLM critique) is verified in test_g6_graph.py via mocked
router behaviour.

Also covers:
  - The pure-code prescan OVERRIDES the LLM's verdict — if pure-code
    found a banned phrase, the critic cannot mark the draft as
    banned_pass=true.
  - Citation extraction from "cite:knowledge_id=<uuid>" breadcrumbs.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

# Required env so config.settings doesn't blow up at import time.
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


from agents.g6_nodes import (    # noqa: E402
    _BANNED_FOLLOW_UP_PHRASES,
    _extract_cites_from_draft,
    _scan_banned_phrases,
)


# ══════════════════════════════════════════════════════════════════════════
# _scan_banned_phrases — the structural enforcement layer
# ══════════════════════════════════════════════════════════════════════════
class TestScanBannedPhrases:
    def test_clean_draft_returns_empty(self):
        draft = (
            "Following my 12 May application for the Head of Payments role at Tabby — "
            "the announcement of your BNPL expansion into Pakistan caught my eye "
            "(cite:knowledge_id=abc-123); that's exactly the merchant-side scale "
            "my Daraz work shipped against. Happy to share more on the launch playbook "
            "if useful."
        )
        assert _scan_banned_phrases(draft) == []

    def test_catches_just_checking_in(self):
        draft = "Hi! Just checking in to see if there are any updates on the role."
        hits = _scan_banned_phrases(draft)
        assert "just checking in" in hits

    def test_catches_circling_back(self):
        draft = "Wanted to circle back on my application from last week."
        # Note: "circle back" is not in the banned set, but "circling back" is.
        # This draft says "circle back" (verb form, no -ing). So it should NOT hit.
        assert "circling back" not in _scan_banned_phrases(draft)

        draft2 = "Circling back on the role we discussed."
        assert "circling back" in _scan_banned_phrases(draft2)

    def test_catches_touching_base(self):
        draft = "I'm touching base regarding the Senior PM position."
        hits = _scan_banned_phrases(draft)
        assert "touching base" in hits

    def test_catches_case_insensitive(self):
        draft = "JUST CHECKING IN — any update on my application?"
        hits = _scan_banned_phrases(draft)
        assert "just checking in" in hits

    def test_catches_inside_sentence(self):
        draft = (
            "Thank you so much for your time the other day. I just wanted to "
            "follow up briefly on a quick question I had."
        )
        hits = _scan_banned_phrases(draft)
        # "just wanted to" AND "wanted to follow up" both match
        assert "just wanted to" in hits
        assert "wanted to follow up" in hits

    def test_catches_any_updates(self):
        draft = "Reaching out for any updates on my application status."
        hits = _scan_banned_phrases(draft)
        assert "any updates" in hits

    def test_empty_string_returns_empty(self):
        assert _scan_banned_phrases("") == []

    def test_none_returns_empty(self):
        assert _scan_banned_phrases(None) == []   # type: ignore[arg-type]

    def test_all_banned_phrases_in_constant_match_themselves(self):
        """Sanity: every phrase in _BANNED_FOLLOW_UP_PHRASES is actually
        detectable when present verbatim."""
        for phrase in _BANNED_FOLLOW_UP_PHRASES:
            assert phrase in _scan_banned_phrases(f"Hi! {phrase} regarding this.")


# ══════════════════════════════════════════════════════════════════════════
# Citation extraction
# ══════════════════════════════════════════════════════════════════════════
class TestExtractCitesFromDraft:
    def test_extracts_knowledge_cite(self):
        draft = (
            "Saw the BNPL launch announcement (cite:knowledge_id=abc-123) which "
            "maps onto my Daraz work."
        )
        cites = _extract_cites_from_draft(draft)
        assert cites == [{"kind": "knowledge", "id": "abc-123"}]

    def test_extracts_linkedin_cite(self):
        draft = "Just published a piece on tokenisation (cite:linkedin_id=xyz-789)."
        cites = _extract_cites_from_draft(draft)
        assert cites == [{"kind": "linkedin", "id": "xyz-789"}]

    def test_extracts_multiple_cites(self):
        draft = (
            "Two relevant signals: the launch (cite:knowledge_id=abc-123) "
            "and my recent post (cite:linkedin_id=xyz-789)."
        )
        cites = _extract_cites_from_draft(draft)
        assert {"kind": "knowledge", "id": "abc-123"} in cites
        assert {"kind": "linkedin", "id": "xyz-789"} in cites
        assert len(cites) == 2

    def test_no_cites_returns_empty(self):
        assert _extract_cites_from_draft("Plain text with no cites.") == []

    def test_empty_string_returns_empty(self):
        assert _extract_cites_from_draft("") == []


# ══════════════════════════════════════════════════════════════════════════
# Persona critic node — banned-phrase prescan overrides LLM verdict
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
class TestPersonaCriticBannedPhraseOverride:
    async def test_prescan_overrides_lenient_llm(self, monkeypatch):
        """Even if the LLM hallucinates banned_pass=true, the pure-code
        prescan forces banned_pass=false when a banned phrase was found."""
        from agents.g6_nodes import persona_critic_node

        # Mock the router so the LLM "returns" a lenient verdict.
        async def _fake_ask(**kwargs):
            class _R:
                text = '{"banned_pass": true, "banned_phrases_used": [], ' \
                       '"value_lead_pass": true, "persona_alignment_score": 90, ' \
                       '"verdict": "accept", "fixes": []}'
                provider = "anthropic"
                model = "claude-sonnet-4-6"
                cost_usd = 0.01
                latency_ms = 100
                input_tokens = 100
                output_tokens = 50
                cache_creation_input_tokens = None
                cache_read_input_tokens = None
            return _R()

        from agents import g6_nodes
        mock_router = MagicMock()
        mock_router.ask = _fake_ask
        monkeypatch.setattr(g6_nodes, "get_router", lambda: mock_router)

        state = {
            "application_row": {"company": "Tabby", "role": "Head of Product"},
            "company_persona": {},
            "draft_email": "Just checking in on my application — any updates?",
            "iteration": 0,
        }
        result = await persona_critic_node(state)
        critique = result["persona_critique"]
        # Pure-code prescan must win: the LLM said banned_pass=true; our
        # override flips it to false because "just checking in" is in the
        # draft.
        assert critique["banned_pass"] is False
        assert "just checking in" in critique["banned_phrases_used"]
        assert critique["verdict"] == "revise"

    async def test_clean_draft_passes_through(self, monkeypatch):
        """When prescan finds nothing, the LLM verdict is preserved."""
        from agents.g6_nodes import persona_critic_node

        async def _fake_ask(**kwargs):
            class _R:
                text = '{"banned_pass": true, "banned_phrases_used": [], ' \
                       '"value_lead_pass": true, "persona_alignment_score": 92, ' \
                       '"verdict": "accept", "fixes": []}'
                provider = "anthropic"
                model = "claude-sonnet-4-6"
                cost_usd = 0.01
                latency_ms = 100
                input_tokens = 100
                output_tokens = 50
                cache_creation_input_tokens = None
                cache_read_input_tokens = None
            return _R()

        from agents import g6_nodes
        mock_router = MagicMock()
        mock_router.ask = _fake_ask
        monkeypatch.setattr(g6_nodes, "get_router", lambda: mock_router)

        state = {
            "application_row": {"company": "Tabby", "role": "Head of Product"},
            "company_persona": {},
            "draft_email": (
                "Following my 12 May application for the role at Tabby — saw the "
                "BNPL expansion announcement (cite:knowledge_id=abc-123). Happy "
                "to share more on the launch playbook if useful."
            ),
            "iteration": 0,
        }
        result = await persona_critic_node(state)
        critique = result["persona_critique"]
        assert critique["banned_pass"] is True
        assert critique["verdict"] == "accept"
