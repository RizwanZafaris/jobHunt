"""
Unit tests for the G6 cadence rules engine (agents.g6_cadence).

The cadence engine is pure code — no LLM, no DB. Tests exercise every
branch of compute_cadence (terminal, pre-apply, in-window, past-window,
overdue, max-follow-ups-reached, ghosted-past-auto-close) with explicit
(status, applied_date, last_contact_date, follow_up_count, days_since)
fixtures.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest


# Project root on path so `agents.*` resolves.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# We don't need supabase stubbed for cadence tests — the module is pure
# code with no DB import path. But pytest collection of OTHER tests in
# the suite may have stubbed it; importing agents.g6_cadence here is
# safe either way.
from agents.g6_cadence import (    # noqa: E402
    CADENCE_RULES,
    CadenceDecision,
    compute_cadence,
    compute_next_follow_up_date,
)


# ── Fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture
def now() -> datetime:
    """Fixed 'now' so date arithmetic is deterministic."""
    return datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc)


def _days_ago(now: datetime, n: int) -> datetime:
    return now - timedelta(days=n)


# ══════════════════════════════════════════════════════════════════════════
# Terminal statuses
# ══════════════════════════════════════════════════════════════════════════
class TestTerminalStatuses:
    def test_rejected_is_cold_noop(self, now):
        d = compute_cadence(
            status="rejected",
            applied_date=_days_ago(now, 5),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "COLD"
        assert d.next_action == "noop"
        assert "terminal_status" in d.reason

    def test_withdrawn_is_cold_noop(self, now):
        d = compute_cadence(
            status="withdrawn",
            applied_date=_days_ago(now, 90),
            follow_up_count=2,
            now=now,
        )
        assert d.urgency == "COLD"
        assert d.next_action == "noop"


# ══════════════════════════════════════════════════════════════════════════
# Pre-apply statuses
# ══════════════════════════════════════════════════════════════════════════
class TestPreApplyStatuses:
    @pytest.mark.parametrize("status", ["new", "researched", "resume_ready"])
    def test_pre_apply_is_waiting_noop(self, status, now):
        d = compute_cadence(
            status=status,
            applied_date=None,
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "waiting"
        assert d.next_action == "noop"
        assert f"pre_apply_status:{status}" in d.reason


# ══════════════════════════════════════════════════════════════════════════
# Applied — 7-day cadence, 2 follow-ups, 30-day auto-close
# ══════════════════════════════════════════════════════════════════════════
class TestAppliedCadence:
    def test_applied_day_1_is_waiting(self, now):
        """1 day after apply — within cadence window."""
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 1),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "waiting"
        assert d.next_action == "wait"
        assert d.recommended_delay_days == 6   # 7 - 1
        assert d.days_since_last_event == 1

    def test_applied_day_6_is_waiting(self, now):
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 6),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "waiting"
        assert d.next_action == "wait"
        assert d.recommended_delay_days == 1

    def test_applied_day_7_is_urgent(self, now):
        """The headline case: 7-day-old `applied` row → URGENT, draft today."""
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 7),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "URGENT"
        assert d.next_action == "follow_up"
        assert d.recommended_delay_days == 0    # draft today
        assert d.days_since_last_event == 7

    def test_applied_day_10_is_urgent(self, now):
        """Still URGENT — not yet OVERDUE (threshold = 2x cadence = 14d)."""
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 10),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "URGENT"
        assert d.next_action == "follow_up"

    def test_applied_day_14_is_overdue(self, now):
        """At 2x cadence (14d for applied) we tip into OVERDUE."""
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 14),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "OVERDUE"
        assert d.next_action == "follow_up"

    def test_applied_day_31_ghosted_cold_close(self, now):
        """31 days past apply with no contact → ghosted; auto-close window
        (30 days) has elapsed."""
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 31),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "COLD"
        assert d.next_action == "close"
        assert "ghosted_past_auto_close" in d.reason

    def test_applied_max_follow_ups_reached_is_cold(self, now):
        """Even within the auto-close window, hitting max follow-ups → COLD."""
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 28),         # within 30-day window
            last_contact_date=_days_ago(now, 1),      # contact yesterday
            follow_up_count=2,                        # max=2 for 'applied'
            now=now,
        )
        assert d.urgency == "COLD"
        assert d.next_action == "close"
        assert "max_follow_ups_reached" in d.reason


# ══════════════════════════════════════════════════════════════════════════
# Interviewing — 3-day cadence, 3 follow-ups, 14-day auto-close
# ══════════════════════════════════════════════════════════════════════════
class TestInterviewingCadence:
    def test_interviewing_day_3_is_urgent(self, now):
        d = compute_cadence(
            status="interviewing",
            applied_date=_days_ago(now, 10),
            last_contact_date=_days_ago(now, 3),
            follow_up_count=1,
            now=now,
        )
        assert d.urgency == "URGENT"
        assert d.next_action == "follow_up"
        assert d.days_since_last_event == 3

    def test_interviewing_anchors_on_last_contact_not_applied(self, now):
        """When last_contact_date > applied_date, cadence anchors on contact."""
        d = compute_cadence(
            status="interviewing",
            applied_date=_days_ago(now, 30),    # ancient
            last_contact_date=_days_ago(now, 2),
            follow_up_count=1,
            now=now,
        )
        # Days_since should be 2 (last_contact), not 30 (applied).
        assert d.days_since_last_event == 2
        assert d.urgency == "waiting"

    def test_interviewing_day_14_is_cold(self, now):
        """14 days since last contact in interview state → auto-close window."""
        d = compute_cadence(
            status="interviewing",
            applied_date=_days_ago(now, 20),
            last_contact_date=_days_ago(now, 14),
            follow_up_count=1,
            now=now,
        )
        assert d.urgency == "COLD"
        assert d.next_action == "close"


# ══════════════════════════════════════════════════════════════════════════
# Offered — 2-day cadence, 2 follow-ups, manual close
# ══════════════════════════════════════════════════════════════════════════
class TestOfferedCadence:
    def test_offered_day_2_is_urgent(self, now):
        d = compute_cadence(
            status="offered",
            applied_date=_days_ago(now, 14),
            last_contact_date=_days_ago(now, 2),
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "URGENT"
        assert d.next_action == "follow_up"

    def test_offered_no_auto_close(self, now):
        """Offered has auto_close_after_days=None — even 60 days out,
        it should NOT be COLD by ghosting. It can still go COLD by
        max_follow_ups, though."""
        d = compute_cadence(
            status="offered",
            applied_date=_days_ago(now, 60),
            last_contact_date=_days_ago(now, 30),
            follow_up_count=0,           # below max=2
            now=now,
        )
        assert d.urgency in ("URGENT", "OVERDUE")
        assert d.next_action == "follow_up"


# ══════════════════════════════════════════════════════════════════════════
# Data-integrity edge: post-apply status with NULL applied_date
# (014 CHECK constraint should prevent this in production but the engine
# must not crash on a degenerate row.)
# ══════════════════════════════════════════════════════════════════════════
class TestDegenerateInputs:
    def test_applied_without_applied_date_returns_waiting(self, now):
        d = compute_cadence(
            status="applied",
            applied_date=None,
            follow_up_count=0,
            now=now,
        )
        assert d.urgency == "waiting"
        assert d.next_action == "noop"
        assert "missing_applied_date" in d.reason


# ══════════════════════════════════════════════════════════════════════════
# compute_next_follow_up_date
# ══════════════════════════════════════════════════════════════════════════
class TestComputeNextFollowUpDate:
    def test_applied_returns_7_days_ahead(self, now):
        result = compute_next_follow_up_date(status="applied", now=now)
        assert result is not None
        assert (result - now).days == 7

    def test_interviewing_returns_3_days_ahead(self, now):
        result = compute_next_follow_up_date(status="interviewing", now=now)
        assert result is not None
        assert (result - now).days == 3

    def test_terminal_status_returns_none(self, now):
        assert compute_next_follow_up_date(status="rejected", now=now) is None
        assert compute_next_follow_up_date(status="withdrawn", now=now) is None

    def test_unknown_status_returns_none(self, now):
        assert compute_next_follow_up_date(status="unknown_xyz", now=now) is None


# ══════════════════════════════════════════════════════════════════════════
# Sanity: the rules table matches the roadmap spec
# (caught me when I miscopied the values from the table.)
# ══════════════════════════════════════════════════════════════════════════
class TestRulesTableMatchesSpec:
    def test_applied_rule(self):
        r = CADENCE_RULES["applied"]
        assert r.follow_up_after_days == 7
        assert r.max_follow_ups == 2
        assert r.auto_close_after_days == 30

    def test_interviewing_rule(self):
        r = CADENCE_RULES["interviewing"]
        assert r.follow_up_after_days == 3
        assert r.max_follow_ups == 3
        assert r.auto_close_after_days == 14

    def test_offered_rule(self):
        r = CADENCE_RULES["offered"]
        assert r.follow_up_after_days == 2
        assert r.max_follow_ups == 2
        assert r.auto_close_after_days is None

    def test_terminal_rules(self):
        assert CADENCE_RULES["rejected"].follow_up_after_days is None
        assert CADENCE_RULES["withdrawn"].follow_up_after_days is None


# ══════════════════════════════════════════════════════════════════════════
# CadenceDecision shape
# ══════════════════════════════════════════════════════════════════════════
class TestCadenceDecisionShape:
    def test_as_dict_has_audit_keys(self, now):
        d = compute_cadence(
            status="applied",
            applied_date=_days_ago(now, 7),
            follow_up_count=0,
            now=now,
        )
        out = d.as_dict()
        assert set(out.keys()) == {
            "urgency", "next_action", "recommended_delay_days",
            "days_since_last_event", "reason",
        }
