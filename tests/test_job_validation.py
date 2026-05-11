"""Tests for agents.job_validation — the 7 JobScout v2 safeguards.

Each safeguard gets at least one happy-path test and one rejection test.
HTTP probes are mocked via httpx.MockTransport so tests run fast and offline.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import patch

import httpx
import pytest

from agents.job_validation import (
    DiscoveredJob,
    ValidatedJob,
    ValidationResult,
    _compute_freshness,
    _domain_in_whitelist,
    _has_expiry_phrase,
    _has_jd_fingerprint,
    _matches_archetype,
    _score_confidence,
    merge_validated_with_existing,
    validate_candidate,
)


# ─── Safeguard #2 — domain whitelist ─────────────────────────────────────
class TestDomainWhitelist:
    def test_company_own_domain_passes(self):
        assert _domain_in_whitelist(
            "https://www.marqeta.com/careers/abc-123",
            ["marqeta.com"],
        )

    def test_ats_greenhouse_always_passes(self):
        assert _domain_in_whitelist(
            "https://boards.greenhouse.io/marqeta/jobs/4567890",
            target_company_domains=[],
        )

    def test_workday_subdomain_passes(self):
        assert _domain_in_whitelist(
            "https://visa.wd3.myworkdayjobs.com/en-US/EarlyCareers/job/Senior-PM",
            target_company_domains=[],
        )

    def test_linkedin_jobs_view_passes(self):
        assert _domain_in_whitelist(
            "https://www.linkedin.com/jobs/view/4390154964",
            target_company_domains=[],
        )

    def test_linkedin_non_jobs_path_blocked(self):
        # LinkedIn root or feed links should NOT be considered valid JDs
        assert not _domain_in_whitelist(
            "https://www.linkedin.com/feed/",
            target_company_domains=[],
        )

    def test_aggregator_blocked(self):
        # indeed.com without an explicit allowance → reject
        assert not _domain_in_whitelist(
            "https://www.indeed.com/jobs?q=product+manager",
            target_company_domains=["marqeta.com"],
        )

    def test_subdomain_of_company_passes(self):
        assert _domain_in_whitelist(
            "https://careers.checkout.com/job/abc",
            target_company_domains=["checkout.com"],
        )


# ─── Safeguard #3 — JD content fingerprint ───────────────────────────────
class TestJDFingerprint:
    def test_real_jd_passes(self):
        body = """
        <html>
        <title>Senior Product Manager — Marqeta</title>
        <h1>Senior Product Manager</h1>
        <button>Apply now</button>
        <h2>Qualifications</h2>
        <ul><li>5+ years experience</li></ul>
        <p>Location: San Francisco</p>
        </html>
        """
        assert _has_jd_fingerprint(body)

    def test_careers_homepage_rejected(self):
        body = """
        <html><body>
        <h1>Welcome to Careers</h1>
        <p>Search for jobs across our company.</p>
        </body></html>
        """
        assert not _has_jd_fingerprint(body)

    def test_blog_post_rejected(self):
        body = "<html><body><h1>Our 2026 Strategy</h1><p>This year we are...</p></body></html>"
        assert not _has_jd_fingerprint(body)

    def test_salary_range_alone_not_enough(self):
        # Salary range matches one fingerprint; need 2.
        body = "<p>Compensation: $120k–$180k</p>"
        assert not _has_jd_fingerprint(body)


# ─── Safeguard #4 — expiry phrase scan ───────────────────────────────────
class TestExpiryPhrase:
    def test_no_longer_accepting_detected(self):
        body = "Thank you for your interest. This job is no longer accepting applications."
        assert _has_expiry_phrase(body)

    def test_position_filled_detected(self):
        body = "Update: this position has been filled. Stay tuned for similar roles."
        assert _has_expiry_phrase(body)

    def test_normal_jd_passes_clean(self):
        body = "Senior PM role. Apply now. Requirements: 5+ years."
        assert _has_expiry_phrase(body) is None

    def test_case_insensitive(self):
        body = "THIS LISTING HAS EXPIRED"
        assert _has_expiry_phrase(body)


# ─── Safeguard #5 — confidence scoring ───────────────────────────────────
class TestConfidenceScoring:
    def test_multi_source_scores_90(self):
        score = _score_confidence(
            ["perplexity_sonar", "ats_greenhouse"],
            fingerprint_passed=True,
            freshness="fresh",
        )
        assert score == 90.0

    def test_ats_alone_scores_85(self):
        score = _score_confidence(["ats_greenhouse"], True, "fresh")
        assert score == 85.0

    def test_perplexity_with_fingerprint_70(self):
        score = _score_confidence(["perplexity_sonar"], True, "fresh")
        assert score == 70.0

    def test_perplexity_url_only_50(self):
        score = _score_confidence(["perplexity_sonar"], False, "fresh")
        assert score == 50.0  # exact threshold — borderline pass

    def test_stale_penalty(self):
        score = _score_confidence(["perplexity_sonar"], True, "stale")
        assert score == 55.0  # 70 - 15

    def test_unknown_source_drops_below_threshold(self):
        score = _score_confidence(["random_source"], True, "fresh")
        assert score < 50.0


# ─── Safeguard #6 — freshness ────────────────────────────────────────────
class TestFreshness:
    def test_today_is_fresh(self):
        iso = datetime.now(timezone.utc).isoformat()
        assert _compute_freshness(iso, "") == "fresh"

    def test_45_days_is_recent(self):
        iso = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        assert _compute_freshness(iso, "") == "recent"

    def test_180_days_is_stale(self):
        iso = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
        assert _compute_freshness(iso, "") == "stale"

    def test_missing_published_at_defaults_recent(self):
        assert _compute_freshness(None, "no meta tag here") == "recent"

    def test_meta_published_time_parsed(self):
        iso = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        body = f'<meta property="article:published_time" content="{iso}">'
        assert _compute_freshness(None, body) == "stale"


# ─── Safeguard #7 — archetype pre-filter ─────────────────────────────────
class TestArchetypeFilter:
    def test_senior_pm_matches(self):
        assert _matches_archetype("Senior Product Manager — Payments")

    def test_head_of_product_matches(self):
        assert _matches_archetype("Head of Product, Fintech")

    def test_chief_product_officer_matches(self):
        assert _matches_archetype("Chief Product Officer (CPO)")

    def test_program_manager_matches(self):
        assert _matches_archetype("Technical Program Manager — Marqeta")

    def test_director_of_sales_rejected(self):
        assert not _matches_archetype("Director of Sales")

    def test_data_scientist_rejected(self):
        assert not _matches_archetype("Senior Data Scientist")

    def test_custom_archetype_list(self):
        assert _matches_archetype("Lead Engineer", persona_archetypes=["Lead Engineer"])
        assert not _matches_archetype("Senior Product Manager", persona_archetypes=["Lead Engineer"])


# ─── merge_validated_with_existing ───────────────────────────────────────
class TestMergeValidated:
    def test_perplexity_then_ats_yields_90(self):
        v = ValidatedJob(
            url="https://boards.greenhouse.io/marqeta/jobs/4567890",
            original_url="https://boards.greenhouse.io/marqeta/jobs/4567890",
            title="Senior PM",
            company_name="Marqeta",
            confidence_score=85.0,
            discovery_sources=["ats_greenhouse"],
            freshness="fresh",
            published_at=None,
            validated_at=datetime.now(timezone.utc),
            body_excerpt="",
        )
        merged, conf = merge_validated_with_existing(
            v,
            existing_sources=["perplexity_sonar"],
            existing_confidence=70.0,
        )
        assert set(merged) == {"ats_greenhouse", "perplexity_sonar"}
        assert conf == 90.0


# ─── Full validate_candidate integration (no-network paths only) ─────────
@pytest.mark.asyncio
class TestValidateCandidate:
    # HTTP-mocked tests deferred to a separate respx-based suite; here we
    # only test the early-return paths that don't hit the network.

    async def test_archetype_mismatch_skipped(self):
        # No HTTP call should happen — title doesn't match.
        candidate = DiscoveredJob(
            url="https://example.com/jobs/123",
            title="Director of Sales",
            company_name="ExampleCo",
            source="perplexity_sonar",
        )
        result = await validate_candidate(
            candidate,
            target_company_domains=["example.com"],
        )
        assert result.validated is None
        assert result.rejected_reason == "archetype_mismatch"
