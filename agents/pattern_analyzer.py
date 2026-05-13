"""
agents/pattern_analyzer.py — Pattern Analytics Engine.

Pure-functional analytics over the 7 SQL views shipped in migration 027.
Each function accepts a Supabase PostgREST client and a user UUID, runs a
single query, applies deterministic math (no LLM), and returns a frozen
dataclass.

Source views (per db/migrations/2026_05_12_027_analytics_views.sql):
    v_application_funnel
    v_application_funnel_by_grade
    v_application_funnel_by_archetype
    v_application_funnel_by_company_size
    v_rejection_signals
    v_resume_build_efficiency
    v_cost_per_outcome

Engineering notes
-----------------
- No external APIs. No LLM calls. No side effects.
- Every public function is per-user (multi-tenant safe).
- Sample-size gating: most analyses set `is_sufficient_data=False` until
  the user has enough data; the API surfaces "Not enough data yet" copy
  to the dashboard so we never display noise.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# ── Dataclasses ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FunnelSnapshot:
    """Aggregated application funnel for a single user."""
    applied: int
    responded: int
    interviewed: int
    offered: int
    accepted: int
    total: int
    conversion_rates: dict[str, float]
    is_sufficient_data: bool


@dataclass(frozen=True, slots=True)
class PartitionedFunnel:
    """Funnel broken down by a single dimension (grade / archetype / size).

    `partitions` is a list of dicts. Each dict has the original view
    columns + `conversion_rates` + `partition_value` so the dashboard can
    render any subset.
    """
    dimension: str
    partitions: list[dict[str, Any]]
    is_sufficient_data: bool


@dataclass(frozen=True, slots=True)
class RejectionPattern:
    """One anomalous cluster of rejections on a single dimension value."""
    dimension: str
    value: str
    n: int
    rate: float
    baseline_rate: float
    lift: float
    avg_days_to_rejection: float | None


@dataclass(frozen=True, slots=True)
class CostEfficiencyRow:
    """Per-company cost-efficiency metrics."""
    company_name: str
    total_cost_usd: float
    total_calls: int
    interviews: int
    offers: int
    cost_per_outcome: float | None
    is_sufficient_data: bool


# ── Internal helpers ───────────────────────────────────────────────────

# Dimension → (view_name, partition_key)
_FUNNEL_VIEW_MAP: dict[str, tuple[str, str]] = {
    "grade": ("v_application_funnel_by_grade", "letter_grade"),
    "archetype": ("v_application_funnel_by_archetype", "archetype"),
    "size": ("v_application_funnel_by_company_size", "company_size_band"),
}

# Dimensions we cluster rejections by when mining patterns.
_REJECTION_DIMENSIONS: tuple[str, ...] = (
    "company_size_band",
    "letter_grade",
    "archetype",
    "legitimacy_tier",
)

# Sample-size thresholds.
_MIN_TOTAL_FUNNEL: int = 5
_MIN_TOTAL_REJECTIONS: int = 5


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _compute_conversion_rates(row: dict[str, Any]) -> dict[str, float]:
    """Stage-to-stage conversion rates from a funnel row."""
    applied = int(row.get("applied", 0))
    responded = int(row.get("responded", 0))
    interviewed = int(row.get("interviewed", 0))
    offered = int(row.get("offered", 0))
    accepted = int(row.get("accepted", 0))

    return {
        "applied_to_responded": _safe_div(responded, applied),
        "responded_to_interviewed": _safe_div(interviewed, responded),
        "interviewed_to_offered": _safe_div(offered, interviewed),
        "offered_to_accepted": _safe_div(accepted, offered),
        "applied_to_offered": _safe_div(offered, applied),
        "applied_to_accepted": _safe_div(accepted, applied),
    }


# ══════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════


def analyze_funnel(user_id: UUID | str, db) -> FunnelSnapshot:
    """Top-level funnel for the user.

    Reads v_application_funnel scoped to user_id. Returns a snapshot with
    `is_sufficient_data=False` until the user has ≥5 applications.
    """
    uid = str(user_id)
    try:
        resp = (
            db.table("v_application_funnel")
            .select("*")
            .eq("user_id", uid)
            .execute()
        )
        rows = resp.data or []
        data: dict[str, Any] = rows[0] if rows else {}
    except Exception:
        logger.exception("v_application_funnel query failed for user %s", uid)
        data = {}

    applied = int(data.get("applied", 0))
    responded = int(data.get("responded", 0))
    interviewed = int(data.get("interviewed", 0))
    offered = int(data.get("offered", 0))
    accepted = int(data.get("accepted", 0))
    total = int(data.get("total", 0))

    return FunnelSnapshot(
        applied=applied,
        responded=responded,
        interviewed=interviewed,
        offered=offered,
        accepted=accepted,
        total=total,
        conversion_rates=_compute_conversion_rates(data),
        is_sufficient_data=total >= _MIN_TOTAL_FUNNEL,
    )


def analyze_funnel_by(user_id: UUID | str, dimension: str, db) -> PartitionedFunnel:
    """Dimension-partitioned funnel.

    Supported dimensions: "grade", "archetype", "size".
    Returns empty `partitions` for unknown dimensions.
    """
    if dimension not in _FUNNEL_VIEW_MAP:
        return PartitionedFunnel(dimension=dimension, partitions=[], is_sufficient_data=False)

    view_name, partition_key = _FUNNEL_VIEW_MAP[dimension]
    uid = str(user_id)

    try:
        resp = (
            db.table(view_name)
            .select("*")
            .eq("user_id", uid)
            .execute()
        )
        raw_rows: list[dict[str, Any]] = list(resp.data or [])
    except Exception:
        logger.exception("%s query failed for user %s", view_name, uid)
        raw_rows = []

    partitions: list[dict[str, Any]] = []
    total_across = 0
    for row in raw_rows:
        row = dict(row)
        row["conversion_rates"] = _compute_conversion_rates(row)
        row["partition_value"] = row.get(partition_key)
        row["key"] = row.get(partition_key)  # API convenience alias
        partitions.append(row)
        total_across += int(row.get("total", 0))

    return PartitionedFunnel(
        dimension=dimension,
        partitions=partitions,
        is_sufficient_data=total_across >= _MIN_TOTAL_FUNNEL,
    )


def find_rejection_patterns(
    user_id: UUID | str,
    db,
    *,
    min_sample: int = _MIN_TOTAL_REJECTIONS,
    min_lift: float = 1.5,
) -> list[RejectionPattern]:
    """Mine clusters of rejections that occur at significantly elevated rates.

    For each clustering dimension in _REJECTION_DIMENSIONS, count rejections
    per dimension value. A pattern is emitted when:
      - the cluster has n >= min_sample rejections AND
      - the cluster's share of total rejections is at least min_lift times
        the expected baseline share (1 / number of values).

    Returns an empty list when fewer than min_sample total rejections exist.
    """
    uid = str(user_id)
    try:
        resp = (
            db.table("v_rejection_signals")
            .select("*")
            .eq("user_id", uid)
            .execute()
        )
        signals: list[dict[str, Any]] = list(resp.data or [])
    except Exception:
        logger.exception("v_rejection_signals query failed for user %s", uid)
        return []

    total_rejections = len(signals)
    if total_rejections < min_sample:
        return []

    patterns: list[RejectionPattern] = []
    for dim in _REJECTION_DIMENSIONS:
        # Bucket rejections by dimension value.
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in signals:
            v = row.get(dim)
            if v is None or v == "":
                continue
            buckets[str(v)].append(row)

        if not buckets:
            continue

        n_values = len(buckets)
        baseline = 1.0 / n_values

        for value, rows in buckets.items():
            n = len(rows)
            if n < min_sample:
                continue
            rate = n / total_rejections
            lift = rate / baseline if baseline > 0 else 0.0
            if lift < min_lift:
                continue
            # Average time-to-rejection (if column exists).
            days = [
                int(r["days_to_rejection"])
                for r in rows
                if r.get("days_to_rejection") is not None
            ]
            avg_days = (sum(days) / len(days)) if days else None
            patterns.append(
                RejectionPattern(
                    dimension=dim,
                    value=value,
                    n=n,
                    rate=round(rate, 4),
                    baseline_rate=round(baseline, 4),
                    lift=round(lift, 3),
                    avg_days_to_rejection=avg_days,
                )
            )

    # Sort by lift desc so the biggest signals come first.
    patterns.sort(key=lambda p: p.lift, reverse=True)
    return patterns


def cost_efficiency(
    user_id: UUID | str,
    db,
    *,
    min_outcomes: int = 1,
) -> list[CostEfficiencyRow]:
    """Per-company cost-per-outcome rows.

    Reads v_cost_per_outcome scoped to the user. Emits one
    CostEfficiencyRow per company, with `is_sufficient_data=False` for
    companies that have spend but no outcomes yet.
    """
    uid = str(user_id)
    try:
        resp = (
            db.table("v_cost_per_outcome")
            .select("*")
            .eq("user_id", uid)
            .execute()
        )
        rows: list[dict[str, Any]] = list(resp.data or [])
    except Exception:
        logger.exception("v_cost_per_outcome query failed for user %s", uid)
        return []

    out: list[CostEfficiencyRow] = []
    for r in rows:
        interviews = int(r.get("interviews") or 0)
        offers = int(r.get("offers") or 0)
        total_outcomes = interviews + offers
        cpo_raw = r.get("cost_per_outcome")
        cpo: float | None = float(cpo_raw) if cpo_raw is not None else None
        out.append(
            CostEfficiencyRow(
                company_name=r.get("company_name") or "",
                total_cost_usd=float(r.get("total_cost_usd") or 0.0),
                total_calls=int(r.get("total_calls") or 0),
                interviews=interviews,
                offers=offers,
                cost_per_outcome=cpo,
                is_sufficient_data=total_outcomes >= min_outcomes,
            )
        )
    # Sort by cost_per_outcome ascending (NULLs last).
    out.sort(key=lambda r: (r.cost_per_outcome is None, r.cost_per_outcome or 0))
    return out
