"""
tests/test_budget_gate.py — per-tenant spend cap (Phase 3).

Covers config/plans resolution + the agents/budget_gate gate: flag-off no-op
(no DB touched), under/at/over allowance, admin + unlimited exemptions, per-user
TTL caching, fail-open on a DB error, and that LLMRouter.ask() enforces the cap
BEFORE spending. All DB access is monkeypatched — no network, no keys, no
settings loading.
"""
import asyncio
import os

import pytest

from agents import budget_gate as bg
from config.plans import DEFAULT_PLAN, get_plan


def _run(coro):
    return asyncio.run(coro)


def setup_function(_):
    bg.reset_cache()
    os.environ.pop("PER_TENANT_BUDGET_ENABLED", None)


def teardown_function(_):
    bg.reset_cache()
    os.environ.pop("PER_TENANT_BUDGET_ENABLED", None)


def _patch_fetches(monkeypatch, plan_name, is_admin, spend):
    async def _p(_uid):
        return (plan_name, is_admin)

    async def _s(_uid):
        return spend

    monkeypatch.setattr(bg, "_fetch_plan", _p)
    monkeypatch.setattr(bg, "_fetch_mtd_spend", _s)


# ── config/plans ────────────────────────────────────────────────────────────
def test_plan_resolution_is_case_insensitive_and_safe():
    assert get_plan("PRO").name == "pro"
    assert get_plan(None) is DEFAULT_PLAN
    assert get_plan("nonsense") is DEFAULT_PLAN  # unknown → conservative free
    assert get_plan("lifetime").unlimited is True
    assert get_plan("free").unlimited is False


def test_paid_plans_are_margin_positive_by_construction():
    for name in ("pro", "scale"):
        p = get_plan(name)
        assert p.floor_margin_pct is not None and p.floor_margin_pct > 0, name
    assert get_plan("lifetime").floor_margin_pct is None


# ── flag OFF (default) ──────────────────────────────────────────────────────
def test_flag_off_is_a_true_noop(monkeypatch):
    calls = {"plan": 0, "spend": 0}

    async def _p(_uid):
        calls["plan"] += 1
        return ("free", False)

    async def _s(_uid):
        calls["spend"] += 1
        return 999.0  # wildly over any allowance

    monkeypatch.setattr(bg, "_fetch_plan", _p)
    monkeypatch.setattr(bg, "_fetch_mtd_spend", _s)
    _run(bg.enforce_budget("u-free"))  # must NOT raise and must NOT query
    assert calls == {"plan": 0, "spend": 0}


# ── flag ON ─────────────────────────────────────────────────────────────────
def test_under_allowance_is_allowed(monkeypatch):
    monkeypatch.setenv("PER_TENANT_BUDGET_ENABLED", "1")
    _patch_fetches(monkeypatch, "pro", False, 5.0)  # pro allowance = 20
    _run(bg.enforce_budget("u-pro"))  # no raise


def test_over_allowance_blocks(monkeypatch):
    monkeypatch.setenv("PER_TENANT_BUDGET_ENABLED", "1")
    _patch_fetches(monkeypatch, "pro", False, 25.0)  # over the 20 allowance
    with pytest.raises(bg.BudgetExceeded) as ei:
        _run(bg.enforce_budget("u-pro"))
    assert ei.value.plan_name == "pro"
    assert ei.value.allowance_usd == 20.0


def test_at_exact_allowance_blocks(monkeypatch):
    # >= is the rule: hitting the allowance exactly halts (margin protection).
    monkeypatch.setenv("PER_TENANT_BUDGET_ENABLED", "1")
    _patch_fetches(monkeypatch, "pro", False, 20.0)
    with pytest.raises(bg.BudgetExceeded):
        _run(bg.enforce_budget("u-pro"))


def test_admin_user_is_exempt(monkeypatch):
    monkeypatch.setenv("PER_TENANT_BUDGET_ENABLED", "1")
    _patch_fetches(monkeypatch, "free", True, 9999.0)  # is_admin overrides
    _run(bg.enforce_budget("u-admin"))  # no raise


def test_unlimited_plan_is_exempt(monkeypatch):
    monkeypatch.setenv("PER_TENANT_BUDGET_ENABLED", "1")
    _patch_fetches(monkeypatch, "lifetime", False, 9999.0)
    _run(bg.enforce_budget("u-life"))  # no raise


def test_cache_avoids_refetch_within_ttl(monkeypatch):
    monkeypatch.setenv("PER_TENANT_BUDGET_ENABLED", "1")
    n = {"spend": 0}

    async def _p(_uid):
        return ("pro", False)

    async def _s(_uid):
        n["spend"] += 1
        return 1.0

    monkeypatch.setattr(bg, "_fetch_plan", _p)
    monkeypatch.setattr(bg, "_fetch_mtd_spend", _s)
    _run(bg.enforce_budget("u-cache"))
    _run(bg.enforce_budget("u-cache"))
    assert n["spend"] == 1  # second call served from the per-user TTL cache


def test_mtd_spend_query_is_fail_open(monkeypatch):
    # The real _fetch_mtd_spend must swallow a DB error and return 0.0 so a
    # telemetry hiccup never blocks paid work.
    import db.client as dbc

    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(dbc, "get_supabase", _boom)
    assert _run(bg._fetch_mtd_spend("u")) == 0.0


# ── LLMRouter.ask() wiring ──────────────────────────────────────────────────
def test_router_ask_enforces_cap_before_spending(monkeypatch):
    # Over budget + flag on → ask() must raise BudgetExceeded before it ever
    # constructs a provider client (so this needs no API key / network).
    monkeypatch.setenv("PER_TENANT_BUDGET_ENABLED", "1")
    _patch_fetches(monkeypatch, "pro", False, 100.0)
    from agents.llm_router import LLMRouter

    router = LLMRouter()  # no keys configured
    with pytest.raises(bg.BudgetExceeded):
        _run(
            router.ask(
                provider="anthropic",
                model="claude-sonnet-4-6",
                system="s",
                messages=[{"role": "user", "content": "hi"}],
                user_id="u-over",
            )
        )
