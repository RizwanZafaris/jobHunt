"""
agents/budget_gate.py — per-tenant spend cap (Phase 3, audit P0).

Flag-gated by ``PER_TENANT_BUDGET_ENABLED`` (default OFF). When ON,
:func:`enforce_budget` raises :class:`BudgetExceeded` *before* an LLM call if the
tenant's month-to-date COGS has reached their plan's
``monthly_cost_allowance_usd`` (``config/plans.py``). This is the mechanism that
keeps every paying tenant gross-margin positive — a tenant can never burn more
LLM spend than their allowance in a calendar month. ``unlimited`` plans
(owner/admin) and ``is_admin`` users are never capped.

OFF by default → single-user prod is byte-for-byte unchanged: the gate is a
single ``os.environ`` check that returns immediately.

Self-contained: reads its flag + cache TTL directly from the environment, so it
has no dependency on settings loading (and is trivially testable). Hot-path
safe: the per-tenant plan + MTD spend are cached per ``user_id`` for
``PER_TENANT_BUDGET_CACHE_TTL_S`` seconds, so the gate adds at most one plan read
+ one spend SUM per user per TTL window — not one per LLM call. The spend SUM is
served by the ``(user_id, called_at DESC)`` index from migration 040 (which
names this gate as its consumer). Both fetches fail OPEN: a meter-query error
allows the call (never block paid work on a telemetry hiccup) and logs a warning.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from config.plans import Plan, get_plan

logger = logging.getLogger(__name__)

# Seed/owner user (single-user mode default), mirrors db/client.py + llm_router.
SEED_USER_ID = "00000000-0000-0000-0000-000000000001"

# Per-user cache: user_id -> (plan, is_admin, mtd_spend_usd, fetched_at_monotonic)
_cache: dict[str, tuple[Plan, bool, float, float]] = {}


class BudgetExceeded(Exception):
    """Raised pre-call when a tenant has hit their monthly cost allowance.

    Callers (API layer) translate this to HTTP 402/429. Carries the numbers so
    the caller can build an informative message without re-querying.
    """

    def __init__(
        self,
        user_id: str,
        spend_usd: float,
        allowance_usd: float,
        plan_name: str,
    ) -> None:
        self.user_id = user_id
        self.spend_usd = spend_usd
        self.allowance_usd = allowance_usd
        self.plan_name = plan_name
        super().__init__(
            f"Monthly LLM cost allowance exceeded for user {user_id} "
            f"(plan={plan_name}): ${spend_usd:.4f} used / ${allowance_usd:.2f} allowed"
        )


def budget_enabled() -> bool:
    """Master flag, read from the env directly so it can flip without a process
    restart (and so tests can monkeypatch ``os.environ``)."""
    return os.environ.get("PER_TENANT_BUDGET_ENABLED", "0") == "1"


def _ttl_s() -> int:
    try:
        return int(os.environ.get("PER_TENANT_BUDGET_CACHE_TTL_S", "60"))
    except (TypeError, ValueError):
        return 60


def reset_cache() -> None:
    """Drop the per-user cache (test helper / manual invalidation)."""
    _cache.clear()


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat()


async def _fetch_plan(user_id: str) -> tuple[Optional[str], bool]:
    """Return (plan_name, is_admin) for the user. Best-effort; (None, False) on
    error → resolves to the conservative ``free`` plan."""
    try:
        from db.client import aexecute, get_supabase

        resp = await aexecute(
            get_supabase()
            .table("users")
            .select("plan, is_admin")
            .eq("id", user_id)
            .limit(1)
        )
        rows = resp.data or []
        if rows:
            return rows[0].get("plan"), bool(rows[0].get("is_admin"))
    except Exception as e:  # noqa: BLE001 — fail-open by design
        logger.warning("budget gate: plan lookup failed for %s: %s", user_id, e)
    return None, False


async def _fetch_mtd_spend(user_id: str) -> float:
    """Sum ``agent_call_log.cost_usd`` for this user since the 1st of the month.
    Off the loop via the async seam; served by the migration-040 composite
    index. Fail-open → 0.0 on error."""
    try:
        from db.client import aexecute, get_supabase

        resp = await aexecute(
            get_supabase()
            .table("agent_call_log")
            .select("cost_usd")
            .eq("user_id", user_id)
            .gte("called_at", _month_start_iso())
        )
        rows = resp.data or []
        return round(sum(float(r.get("cost_usd") or 0) for r in rows), 6)
    except Exception as e:  # noqa: BLE001 — fail-open by design
        logger.warning(
            "budget gate: MTD spend query failed for %s (fail-open): %s",
            user_id, e,
        )
        return 0.0


async def _get_cached(user_id: str) -> tuple[Plan, bool, float]:
    """Return (plan, is_admin, mtd_spend_usd), refreshing past the TTL."""
    ttl = _ttl_s()
    now = time.monotonic()
    hit = _cache.get(user_id)
    if hit is not None and (now - hit[3]) < ttl:
        return hit[0], hit[1], hit[2]
    plan_name, is_admin = await _fetch_plan(user_id)
    plan = get_plan(plan_name)
    spend = await _fetch_mtd_spend(user_id)
    _cache[user_id] = (plan, is_admin, spend, now)
    return plan, is_admin, spend


async def enforce_budget(
    user_id: Optional[str] = None,
    *,
    agent_name: Optional[str] = None,
) -> None:
    """Raise :class:`BudgetExceeded` if the tenant is over their monthly cost
    allowance. No-op when the flag is off, the user is admin, or the plan is
    unlimited. Safe to ``await`` on every LLM call: flag-off returns instantly,
    flag-on hits the per-user TTL cache.
    """
    if not budget_enabled():
        return
    uid = user_id or os.environ.get("RIZWAN_USER_ID", SEED_USER_ID)
    plan, is_admin, spend = await _get_cached(uid)
    if is_admin or plan.unlimited:
        return
    if spend >= plan.monthly_cost_allowance_usd:
        logger.warning(
            "budget gate: BLOCKING user=%s plan=%s spend=$%.4f >= allowance=$%.2f "
            "(agent=%s)",
            uid, plan.name, spend, plan.monthly_cost_allowance_usd, agent_name,
        )
        raise BudgetExceeded(uid, spend, plan.monthly_cost_allowance_usd, plan.name)
