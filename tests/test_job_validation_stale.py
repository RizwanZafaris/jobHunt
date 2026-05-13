"""
Regression tests for _detect_stale_age_marker (BUG-048).

Covers the 4 broadened branches added 2026-05-13:
  1. "over a month ago"
  2. "over N months/years ago"
  3. "about/approximately/~ N months ago"
  4. "N weeks ago" (>= 4 weeks)

Plus baseline regression checks for the original patterns + the
80-char disambiguator window (which prevents false positives from JD
body content that legitimately references past events).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")

from agents.job_validation import _detect_stale_age_marker


# ── Baseline (original) patterns ────────────────────────────────────────


def test_baseline_2_months_with_disambig_is_stale():
    body = "Senior PM at Acme — Posted 2 months ago · Be among the first 25 applicants"
    assert _detect_stale_age_marker(body) is not None


def test_baseline_1_year_with_disambig_is_stale():
    body = "Senior PM at Acme — 1 year ago · 12 applicants · Apply now"
    assert _detect_stale_age_marker(body) is not None


def test_baseline_just_now_is_fresh():
    body = "Senior PM at Acme — Posted just now · Apply now"
    assert _detect_stale_age_marker(body) is None


def test_baseline_yesterday_is_fresh():
    body = "Senior PM at Acme — Posted yesterday · View job"
    assert _detect_stale_age_marker(body) is None


# ── BUG-048 new branches ────────────────────────────────────────────────


def test_over_a_month_ago_is_stale():
    body = "Senior PM at Acme — Posted over a month ago · Apply now"
    assert _detect_stale_age_marker(body) is not None


def test_over_3_months_ago_is_stale():
    body = "Senior PM at Acme — Posted over 3 months ago · Be among the first 50 applicants"
    assert _detect_stale_age_marker(body) is not None


def test_about_2_months_ago_is_stale():
    body = "Senior PM at Acme — Posted about 2 months ago · View job"
    assert _detect_stale_age_marker(body) is not None


def test_approximately_6_months_ago_is_stale():
    body = "Senior PM at Acme — Posted approximately 6 months ago · Apply now"
    assert _detect_stale_age_marker(body) is not None


def test_tilde_3_months_ago_is_stale():
    body = "Senior PM at Acme — Posted ~3 months ago · Apply now"
    assert _detect_stale_age_marker(body) is not None


def test_4_weeks_ago_is_stale():
    """4 weeks ≈ 1 month — same financial-leak archetype."""
    body = "Senior PM at Acme — Posted 4 weeks ago · Apply now"
    assert _detect_stale_age_marker(body) is not None


def test_8_weeks_ago_is_stale():
    body = "Senior PM at Acme — Posted 8 weeks ago · Be among the first 12 applicants"
    assert _detect_stale_age_marker(body) is not None


def test_1_week_ago_is_fresh():
    """1 week is fresh — must NOT flag."""
    body = "Senior PM at Acme — Posted 1 week ago · Apply now"
    assert _detect_stale_age_marker(body) is None


def test_3_weeks_ago_is_fresh():
    """3 weeks is still under the 4-week threshold."""
    body = "Senior PM at Acme — Posted 3 weeks ago · Apply now"
    assert _detect_stale_age_marker(body) is None


# ── Disambiguator window control ────────────────────────────────────────


def test_false_positive_jd_body_3_years_ago():
    """Body text mentioning past events without LinkedIn-listing context
    should NOT be flagged. This is the control case for BUG-009 +
    BUG-048: ANY year/month/week phrase in the first 500 chars without
    one of (be among the first | applicant | apply now | posted | view
    job) within the 80-char window is treated as JD content.
    """
    body = (
        "About Acme: We shipped our flagship payments product 3 years ago. "
        "Today we are hiring a Senior PM to lead the next chapter. The role "
        "involves API design, partner integrations, and roadmap ownership."
    )
    # No "apply now" / "applicant" / "view job" / "posted" within 40 chars
    # of the "3 years ago" phrase → must NOT flag.
    assert _detect_stale_age_marker(body) is None


def test_false_positive_company_founded_5_years_ago():
    body = (
        "About Acme: Founded 5 years ago by ex-Stripe engineers. We process "
        "$10B in payments annually and are growing fast."
    )
    assert _detect_stale_age_marker(body) is None


# ── Edge cases ──────────────────────────────────────────────────────────


def test_empty_body_returns_none():
    assert _detect_stale_age_marker("") is None
    assert _detect_stale_age_marker(None) is None  # type: ignore[arg-type]


def test_only_first_500_chars_scanned():
    """Marker far past 500 chars should be ignored — LinkedIn always puts
    the timestamp in the header within the first 500 chars."""
    body = "A" * 600 + " Posted 3 months ago · Apply now"
    assert _detect_stale_age_marker(body) is None
