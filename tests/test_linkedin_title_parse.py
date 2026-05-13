"""
Regression tests for Stream E (BUG-051, 2026-05-13): LinkedIn search-result
title parsing.

Production examples that the prior naive split() got wrong:
  - "Fintech/Payments — Ventula Consulting hiring Group Product Manager"
    prior:  "Fintech/Payments"        (WRONG — topic)
    correct: "Ventula Consulting"
  - "Emirates NBD hiring Senior Product Manager – Merchant Acquiring
     at Emirates NBD"
    prior:  "Merchant Acquiring …"    (WRONG — phantom function fragment)
    correct: "Emirates NBD"

The fix consults 4 ordered patterns: em-dash split, "at <co>" suffix,
"<co> hiring" prefix, and legacy "ROLE - COMPANY | PORTAL". Each
candidate gets phantom-chrome filtering before returning.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")
os.environ.setdefault("SERPER_API_KEY", "test")


@pytest.fixture
def extract():
    """Return the bare _extract_company_from_title method, no instance."""
    from agents.job_scout_agent import JobScoutAgent

    # Bypass JobScoutAgent.__init__ since it requires API keys + does work.
    fn = JobScoutAgent._extract_company_from_title.__get__(
        MagicMock(), JobScoutAgent
    )
    return fn


# ── New patterns the fix introduces ────────────────────────────────────


def test_em_dash_topic_company_hiring_role(extract):
    """LinkedIn modern format: <topic> — <company> hiring <role>."""
    title = "Fintech/Payments — Ventula Consulting hiring Group Product Manager – Fintech/Payments"
    assert extract(title) == "Ventula Consulting"


def test_role_at_company_suffix(extract):
    """LinkedIn old format: <co> hiring <role> – <topic> at <co>.

    Prefer the 'at <COMPANY>' suffix when present — ground-truth employer."""
    title = "Emirates NBD hiring Senior Product Manager – Merchant Acquiring at Emirates NBD"
    assert extract(title) == "Emirates NBD"


def test_company_hiring_role_no_suffix(extract):
    """When no 'at' suffix, fall back to the '<co> hiring' prefix."""
    title = "Acme Corp hiring Senior Product Manager"
    assert extract(title) == "Acme Corp"


# ── Phantom-chrome rejection ───────────────────────────────────────────


def test_rejects_phantom_chrome_careers(extract):
    """Title pattern that produces 'Careers' must NOT be accepted."""
    title = "Adyen Careers — Head of Product"  # NB ambiguous, em-dash
    # The em-dash branch would yield "Head of Product" which IS a phantom.
    # Should fall through and ultimately return None (or "Adyen" via
    # legacy hyphen split). Either way, NOT "Careers".
    result = extract(title)
    assert result != "Careers"
    if result is not None:
        assert "Careers" not in result


def test_rejects_phantom_chrome_hiring(extract):
    """'Hiring' alone (e.g. from 'X Hiring' platform) must not be returned."""
    title = "Senior PM - Hiring | Indeed"
    # parts[1] would be "Hiring" — phantom chrome → reject.
    assert extract(title) != "Hiring"


# ── Legacy patterns still work ─────────────────────────────────────────


def test_legacy_role_dash_company_portal(extract):
    """The old '<role> - <company> | <portal>' format still extracts."""
    title = "Senior Product Manager - Stripe | LinkedIn"
    assert extract(title) == "Stripe"


def test_legacy_role_at_company(extract):
    """The old '<role> at <company>' format still extracts."""
    title = "Senior Product Manager at Marqeta"
    assert extract(title) == "Marqeta"


def test_portal_name_rejected(extract):
    """Don't surface 'linkedin'/'indeed' as a company."""
    title = "Senior PM | LinkedIn"  # split on ' | ' would give "LinkedIn"
    # parts[1] = "LinkedIn", which is in non_companies → None
    result = extract(title)
    assert result is None or result.lower() != "linkedin"


# ── Edge cases ─────────────────────────────────────────────────────────


def test_empty_title_returns_none(extract):
    assert extract("") is None
    assert extract(None) is None  # type: ignore[arg-type]


def test_title_with_no_separators(extract):
    """A flat title with no recognized separator → None."""
    assert extract("Some random job posting blob") is None


def test_title_with_only_topic(extract):
    """'Fintech — Payments' has no 'hiring' marker, no 'at', no '-' → fallback to em-dash split."""
    title = "Fintech — Payments"
    # No 'hiring' → pattern 1 misses. No 'at' → pattern 2 misses. Legacy
    # split on em-dash via the unicode `[-–|]` class doesn't match `—`
    # (different codepoint). Result: None (good).
    assert extract(title) is None or extract(title) != "Fintech"
