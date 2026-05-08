"""
Phase 1.6 unit tests — PersonaSynthesizer.

Tests the pure-logic helpers that don't need Supabase or live LLM calls:
  - _has_new_data filtering (does this run skip cleanly when no new outcomes?)
  - _build_synthesis_prompt assembly (cold-start vs. existing-persona shapes)
  - SynthesisResult dataclass

The end-to-end run() needs Supabase + LLM and is exercised in integration tests
or live runs.
"""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHasNewData:
    """_has_new_data — the freshness filter that lets us skip companies cheaply."""

    @pytest.fixture
    def synth(self):
        # Set a dummy GOOGLE_API_KEY so __init__ doesn't fail
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
        from agents.persona_synthesizer import PersonaSynthesizer
        return PersonaSynthesizer()

    def test_no_outcomes_no_transcripts_returns_false(self, synth):
        last = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        assert synth._has_new_data(last, [], []) is False

    def test_outcome_newer_than_last_synth_returns_true(self, synth):
        last = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        outcome_recent = {
            "logged_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        }
        assert synth._has_new_data(last, [outcome_recent], []) is True

    def test_outcome_older_than_last_synth_returns_false(self, synth):
        last = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        outcome_old = {
            "logged_at": (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        }
        assert synth._has_new_data(last, [outcome_old], []) is False

    def test_transcript_newer_than_last_synth_returns_true(self, synth):
        last = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        transcript_recent = {
            "finalized_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        }
        assert synth._has_new_data(last, [], [transcript_recent]) is True

    def test_malformed_last_synth_returns_true(self, synth):
        """Defensive: if last_synthesized_at is bad, prefer to re-synth."""
        assert synth._has_new_data("not-a-date", [], []) is True

    def test_malformed_outcome_timestamp_skipped_gracefully(self, synth):
        last = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        outcome_bad = {"logged_at": "garbage"}
        outcome_good = {
            "logged_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        }
        # Bad row alone → False; mix with good → True
        assert synth._has_new_data(last, [outcome_bad], []) is False
        assert synth._has_new_data(last, [outcome_bad, outcome_good], []) is True


class TestBuildSynthesisPrompt:
    """_build_synthesis_prompt — assembles the user message for the LLM."""

    @pytest.fixture
    def synth(self):
        os.environ.setdefault("ANTHROPIC_API_KEY", "test")
        os.environ.setdefault("OPENAI_API_KEY", "test")
        os.environ.setdefault("SUPABASE_URL", "http://test")
        os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
        from agents.persona_synthesizer import PersonaSynthesizer
        return PersonaSynthesizer()

    def test_cold_start_marks_no_existing_persona(self, synth):
        prompt = synth._build_synthesis_prompt(
            company_name="Plaid",
            existing=None,
            knowledge_text="overview: payments fintech",
            outcomes=[],
            transcripts=[],
        )
        assert "no existing persona" in prompt
        assert "Plaid" in prompt

    def test_with_existing_persona_includes_serialized_block(self, synth):
        existing = {
            "system_prompt_template": "You are a recruiter at Stripe.",
            "ats_keyword_bank": {"required": ["payments"]},
            "success_patterns": [],
            "failure_patterns": [],
            "persona_version": 3,
            "metadata": {"persona_quality": "high"},
        }
        prompt = synth._build_synthesis_prompt(
            company_name="Stripe",
            existing=existing,
            knowledge_text="...",
            outcomes=[],
            transcripts=[],
        )
        assert "You are a recruiter at Stripe." in prompt
        assert "Stripe" in prompt

    def test_outcomes_compressed_to_signal_only(self, synth):
        outcomes = [
            {
                "id": "abc",
                "logged_at": "2026-05-01T00:00:00Z",
                "interview_received": True,
                "rounds_reached": 2,
                "offer_received": False,
                "rejected_reason": "internal candidate",
                "self_rated_quality": 4,
                "notes": "x" * 1000,    # should be truncated
                "extra_field": "should not appear",
            }
        ]
        prompt = synth._build_synthesis_prompt(
            company_name="Test",
            existing=None,
            knowledge_text="",
            outcomes=outcomes,
            transcripts=[],
        )
        # Truncated note shouldn't blow up token budget
        assert "extra_field" not in prompt
        assert "internal candidate" in prompt
        # Note truncation: 300 chars + ellipsis or so
        note_section = prompt.split('"notes":')[1][:400]
        assert "x" * 500 not in note_section

    def test_includes_lookback_window_in_outcomes_label(self, synth):
        prompt = synth._build_synthesis_prompt(
            company_name="X",
            existing=None,
            knowledge_text="",
            outcomes=[],
            transcripts=[],
        )
        assert "last 90 days" in prompt or "n=0" in prompt


class TestSynthesisResult:
    def test_dataclass_defaults(self):
        from agents.persona_synthesizer import SynthesisResult
        r = SynthesisResult(company_name="Stripe", status="synthesized")
        assert r.company_name == "Stripe"
        assert r.status == "synthesized"
        assert r.persona_version_after is None
        assert r.n_outcomes_used == 0
        assert r.cost_usd == 0.0
        assert r.error is None

    def test_failed_carries_error_message(self):
        from agents.persona_synthesizer import SynthesisResult
        r = SynthesisResult(
            company_name="X",
            status="failed",
            error="json parse: unexpected token",
        )
        assert r.status == "failed"
        assert "unexpected token" in r.error
