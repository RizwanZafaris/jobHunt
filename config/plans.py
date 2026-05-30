"""
config/plans.py — plan tiers: monthly price + monthly LLM-cost allowance.

The **allowance** is the per-tenant MONTH-TO-DATE COGS ceiling that the spend
gate (``agents/budget_gate.py``) enforces — NOT the price. Keeping the
allowance well below the price is the mechanism that makes every paying tenant
gross-margin positive: a tenant can never burn more LLM spend than
``monthly_cost_allowance_usd`` in a calendar month (``unlimited`` plans —
owner/admin — excepted).

Keyed by the ``users.plan`` TEXT column (migration 001: defaults to ``'free'``
for new users, ``'lifetime'`` for the seed owner). Unknown/empty plan names
resolve to the most conservative tier (``free``) so a misconfigured row can
never get an unbounded allowance by accident.

Pure config (no DB, no I/O) — trivially testable and importable anywhere.
Tune the numbers with real usage data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Plan:
    name: str
    price_usd_month: float              # what the tenant pays per month
    monthly_cost_allowance_usd: float   # COGS ceiling the spend gate enforces
    unlimited: bool = False             # True → never capped (owner/admin)

    @property
    def floor_margin_pct(self) -> Optional[float]:
        """Gross margin % if the tenant spends their FULL allowance.

        None for unlimited or unpriced plans (margin undefined). This is the
        *floor* — a tenant that spends less has a higher margin; the gate
        guarantees it can never drop below this.
        """
        if self.unlimited or self.price_usd_month <= 0:
            return None
        return round(
            100.0
            * (self.price_usd_month - self.monthly_cost_allowance_usd)
            / self.price_usd_month,
            1,
        )


# Keyed by users.plan (lowercased). Unknown/empty → FREE (most conservative).
PLANS: dict[str, Plan] = {
    "free":     Plan("free",     0.0,   2.0),
    "pro":      Plan("pro",      49.0,  20.0),
    "scale":    Plan("scale",    199.0, 90.0),
    # Owner / early-backer / admin — never capped.
    "lifetime": Plan("lifetime", 0.0,   0.0, unlimited=True),
    "admin":    Plan("admin",    0.0,   0.0, unlimited=True),
}

DEFAULT_PLAN: Plan = PLANS["free"]


def get_plan(plan_name: Optional[str]) -> Plan:
    """Resolve a ``users.plan`` value to a :class:`Plan`.

    Case-insensitive; unknown/empty → :data:`DEFAULT_PLAN` (``free``).
    """
    if not plan_name:
        return DEFAULT_PLAN
    return PLANS.get(plan_name.strip().lower(), DEFAULT_PLAN)


def compute_margin(plan_name: Optional[str], spend_usd: float) -> dict:
    """Pure margin math for one tenant's month-to-date spend.

    Returns a JSON-serialisable dict the admin ``/admin/margin`` report rows are
    built from. Kept here (no I/O) so the arithmetic is unit-testable without a
    DB. ``revenue − cost = margin``; ``margin_pct`` is margin as a share of
    revenue. For unlimited/unpriced plans (owner/admin, free) revenue is 0 so
    ``margin_pct`` is None (undefined) — those tenants are cost centres by
    design, not margin-bearing.

    ``allowance_used_pct`` shows how close the tenant is to the spend cap
    (None for unlimited plans, which have no cap). ``over_allowance`` flags a
    tenant whose spend has reached/exceeded the cap — with the gate enabled this
    can only be transiently true (the cap halts further spend), but it surfaces
    a misconfiguration or a pre-enablement overspend.
    """
    plan = get_plan(plan_name)
    spend = round(float(spend_usd or 0), 6)
    revenue = plan.price_usd_month
    margin = round(revenue - spend, 6)
    margin_pct = (
        None if revenue <= 0 else round(100.0 * margin / revenue, 1)
    )
    allowance = plan.monthly_cost_allowance_usd
    if plan.unlimited:
        allowance_used_pct = None
        over_allowance = False
    else:
        allowance_used_pct = (
            None if allowance <= 0 else round(100.0 * spend / allowance, 1)
        )
        over_allowance = spend >= allowance if allowance > 0 else spend > 0
    return {
        "plan": plan.name,
        "unlimited": plan.unlimited,
        "revenue_usd": round(revenue, 2),
        "cost_usd": spend,
        "margin_usd": margin,
        "margin_pct": margin_pct,
        "allowance_usd": round(allowance, 2),
        "allowance_used_pct": allowance_used_pct,
        "over_allowance": over_allowance,
    }
