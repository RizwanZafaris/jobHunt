"""tests/test_admin_rebuild_personas.py — /admin/personas/rebuild-all.

Verifies the endpoint is registered, admin-gated, and that its planning logic
selects the right companies (below-threshold quality + missing) by calling the
handler directly with a monkeypatched aexecute (no DB, no network, no LLM).
"""
import asyncio
from types import SimpleNamespace

import pytest


@pytest.fixture
def srv(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    import config.settings as _cs
    _cs._settings = None
    import api.server as _srv
    return _srv


class _BG:
    """Captures background_tasks.add_task without running it."""
    def __init__(self):
        self.tasks = []
    def add_task(self, fn, *a, **k):
        self.tasks.append((fn, a, k))


def _patch_two_queries(monkeypatch, srv, companies, personas):
    """aexecute is called twice: first companies, then company_personas."""
    calls = {"n": 0}

    async def _ax(_q):
        calls["n"] += 1
        return SimpleNamespace(data=companies if calls["n"] == 1 else personas)

    monkeypatch.setattr(srv, "aexecute", _ax)


def test_route_registered_and_admin_gated(srv):
    from api.context import require_admin
    route = next(
        (r for r in srv.app.routes
         if getattr(r, "path", None) == "/admin/personas/rebuild-all"),
        None,
    )
    assert route is not None, "route not registered"
    assert "POST" in route.methods
    deps = [d.call for d in route.dependant.dependencies]
    assert require_admin in deps, "not admin-gated"


def test_plan_selects_low_and_missing(srv, monkeypatch):
    companies = [{"name": "Adyen"}, {"name": "Stripe"}, {"name": "Visa"}, {"name": "Mastercard"}]
    personas = [
        {"company_name": "Adyen", "metadata": {"persona_quality": "low"}},
        {"company_name": "Stripe", "metadata": {"persona_quality": "high"}},
        {"company_name": "Visa", "metadata": {"persona_quality": "medium"}},
        # Mastercard: no persona row → "missing"
    ]
    _patch_two_queries(monkeypatch, srv, companies, personas)
    res = asyncio.run(srv.admin_rebuild_personas(
        background_tasks=_BG(), below="medium", include_missing=True, limit=200,
    ))
    names = {p["company"] for p in res["plan"]}
    # low (Adyen) + missing (Mastercard) rebuilt; high/medium skipped.
    assert names == {"Adyen", "Mastercard"}
    assert res["rebuild_count"] == 2
    assert res["targets_total"] == 4
    assert res["status"] == "started"


def test_exclude_missing_when_flag_off(srv, monkeypatch):
    companies = [{"name": "Adyen"}, {"name": "Mastercard"}]
    personas = [{"company_name": "Adyen", "metadata": {"persona_quality": "low"}}]
    _patch_two_queries(monkeypatch, srv, companies, personas)
    res = asyncio.run(srv.admin_rebuild_personas(
        background_tasks=_BG(), below="medium", include_missing=False, limit=200,
    ))
    names = {p["company"] for p in res["plan"]}
    assert names == {"Adyen"}  # Mastercard (missing) excluded


def test_unknown_quality_is_rebuilt(srv, monkeypatch):
    companies = [{"name": "Adyen"}]
    personas = [{"company_name": "Adyen", "metadata": {}}]  # no persona_quality → unknown
    _patch_two_queries(monkeypatch, srv, companies, personas)
    res = asyncio.run(srv.admin_rebuild_personas(
        background_tasks=_BG(), below="medium", include_missing=True, limit=200,
    ))
    assert {p["company"] for p in res["plan"]} == {"Adyen"}


def test_limit_caps_roster(srv, monkeypatch):
    companies = [{"name": f"C{i}"} for i in range(10)]
    personas = []  # all missing
    _patch_two_queries(monkeypatch, srv, companies, personas)
    res = asyncio.run(srv.admin_rebuild_personas(
        background_tasks=_BG(), below="medium", include_missing=True, limit=3,
    ))
    assert res["rebuild_count"] == 3
