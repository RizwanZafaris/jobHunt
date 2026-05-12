"""Tests for agents/comp_research.py — Phase 1.1 compensation intelligence.

We test the parser + strategy + cache-key logic in isolation. The Perplexity
HTTP call and the Supabase calls are mocked since this is a unit-test layer.
The full E2E path is exercised against the live cache table when the
integration suite runs against the staging Supabase project.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from agents.comp_research import (
    CompBand,
    SalaryStrategy,
    _build_user_query,
    _cache_key_tuple,
    _classify_confidence,
    _parse_comp_response,
    _row_to_band,
    suggest_salary_strategy,
)


RIZWAN_UUID = UUID("00000000-0000-0000-0000-000000000001")


# ─── _parse_comp_response ─────────────────────────────────────────────────


def test_parse_clean_json():
    raw = json.dumps({
        "currency": "USD",
        "p25": 150000, "p50": 180000, "p75": 215000, "p90": 260000,
        "summary": "Glassdoor + Levels.fyi 2025 data.",
    })
    parsed = _parse_comp_response(raw)
    assert parsed["currency"] == "USD"
    assert parsed["p25"] == 150000
    assert parsed["p90"] == 260000
    assert "Glassdoor" in parsed["summary"]


def test_parse_handles_prose_wrapping():
    raw = (
        "Here is the comp data you asked for:\n\n"
        '{"currency": "EUR", "p50": 90000, "p90": 130000, "p25": null, "p75": null, '
        '"summary": "thin data"}\n\n'
        "Hope that helps."
    )
    parsed = _parse_comp_response(raw)
    assert parsed["currency"] == "EUR"
    assert parsed["p50"] == 90000
    assert parsed["p90"] == 130000
    assert parsed["p25"] is None
    assert parsed["p75"] is None


def test_parse_coerces_string_numbers():
    """Sonar occasionally returns numbers as strings or with $ / comma / k."""
    raw = json.dumps({
        "currency": "USD",
        "p25": "$150,000",
        "p50": "180000",
        "p75": "215K",
        "p90": "260k",
        "summary": "ok",
    })
    parsed = _parse_comp_response(raw)
    assert parsed["p25"] == 150000
    assert parsed["p50"] == 180000
    assert parsed["p75"] == 215000
    assert parsed["p90"] == 260000


def test_parse_returns_none_on_garbage():
    assert _parse_comp_response("") is None
    assert _parse_comp_response("totally unparseable response") is None
    assert _parse_comp_response("{not valid json") is None


def test_parse_returns_none_on_non_object():
    assert _parse_comp_response("[1, 2, 3]") is None


# ─── _classify_confidence ─────────────────────────────────────────────────


def test_confidence_high_when_all_percentiles_present():
    assert _classify_confidence({"p25": 1, "p50": 2, "p75": 3, "p90": 4}) == "high"


def test_confidence_medium_when_some_percentiles_present():
    assert _classify_confidence({"p50": 1, "p75": 2, "p25": None, "p90": None}) == "medium"


def test_confidence_low_when_zero_or_one_percentile():
    assert _classify_confidence({"p50": 1, "p25": None, "p75": None, "p90": None}) == "low"
    assert _classify_confidence({}) == "low"


# ─── _cache_key_tuple ─────────────────────────────────────────────────────


def test_cache_key_normalises_case_and_whitespace():
    a = _cache_key_tuple(company=" Stripe ", role="Senior PM", level="L5", location="Dubai")
    b = _cache_key_tuple(company="stripe", role="senior pm", level="l5", location="DUBAI")
    assert a == b == ("stripe", "senior pm", "l5", "dubai")


def test_cache_key_handles_none_fields():
    assert _cache_key_tuple(company="x", role="y", level=None, location=None) == (
        "x", "y", "", "",
    )


# ─── _build_user_query ────────────────────────────────────────────────────


def test_user_query_includes_optional_fields_only_when_set():
    q = _build_user_query(company="Stripe", role="PM", level=None, location=None)
    assert "Company: Stripe" in q
    assert "Role: PM" in q
    assert "Level:" not in q
    assert "Location:" not in q

    q2 = _build_user_query(company="Stripe", role="PM", level="Senior", location="Dubai")
    assert "Level: Senior" in q2
    assert "Location: Dubai" in q2


# ─── _row_to_band ─────────────────────────────────────────────────────────


def test_row_to_band_decodes_citations_string():
    """citations sometimes come back from Supabase as a JSON string."""
    row = {
        "id": "abc-123",
        "company": "Stripe",
        "role": "PM",
        "level": "Senior",
        "location": "Dubai",
        "currency": "USD",
        "p25": 150000, "p50": 180000, "p75": 215000, "p90": 260000,
        "source_summary": "ok",
        "source_citations": '[{"url": "https://example.com"}]',
        "confidence": "high",
        "created_at": "2026-05-10T00:00:00+00:00",
    }
    band = _row_to_band(row, source_company="Stripe", source_role="PM")
    assert band.cached is True
    assert band.cache_id == "abc-123"
    assert band.confidence == "high"
    assert band.p50 == 180000
    assert isinstance(band.source_citations, list)
    assert band.source_citations[0]["url"] == "https://example.com"
    assert band.age_days >= 0  # depends on current clock; just sanity-bound


def test_row_to_band_tolerates_missing_citations():
    row = {
        "id": "x", "company": "x", "role": "y", "currency": "USD",
        "p50": 100000, "source_summary": "", "source_citations": None,
    }
    band = _row_to_band(row, source_company="x", source_role="y")
    assert band.source_citations == []


# ─── CompBand methods ─────────────────────────────────────────────────────


def test_band_has_data_true_with_any_percentile():
    b = CompBand(company="x", role="y", level=None, location=None, currency="USD",
                 p25=None, p50=None, p75=None, p90=100000, source_summary="")
    assert b.has_data()


def test_band_has_data_false_when_all_null():
    b = CompBand(company="x", role="y", level=None, location=None, currency="USD",
                 p25=None, p50=None, p75=None, p90=None, source_summary="")
    assert not b.has_data()


def test_band_midpoint_prefers_p50():
    b = CompBand(company="x", role="y", level=None, location=None, currency="USD",
                 p25=100, p50=200, p75=300, p90=400, source_summary="")
    assert b.midpoint() == 200


def test_band_midpoint_falls_back_to_p25_p75_average():
    b = CompBand(company="x", role="y", level=None, location=None, currency="USD",
                 p25=100, p50=None, p75=300, p90=None, source_summary="")
    assert b.midpoint() == 200


def test_band_width_pct_wide_market():
    b = CompBand(company="x", role="y", level=None, location=None, currency="USD",
                 p25=100, p50=100, p75=130, p90=200, source_summary="")
    # (200-100)/100 = 1.0 (100% spread)
    assert b.band_width_pct() == 1.0


def test_band_width_pct_none_when_missing_data():
    b = CompBand(company="x", role="y", level=None, location=None, currency="USD",
                 p25=None, p50=None, p75=None, p90=None, source_summary="")
    assert b.band_width_pct() is None


# ─── suggest_salary_strategy ──────────────────────────────────────────────


def _band(**kwargs) -> CompBand:
    """Build a CompBand with required defaults."""
    base = dict(
        company="Stripe", role="Senior PM", level="Senior", location="Dubai",
        currency="USD", p25=None, p50=None, p75=None, p90=None, source_summary="",
    )
    base.update(kwargs)
    return CompBand(**base)


def test_strategy_decline_on_no_data():
    band = _band()  # all None
    strat = suggest_salary_strategy(band)
    assert strat.approach == "decline"
    assert strat.low is None
    assert strat.anchor is None
    assert "flexible" in strat.framing.lower()


def test_strategy_range_for_wide_band():
    # p50=180k, p90=260k → width = 80/180 = 44% > 30% → range
    band = _band(p25=150000, p50=180000, p75=215000, p90=260000)
    strat = suggest_salary_strategy(band)
    assert strat.approach == "range"
    assert strat.low is not None and strat.high is not None
    assert strat.low < strat.high
    assert strat.anchor is None
    assert "range" in strat.framing.lower()
    # The framing should not include the user-target note since no target was given.
    assert "exceeds the market p90" not in strat.framing
    assert "exceeds the market p90" not in strat.rationale


def test_strategy_anchor_for_narrow_band():
    # p50=180k, p90=200k → width = 20/180 = 11% < 30% → anchor
    band = _band(p25=170000, p50=180000, p75=190000, p90=200000)
    strat = suggest_salary_strategy(band)
    assert strat.approach == "anchor"
    assert strat.anchor == 190000  # p75
    assert strat.low is None and strat.high is None
    assert "190" in strat.framing  # rendered as 190,000


def test_strategy_user_target_capped_at_p90():
    """User target 400k vs market p90 280k → strategy caps at p90 (and notes it)."""
    band = _band(p25=150000, p50=180000, p75=220000, p90=280000)
    strat = suggest_salary_strategy(band, user_target=400_000)
    # Cap means the anchor (or high in a range) shouldn't be 400k.
    if strat.approach == "anchor":
        assert strat.anchor <= 280000
    if strat.approach == "range":
        assert strat.high <= 280000
    # And the rationale must mention the cap.
    assert "exceeds" in strat.rationale or "auto-filter" in strat.rationale


def test_strategy_uses_currency_in_framing():
    band = _band(p25=80000, p50=90000, p75=100000, p90=110000, currency="EUR")
    strat = suggest_salary_strategy(band)
    assert "EUR" in strat.framing


# ─── End-to-end with mocked Perplexity + Supabase (cache miss path) ────────


@pytest.mark.asyncio
async def test_get_comp_band_cache_miss_calls_sonar_and_persists():
    from agents import comp_research

    fake_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "currency": "USD",
                        "p25": 150000, "p50": 180000, "p75": 215000, "p90": 260000,
                        "summary": "Glassdoor 2025-2026 sample.",
                    })
                }
            }
        ],
        "usage": {"prompt_tokens": 200, "completion_tokens": 100},
        "citations": ["https://glassdoor.com/...", "https://levels.fyi/..."],
    }

    fake_db = MagicMock()
    # Cache miss: select returns []
    fake_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.eq.return_value.eq.return_value.gte.return_value.order \
        .return_value.limit.return_value.execute.return_value.data = []
    # Persist: upsert returns one row with id
    fake_db.table.return_value.upsert.return_value.execute.return_value.data = [
        {"id": "new-cache-id"}
    ]

    with patch("agents.comp_research.get_supabase", return_value=fake_db), \
         patch("agents.comp_research._post_chat", new=AsyncMock(return_value=fake_payload)):
        band = await comp_research.get_comp_band(
            company="Stripe", role="Senior PM", level="Senior", location="Dubai",
            user_id=RIZWAN_UUID,
        )

    assert band.cached is False
    assert band.cost_usd > 0  # we charged for the Sonar call
    assert band.p50 == 180000
    assert band.confidence == "high"
    assert band.cache_id == "new-cache-id"
    assert len(band.source_citations) >= 1


@pytest.mark.asyncio
async def test_get_comp_band_cache_hit_skips_sonar():
    from agents import comp_research

    cached_row = {
        "id": "cache-row-id",
        "company": "Stripe", "role": "Senior PM", "level": "Senior", "location": "Dubai",
        "currency": "USD",
        "p25": 150000, "p50": 180000, "p75": 215000, "p90": 260000,
        "source_summary": "cached", "source_citations": [],
        "confidence": "high", "created_at": "2026-05-10T00:00:00+00:00",
    }

    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.eq.return_value.eq.return_value.gte.return_value.order \
        .return_value.limit.return_value.execute.return_value.data = [cached_row]

    sonar_mock = AsyncMock()  # Should never be called.

    with patch("agents.comp_research.get_supabase", return_value=fake_db), \
         patch("agents.comp_research._post_chat", new=sonar_mock):
        band = await comp_research.get_comp_band(
            company="Stripe", role="Senior PM", level="Senior", location="Dubai",
            user_id=RIZWAN_UUID,
        )

    assert band.cached is True
    assert band.cost_usd == 0.0
    assert band.cache_id == "cache-row-id"
    sonar_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_comp_band_returns_empty_band_on_perplexity_failure():
    """If Perplexity raises, we should still return a usable CompBand (no_data)."""
    from agents import comp_research

    fake_db = MagicMock()
    fake_db.table.return_value.select.return_value.eq.return_value.eq.return_value \
        .eq.return_value.eq.return_value.eq.return_value.gte.return_value.order \
        .return_value.limit.return_value.execute.return_value.data = []

    failing_sonar = AsyncMock(side_effect=RuntimeError("perplexity 500"))

    with patch("agents.comp_research.get_supabase", return_value=fake_db), \
         patch("agents.comp_research._post_chat", new=failing_sonar):
        band = await comp_research.get_comp_band(
            company="Stripe", role="Senior PM", user_id=RIZWAN_UUID,
        )

    assert band.has_data() is False
    assert "perplexity call failed" in band.source_summary
    assert band.confidence == "low"


@pytest.mark.asyncio
async def test_get_comp_band_missing_company_returns_empty():
    from agents import comp_research
    band = await comp_research.get_comp_band(
        company="", role="PM", user_id=RIZWAN_UUID,
    )
    assert band.has_data() is False
    assert "missing" in band.source_summary.lower()
