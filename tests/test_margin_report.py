"""
tests/test_margin_report.py — per-tenant margin report (Phase 3, P3-2).

Covers config.plans.compute_margin (pure arithmetic, no DB) + that the
/admin/margin route is registered and admin-gated. No network/keys.

The route-wiring test imports api.server, which constructs Settings() at import
time — so it sets the minimal env first and nulls the cached settings singleton,
mirroring the fixture in tests/test_admin_scheduler.py (the project has no
conftest that does this globally).
"""
import pytest

from config.plans import compute_margin


# ── compute_margin: pure math (no imports that touch settings) ──────────────
def test_paid_plan_positive_margin():
    m = compute_margin("pro", 5.0)  # pro: $49 revenue, $20 allowance
    assert m["plan"] == "pro"
    assert m["revenue_usd"] == 49.0
    assert m["cost_usd"] == 5.0
    assert m["margin_usd"] == 44.0
    assert m["margin_pct"] == round(100.0 * 44.0 / 49.0, 1)  # ~89.8
    assert m["allowance_used_pct"] == 25.0  # 5/20
    assert m["over_allowance"] is False


def test_paid_plan_can_show_negative_margin():
    # Spend above revenue (only possible pre-cap-enablement or misconfig) →
    # the report must surface it as a negative margin, not hide it.
    m = compute_margin("pro", 60.0)
    assert m["margin_usd"] == round(49.0 - 60.0, 6)
    assert m["margin_usd"] < 0
    assert m["over_allowance"] is True  # 60 >= 20 allowance


def test_at_allowance_is_over():
    assert compute_margin("pro", 20.0)["over_allowance"] is True


def test_unlimited_plan_has_undefined_margin():
    m = compute_margin("lifetime", 123.45)
    assert m["unlimited"] is True
    assert m["revenue_usd"] == 0.0
    assert m["cost_usd"] == 123.45
    assert m["margin_pct"] is None          # revenue 0 → undefined
    assert m["allowance_used_pct"] is None  # unlimited → no cap
    assert m["over_allowance"] is False


def test_free_plan_revenue_zero_margin_undefined():
    m = compute_margin("free", 1.0)
    assert m["revenue_usd"] == 0.0
    assert m["margin_pct"] is None
    # free has a $2 allowance → 1.0 is 50% used, not over
    assert m["allowance_used_pct"] == 50.0
    assert m["over_allowance"] is False


def test_unknown_plan_falls_back_to_free():
    assert compute_margin("bogus", 0.0)["plan"] == "free"


def test_serialisable_shape():
    import json
    json.dumps(compute_margin("scale", 10.0))  # must not raise


# ── route wiring ────────────────────────────────────────────────────────────
@pytest.fixture
def _server(monkeypatch):
    """Set the minimal env api.server needs at import, drop the cached settings
    singleton, then import the app. Mirrors tests/test_admin_scheduler.py."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    import config.settings as _cs
    _cs._settings = None
    import api.server as _srv
    return _srv


def test_admin_margin_route_registered_and_gated(_server):
    from api.context import require_admin

    route = next(
        (r for r in _server.app.routes
         if getattr(r, "path", None) == "/admin/margin"),
        None,
    )
    assert route is not None, "/admin/margin route is not registered"
    assert "GET" in route.methods
    # The require_admin dependency must guard the endpoint.
    dep_calls = [d.call for d in route.dependant.dependencies]
    assert require_admin in dep_calls, "/admin/margin is not admin-gated"
