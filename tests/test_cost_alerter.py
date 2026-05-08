"""
Phase 1.10 unit tests — CostAlerter pure-logic helpers.

The end-to-end check_daily_spend / send_weekly_digest paths require
Supabase + Slack/SendGrid and are exercised in integration tests or by
manual triggering via /alerts/check + /alerts/weekly-digest.

Here we test:
  - AlertResult dataclass shape
  - _format_daily_alert message structure
  - _format_weekly_digest empty-state + populated cases
  - _pct helper edge cases
"""
from __future__ import annotations
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _env():
    """Stub env so settings + supabase client construct without real keys."""
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")
    os.environ.setdefault("SUPABASE_URL", "http://test")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


class TestAlertResult:
    def test_dataclass_defaults(self):
        from agents.cost_alerter import AlertResult
        r = AlertResult(kind="daily", fired=False, channel=None, message="x", metrics={})
        assert r.kind == "daily"
        assert r.fired is False
        assert r.channel is None
        assert r.error is None
        assert r.metrics == {}

    def test_fired_with_channel(self):
        from agents.cost_alerter import AlertResult
        r = AlertResult(
            kind="weekly_digest",
            fired=True,
            channel="slack",
            message="full digest",
            metrics={"total": 42},
        )
        assert r.channel == "slack"
        assert r.metrics["total"] == 42


class TestFormatDailyAlert:
    @pytest.fixture
    def alerter(self):
        from agents.cost_alerter import CostAlerter
        return CostAlerter()

    def test_basic_alert_structure(self, alerter):
        msg = alerter._format_daily_alert(
            today_spend=25.50,
            today_calls=43,
            by_provider={
                "anthropic": {"calls": 30, "cost_usd": 18.0},
                "google": {"calls": 13, "cost_usd": 7.50},
            },
            threshold=20.0,
        )
        assert "Daily LLM cost alert" in msg
        assert "$25.50" in msg
        assert "$20.00" in msg
        assert "43 calls" in msg
        assert "$5.50" in msg                     # overage = 25.50 - 20.00
        assert "anthropic" in msg
        assert "google" in msg

    def test_providers_sorted_descending_by_cost(self, alerter):
        msg = alerter._format_daily_alert(
            today_spend=10,
            today_calls=5,
            by_provider={
                "deepseek": {"calls": 2, "cost_usd": 1.0},
                "anthropic": {"calls": 2, "cost_usd": 7.0},
                "google": {"calls": 1, "cost_usd": 2.0},
            },
            threshold=5.0,
        )
        # Anthropic should appear before google, which appears before deepseek
        anth_idx = msg.index("anthropic")
        google_idx = msg.index("google")
        deepseek_idx = msg.index("deepseek")
        assert anth_idx < google_idx < deepseek_idx

    def test_no_providers(self, alerter):
        msg = alerter._format_daily_alert(
            today_spend=20.01,
            today_calls=0,
            by_provider={},
            threshold=20.0,
        )
        # Should still produce a coherent message
        assert "$20.01" in msg
        assert "By provider" in msg


class TestFormatWeeklyDigest:
    @pytest.fixture
    def alerter(self):
        from agents.cost_alerter import CostAlerter
        return CostAlerter()

    def test_empty_metrics_renders_helpfully(self, alerter):
        """Empty digest should still produce a message with helpful zero-state copy."""
        msg = alerter._format_weekly_digest({
            "window_start_iso": "2026-05-02T00:00:00+00:00",
            "total_cost_usd_7d": 0.0,
            "total_calls_7d": 0,
            "total_errors_7d": 0,
            "health_by_provider": [],
            "top_builds": [],
            "cost_capped_count_7d": 0,
            "conversion_funnel": [],
        })
        assert "Weekly cost digest" in msg
        assert "$0.00" in msg
        assert "agent_call_log is empty" in msg
        assert "USE_G2_GRAPH=true" in msg

    def test_populated_digest(self, alerter):
        msg = alerter._format_weekly_digest({
            "window_start_iso": "2026-05-02T00:00:00+00:00",
            "total_cost_usd_7d": 47.32,
            "total_calls_7d": 312,
            "total_errors_7d": 4,
            "health_by_provider": [
                {
                    "provider": "anthropic",
                    "total_cost_usd_7d": 30.0,
                    "calls_7d": 200,
                    "error_rate_pct": 0.5,
                    "p95_latency_ms": 5500,
                },
                {
                    "provider": "google",
                    "total_cost_usd_7d": 12.0,
                    "calls_7d": 80,
                    "error_rate_pct": 6.5,        # > 5% → should warn
                    "p95_latency_ms": 22000,      # > 20s → should slow-marker
                },
            ],
            "top_builds": [
                {
                    "company_name": "Plaid",
                    "polisher_score": 95,
                    "status": "converged",
                    "iterations": 2,
                    "cost_usd_total": 2.4,
                },
                {
                    "company_name": "Mastercard",
                    "polisher_score": None,
                    "status": "cost_capped",
                    "iterations": 3,
                    "cost_usd_total": 5.6,
                },
            ],
            "cost_capped_count_7d": 1,
            "conversion_funnel": [
                {
                    "company_name": "Plaid",
                    "resumes_built": 3,
                    "responses": 1,
                    "interviews": 1,
                    "offers": 0,
                },
            ],
        })
        # Cost totals
        assert "$47.32" in msg
        assert "312 calls" in msg
        # Threshold markers
        assert "⚠" in msg              # warns on google's 6.5%
        assert "🐢" in msg              # slow marker on 22s p95
        # Top builds
        assert "Plaid" in msg
        assert "Mastercard" in msg
        assert "cost_capped" in msg
        assert "$2.40" in msg or "$2.4" in msg
        # Funnel
        assert "3 built" in msg
        assert "1 interview" in msg

    def test_status_score_dash_when_polisher_score_none(self, alerter):
        msg = alerter._format_weekly_digest({
            "window_start_iso": "x",
            "total_cost_usd_7d": 1.0,
            "total_calls_7d": 5,
            "total_errors_7d": 0,
            "health_by_provider": [],
            "top_builds": [
                {
                    "company_name": "Adyen",
                    "polisher_score": None,
                    "status": "failed",
                    "iterations": 0,
                    "cost_usd_total": 0.5,
                },
            ],
            "cost_capped_count_7d": 0,
            "conversion_funnel": [],
        })
        assert "score —" in msg


class TestPctHelper:
    def test_zero_denominator(self):
        from agents.cost_alerter import _pct
        assert _pct(0, 0) == 0.0
        assert _pct(5, 0) == 0.0

    def test_normal(self):
        from agents.cost_alerter import _pct
        assert _pct(1, 100) == 1.0
        assert _pct(33, 100) == 33.0
        assert _pct(2, 7) == 28.6        # rounded to 1dp


class TestSlackDispatchSignature:
    """We don't hit Slack — but verify the call shape is correct."""

    @pytest.mark.asyncio
    async def test_send_slack_returns_none_on_200(self, monkeypatch):
        from agents.cost_alerter import CostAlerter
        alerter = CostAlerter()

        class MockResponse:
            status_code = 200
            text = "ok"

        class MockClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, url, json=None, headers=None):
                # Verify expected payload shape
                assert "text" in json
                return MockResponse()

        monkeypatch.setattr("agents.cost_alerter.httpx.AsyncClient", MockClient)
        result = await alerter._send_slack("https://hooks.slack.com/x", "hello")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_slack_returns_error_on_non_200(self, monkeypatch):
        from agents.cost_alerter import CostAlerter
        alerter = CostAlerter()

        class MockResponse:
            status_code = 403
            text = "channel_not_found"

        class MockClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
            async def post(self, url, json=None, headers=None):
                return MockResponse()

        monkeypatch.setattr("agents.cost_alerter.httpx.AsyncClient", MockClient)
        result = await alerter._send_slack("https://hooks.slack.com/x", "hello")
        assert result is not None
        assert "403" in result
