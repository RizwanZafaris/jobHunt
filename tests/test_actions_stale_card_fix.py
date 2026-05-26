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
class TestAggregatorFilter:
    """2026-05-26: filter out URLs from known aggregator hosts (LinkedIn
    /jobs/view, BuiltInNYC, Bayt, etc.). The user reported these as bogus."""

    def test_builtinnyc_is_aggregator(self):
        url = "https://www.builtinnyc.com/job/head-product-management-issuing/4498776"
        assert A._is_aggregator_url(url), (
            "builtinnyc.com — canonical aggregator that surfaced the bogus Adyen card"
        )

    def test_linkedin_jobs_view_is_aggregator(self):
        urls = [
            "https://ae.linkedin.com/jobs/view/chief-product-officer-fintech-b2b-platforms-at-adecco-4409575832",
            "https://www.linkedin.com/jobs/view/abc-123",
            "https://sg.linkedin.com/jobs/view/senior-pm",
        ]
        for url in urls:
            assert A._is_aggregator_url(url), f"LinkedIn /jobs/view path is aggregator: {url}"

    def test_direct_ats_urls_not_aggregator(self):
        urls = [
            "https://stripe.com/jobs/listing/product-manager-payments/7176530",
            "https://careers.adyen.com/jobs/12345",
            "https://jobs.smartrecruiters.com/Visa/744000116287926-sr-product-manager",
            "https://www.marqeta.com/careers/7743057",
            "https://boards.greenhouse.io/stripe/jobs/12345",
            "https://jobs.lever.co/airwallex/abc",
        ]
        for url in urls:
            assert not A._is_aggregator_url(url), f"Direct ATS URL must pass: {url}"

    def test_none_url_not_aggregator(self):
        assert not A._is_aggregator_url(None)
        assert not A._is_aggregator_url("")

    def test_bayt_indeed_glassdoor_blocked(self):
        for url in [
            "https://www.bayt.com/en/saudi-arabia/jobs/senior-product-manager-arab-73928456/",
            "https://www.indeed.com/viewjob?jk=abc",
            "https://www.glassdoor.com/job-listing/x",
        ]:
            assert A._is_aggregator_url(url), f"Should block aggregator: {url}"


class TestFreshnessFilter:
    """2026-05-26: MAX_AGE_DAYS hides jobs older than the threshold."""

    def test_max_age_days_is_reasonable(self):
        assert 7 <= A.MAX_AGE_DAYS <= 60, (
            f"MAX_AGE_DAYS={A.MAX_AGE_DAYS} should be 7-60 days — long enough "
            "to keep good jobs visible, short enough to hide stale ones."
        )

    def test_aggregator_hosts_includes_known_offenders(self):
        # The user explicitly named these as bogus
        assert any("builtinnyc" in h for h in A.AGGREGATOR_HOSTS)
        assert any("linkedin.com/jobs/view" in h for h in A.AGGREGATOR_HOSTS)
        assert any("bayt" in h for h in A.AGGREGATOR_HOSTS)


class TestTunables:
    def test_dormant_threshold_positive(self):
        assert A.CARD_DORMANT_THRESHOLD > 0

    def test_surface_penalty_per_view_positive(self):
        assert A.CARD_SURFACE_PENALTY_PER_VIEW > 0

    def test_age_penalty_per_day_positive(self):
        assert A.CARD_AGE_PENALTY_PER_DAY > 0


# ─── Confidence filter relaxation (2026-05-26 hotfix) ────────────────────
class TestConfidenceFilterLegacyRows:
    """The query must accept legacy rows (confidence_score IS NULL) alongside
    v2-validated rows (confidence_score >= 50). Previously it required NOT
    NULL which filtered out 100% of pre-2026-05-12 jobs.

    The actual SQL execution is via PostgREST → can't easily mock the whole
    chain in a unit test. Instead we assert the query construction calls
    `.or_(...)` with the expected pattern, which is the contract the
    PostgREST client honors at runtime.
    """

    def test_strict_not_null_filter_removed(self):
        """The original tight filter (NOT NULL AND >= 50) must stay gone."""
        import inspect
        src = inspect.getsource(A._build_job_actions)
        assert '.not_.is_("confidence_score"' not in src, (
            "The strict `.not_.is_('confidence_score', None)` filter "
            "was re-introduced — this regresses the 2026-05-26 hotfix "
            "and will hide all legacy v1 jobs from /today."
        )

    def test_safe_select_uses_only_proven_columns(self):
        """Hotfix #4 (2026-05-26): SELECT must use ONLY columns proven
        to exist on the jobs table (per /api/proxy/jobs probe). Adding
        columns that don't exist causes PostgREST 400, swallowed by the
        try/except, returns empty list, blank /today."""
        import inspect
        src = inspect.getsource(A._build_job_actions)
        assert "_SAFE_SELECT" in src, (
            "_SAFE_SELECT constant missing — the SELECT must use a "
            "verified column whitelist."
        )
        # The forbidden columns (don't exist on jobs table in production)
        # must NOT appear in any SELECT statement inside _build_job_actions.
        # They CAN appear in setdefault() calls (padding for downstream code).
        # Specifically check the _SAFE_SELECT block.
        # Extract just the _SAFE_SELECT assignment.
        import re
        m = re.search(r'_SAFE_SELECT\s*=\s*\(([^)]+)\)', src, re.DOTALL)
        assert m, "_SAFE_SELECT assignment not found"
        select_cols = m.group(1)
        for forbidden in ("confidence_score", "validation_status", "created_at", "legitimacy_score"):
            assert forbidden not in select_cols, (
                f"_SAFE_SELECT must NOT include '{forbidden}' — column does not exist on jobs."
            )

    def test_safe_select_no_or_filter_on_confidence(self):
        """The OR filter on confidence_score was removed because the
        column doesn't exist. Re-introducing it would break the query."""
        import inspect
        import re
        src = inspect.getsource(A._build_job_actions)
        # Strip comments + docstrings to inspect only executable code
        code_only = re.sub(r'#[^\n]*', '', src)
        code_only = re.sub(r'""".*?"""', '', code_only, flags=re.DOTALL)
        assert '.or_(' not in code_only or 'confidence_score' not in code_only.split('.or_(')[1].split(')')[0], (
            "The OR filter on confidence_score must stay removed — "
            "the column doesn't exist on the jobs table."
        )
