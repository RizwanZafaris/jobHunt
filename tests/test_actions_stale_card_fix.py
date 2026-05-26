"""
tests/test_actions_stale_card_fix.py — unit tests for the Adyen stale-card fix.

Covers the three fixes:
  Fix 1 — age penalty in _rank()
  Fix 2 — dismiss/snooze filter
  Fix 3 — surface-count lifecycle penalty + dormant hide

These are pure-function tests against api.actions internals — no DB,
no FastAPI. Fast (<1 s total) so they can run on every commit.
"""
from __future__ import annotations

import os
import sys

# Project root on sys.path — matches the rest of the test suite.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# Import the module under test. Some symbols are private-by-convention
# but stable; we import them directly because that's the contract this
# test enforces.
from api import actions as A


# ─── helpers ─────────────────────────────────────────────────────────────
def _meta(*, score=90, created_days_ago=None, surface_count=None) -> dict:
    m: dict = {"score": score}
    if created_days_ago is not None:
        m["createdAt"] = (
            datetime.now(timezone.utc) - timedelta(days=created_days_ago)
        ).isoformat()
    if surface_count is not None:
        m["surfaceCount"] = surface_count
    return m


def _job_action(action_id="job-1-high", kind="score_high_no_resume", **meta_kw) -> dict:
    return {
        "id": action_id,
        "kind": kind,
        "title": "Test",
        "state": "ready",
        "primary": {"label": "Go"},
        "meta": _meta(**meta_kw),
    }


# ─── Fix 1 — age penalty ─────────────────────────────────────────────────
class TestAgePenalty:
    def test_fresh_card_no_penalty(self):
        m = _meta(score=90, created_days_ago=0, surface_count=0)
        assert A._effective_score(m) == 90

    def test_one_day_old_drops_by_one(self):
        m = _meta(score=90, created_days_ago=1, surface_count=0)
        # CARD_AGE_PENALTY_PER_DAY = 1
        assert A._effective_score(m) == 89

    def test_fourteen_days_old(self):
        m = _meta(score=95, created_days_ago=14, surface_count=0)
        # 14 * 1 = 14
        assert A._effective_score(m) == 81

    def test_age_penalty_capped(self):
        m = _meta(score=95, created_days_ago=365, surface_count=0)
        # Capped at CARD_AGE_PENALTY_CAP_DAYS = 30
        assert A._effective_score(m) == 95 - 30

    def test_missing_created_at_no_crash(self):
        m = {"score": 90}  # no createdAt
        assert A._effective_score(m) == 90

    def test_malformed_created_at_no_crash(self):
        m = {"score": 90, "createdAt": "not-a-date"}
        # Should silently skip the penalty rather than crash /today.
        assert A._effective_score(m) == 90


# ─── Fix 3 — surface penalty + dormant ───────────────────────────────────
class TestSurfacePenalty:
    def test_first_surface_free(self):
        m = _meta(score=90, surface_count=1)
        # extra = max(1-1, 0) = 0
        assert A._effective_score(m) == 90

    def test_second_surface_costs_2(self):
        m = _meta(score=90, surface_count=2)
        # extra = 1, penalty = 1 * 2 = 2
        assert A._effective_score(m) == 88

    def test_ten_surfaces(self):
        m = _meta(score=90, surface_count=10)
        # extra = 9, penalty = 9 * 2 = 18
        assert A._effective_score(m) == 72

    def test_surface_penalty_capped(self):
        m = _meta(score=90, surface_count=1000)
        # extra = max(999, 0), capped at CARD_SURFACE_PENALTY_CAP = 30
        assert A._effective_score(m) == 90 - (30 * 2)


# ─── Fix 1 + 3 combined — the Adyen scenario ─────────────────────────────
class TestAdyenScenario:
    def test_old_adyen_sinks_below_fresh_lower_score(self):
        """The original bug: a 95-score Adyen job from 14 days ago,
        surfaced 7 times, should rank BELOW a fresh 88-score job."""
        adyen = _job_action(
            "job-adyen-high",
            score=95,
            created_days_ago=14,
            surface_count=7,
        )
        fresh = _job_action(
            "job-fresh-high",
            score=88,
            created_days_ago=0,
            surface_count=0,
        )
        ranked = A._rank([adyen, fresh])
        assert ranked[0]["id"] == "job-fresh-high"
        assert ranked[1]["id"] == "job-adyen-high"

    def test_two_fresh_cards_higher_score_wins(self):
        a = _job_action("a", score=95, created_days_ago=0, surface_count=0)
        b = _job_action("b", score=88, created_days_ago=0, surface_count=0)
        ranked = A._rank([a, b])
        assert ranked[0]["id"] == "a"

    def test_kind_priority_dominates(self):
        """Follow-up overdue should beat a fresh high-score job
        regardless of age (kind priority is the outer sort key)."""
        followup = {
            "id": "f1",
            "kind": "follow_up_overdue",
            "title": "Follow up",
            "state": "ready",
            "primary": {"label": "x"},
            "meta": {"score": 0},
        }
        job = _job_action(score=99, created_days_ago=0, surface_count=0)
        ranked = A._rank([job, followup])
        assert ranked[0]["id"] == "f1"


# ─── Fix 2 — dismiss filter ──────────────────────────────────────────────
class TestDismissFilter:
    def test_active_dismissed_ids_returns_permanent(self):
        """snoozed_until=None → permanent → always in active set."""
        with patch.object(A, "get_supabase") as gs:
            gs.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
                {"job_id": 1, "snoozed_until": None},
                {"job_id": 2, "snoozed_until": None},
            ]
            result = A._active_dismissed_job_ids("test-user")
            assert result == {1, 2}

    def test_active_dismissed_ids_filters_expired_snooze(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        with patch.object(A, "get_supabase") as gs:
            gs.return_value.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
                {"job_id": 10, "snoozed_until": past},    # expired, NOT in active
                {"job_id": 20, "snoozed_until": future},  # still active
                {"job_id": 30, "snoozed_until": None},    # permanent
            ]
            result = A._active_dismissed_job_ids("test-user")
            assert result == {20, 30}

    def test_active_dismissed_ids_db_error_returns_empty(self):
        """Defensive: a DB error must NOT black out /today."""
        with patch.object(A, "get_supabase") as gs:
            gs.return_value.table.return_value.select.side_effect = Exception("boom")
            result = A._active_dismissed_job_ids("test-user")
            assert result == set()


# ─── Action meta wiring ──────────────────────────────────────────────────
class TestActionMeta:
    def test_action_helper_includes_new_meta(self):
        a = A._action(
            id="x",
            kind="score_high_no_resume",
            title="t",
            state="ready",
            primary_label="Go",
            job_id=42,
            created_at="2026-05-12T00:00:00Z",
            surface_count=5,
        )
        assert a["meta"]["jobId"] == 42
        assert a["meta"]["createdAt"] == "2026-05-12T00:00:00Z"
        assert a["meta"]["surfaceCount"] == 5

    def test_action_helper_omits_when_none(self):
        a = A._action(
            id="x",
            kind="persona_stale",
            title="t",
            state="stale",
            primary_label="x",
        )
        meta = a.get("meta") or {}
        assert "jobId" not in meta
        assert "createdAt" not in meta
        assert "surfaceCount" not in meta


# ─── Dismiss reason validation ───────────────────────────────────────────
class TestDismissReasonEnum:
    def test_valid_reasons_match_migration_constraint(self):
        # Must match db/migrations/2026_05_26_036_job_card_dismissals.sql
        # chk_reason constraint exactly. Drift here = 500 in production.
        expected = {
            "not_interested", "maybe_later", "wrong_seniority",
            "wrong_location", "wrong_comp", "closed_already", "other",
        }
        assert A._VALID_REASONS == expected


# ─── Tunables sanity ─────────────────────────────────────────────────────
class TestTunables:
    def test_dormant_threshold_positive(self):
        assert A.CARD_DORMANT_THRESHOLD > 0

    def test_surface_penalty_per_view_positive(self):
        assert A.CARD_SURFACE_PENALTY_PER_VIEW > 0

    def test_age_penalty_per_day_positive(self):
        assert A.CARD_AGE_PENALTY_PER_DAY > 0
