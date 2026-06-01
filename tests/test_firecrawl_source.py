"""
Tests for agents/sources/firecrawl_source.py.

Covers:
  1. Missing FIRECRAWL_API_KEY → FirecrawlConfigError
  2. Budget cap exceeded → FirecrawlBudgetError
  3. Successful scrape returns ScrapedJobListing items with provenance
  4. Firecrawl HTTP 5xx → empty list (graceful degrade) + telemetry row
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


# ── Helpers ────────────────────────────────────────────────────────────


def _settings_with(api_key: str | None, *, budget: float = 20.0, per_call: float = 0.005):
    """Return a MagicMock settings object that the source will read."""
    s = MagicMock()
    s.firecrawl_api_key = api_key
    s.firecrawl_base_url = "https://api.firecrawl.dev/v1"
    s.firecrawl_monthly_budget_usd = budget
    s.firecrawl_cost_per_call_usd = per_call
    return s


def _mock_httpx_response(*, status: int = 200, json_body: dict | None = None):
    """Build a MagicMock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=(json_body or {}))
    resp.text = "" if json_body else "internal_error"
    return resp


# ── 1. Missing API key ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_api_key_raises_config_error(monkeypatch):
    from agents.sources.firecrawl_source import (
        FirecrawlConfigError,
        scrape_career_page,
    )

    monkeypatch.setattr(
        "config.settings.get_settings",
        lambda: _settings_with(None),
        raising=False,
    )

    with pytest.raises(FirecrawlConfigError):
        await scrape_career_page("Visa", "https://example.com")


# ── 2. Budget cap ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_cap_blocks_scrape(monkeypatch):
    from agents.sources import firecrawl_source as mod
    from agents.sources.firecrawl_source import (
        FirecrawlBudgetError,
        scrape_career_page,
    )

    monkeypatch.setattr(
        "config.settings.get_settings",
        lambda: _settings_with("sk_test_123", budget=1.0, per_call=0.005),
        raising=False,
    )
    # Pretend we've already spent $1.00 this month.
    monkeypatch.setattr(mod, "_month_spend_usd", lambda: 1.00)

    with pytest.raises(FirecrawlBudgetError) as exc_info:
        await scrape_career_page("Visa", "https://example.com")
    assert "budget cap hit" in str(exc_info.value).lower()


# ── 3. Successful scrape parses listings ───────────────────────────────


@pytest.mark.asyncio
async def test_successful_scrape_returns_listings(monkeypatch):
    from agents.sources import firecrawl_source as mod
    from agents.sources.firecrawl_source import scrape_career_page

    monkeypatch.setattr(
        "config.settings.get_settings",
        lambda: _settings_with("sk_test_123"),
        raising=False,
    )
    monkeypatch.setattr(mod, "_month_spend_usd", lambda: 0.0)
    monkeypatch.setattr(mod, "_log_telemetry", lambda **kw: None)

    fake_body = {
        "success": True,
        "data": {
            "extract": {
                "jobs": [
                    {
                        "title": "Senior Product Manager, Payments",
                        "location": "San Francisco, CA",
                        "department": "Product / Payments",
                        "apply_url": "https://example.com/jobs/42",
                        "jd_excerpt": "Lead the payments product roadmap...",
                        "salary_range": "$210K-$245K",
                        "seniority": "Senior",
                    },
                    {
                        "title": "Staff Engineer, Risk Platform",
                        "apply_url": "https://example.com/jobs/43",
                    },
                ]
            }
        },
    }

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *_args, **_kw):
            return _mock_httpx_response(json_body=fake_body)

    monkeypatch.setattr(
        "agents.sources.firecrawl_source.httpx.AsyncClient", lambda **_kw: _MockClient()
    )

    listings = await scrape_career_page("Visa", "https://example.com/careers")

    assert len(listings) == 2
    first = listings[0]
    assert first.title == "Senior Product Manager, Payments"
    assert first.apply_url == "https://example.com/jobs/42"
    assert first.salary_range == "$210K-$245K"
    assert first.source_company == "Visa"
    assert first.source_url == "https://example.com/careers"
    assert first.scraped_at  # ISO timestamp set
    assert first.parse_warnings == []

    # Second listing missing title/dept → still returned, no warning
    # because apply_url IS present.
    second = listings[1]
    assert second.title == "Staff Engineer, Risk Platform"
    assert second.parse_warnings == []


# ── 4. HTTP failure → graceful empty list ──────────────────────────────


@pytest.mark.asyncio
async def test_http_5xx_returns_empty_list(monkeypatch):
    from agents.sources import firecrawl_source as mod
    from agents.sources.firecrawl_source import scrape_career_page

    monkeypatch.setattr(
        "config.settings.get_settings",
        lambda: _settings_with("sk_test_123"),
        raising=False,
    )
    monkeypatch.setattr(mod, "_month_spend_usd", lambda: 0.0)

    telemetry_calls: list[dict] = []
    monkeypatch.setattr(mod, "_log_telemetry", lambda **kw: telemetry_calls.append(kw))

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *_args, **_kw):
            return _mock_httpx_response(status=503, json_body=None)

    monkeypatch.setattr(
        "agents.sources.firecrawl_source.httpx.AsyncClient", lambda **_kw: _MockClient()
    )

    listings = await scrape_career_page("Visa", "https://example.com/careers")

    assert listings == []
    # Telemetry row recorded the failure
    assert len(telemetry_calls) == 1
    assert telemetry_calls[0]["status"] == "http_503"
    # Did NOT charge a credit (cost_usd=0 on failure)
    assert telemetry_calls[0]["cost_usd"] == 0.0
