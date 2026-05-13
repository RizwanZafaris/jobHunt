"""
api/analytics.py — Pattern Analytics REST API.

Six read-only endpoints feeding the /insights/analytics dashboard:

  GET /analytics/funnel                  — top-level user funnel
  GET /analytics/funnel-by-grade         — partitioned by jobs.letter_grade
  GET /analytics/funnel-by-archetype     — partitioned by jobs.archetype
  GET /analytics/funnel-by-size          — partitioned by companies.headcount
  GET /analytics/rejection-patterns      — clustered rejection signals
  GET /analytics/cost-efficiency         — cost/outcome per company

No wallet guard required — pure SQL queries with zero LLM cost. Every
endpoint scopes queries to the authenticated user's id (defence-in-depth
on top of RLS).

Cache headers: Cache-Control: private, max-age=60 — analytics shifts
slowly enough that 60s of staleness is acceptable for dashboard latency.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from agents.pattern_analyzer import (
    CostEfficiencyRow,
    FunnelSnapshot,
    PartitionedFunnel,
    RejectionPattern,
    analyze_funnel,
    analyze_funnel_by,
    cost_efficiency,
    find_rejection_patterns,
)
from api.context import get_current_user
from api.users import User
from db.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Below this threshold, the dashboard renders a "Not enough data yet"
# placeholder instead of a graph. Keeps the surface honest at low scale.
MIN_APPLICATIONS_FOR_FUNNEL = 5
MIN_REJECTIONS_FOR_PATTERNS = 5


# ── Response models ─────────────────────────────────────────────────────


class ConversionRates(BaseModel):
    applied_to_responded: float = 0.0
    responded_to_interviewed: float = 0.0
    interviewed_to_offered: float = 0.0
    offered_to_accepted: float = 0.0
    applied_to_offered: float = 0.0
    applied_to_accepted: float = 0.0


class FunnelResponse(BaseModel):
    applied: int = 0
    responded: int = 0
    interviewed: int = 0
    offered: int = 0
    accepted: int = 0
    total: int = 0
    conversion_rates: ConversionRates = Field(default_factory=ConversionRates)
    is_sufficient_data: bool = False
    message: str = ""


class PartitionEntry(BaseModel):
    key: str
    applied: int = 0
    responded: int = 0
    interviewed: int = 0
    offered: int = 0
    accepted: int = 0
    total: int = 0
    conversion_rates: ConversionRates = Field(default_factory=ConversionRates)


class PartitionedFunnelResponse(BaseModel):
    dimension: str
    partitions: list[PartitionEntry] = Field(default_factory=list)
    is_sufficient_data: bool = False
    message: str = ""


class RejectionPatternItem(BaseModel):
    dimension: str
    value: str
    n: int
    rate: float
    baseline_rate: float
    lift: float
    avg_days_to_rejection: Optional[float] = None


class RejectionPatternsResponse(BaseModel):
    patterns: list[RejectionPatternItem] = Field(default_factory=list)
    is_sufficient_data: bool = False
    message: str = ""


class CostEfficiencyItem(BaseModel):
    company_name: str
    total_cost_usd: float = 0.0
    total_calls: int = 0
    interviews: int = 0
    offers: int = 0
    cost_per_outcome: Optional[float] = None
    is_sufficient_data: bool = False


class CostEfficiencyResponse(BaseModel):
    rows: list[CostEfficiencyItem] = Field(default_factory=list)
    message: str = ""


# ── Helpers ─────────────────────────────────────────────────────────────


def _set_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "private, max-age=60"


def _conv_to_response(d: dict[str, float] | None) -> ConversionRates:
    """Coerce a {applied_to_responded: float, ...} dict to ConversionRates."""
    if not d:
        return ConversionRates()
    fields = ConversionRates.model_fields.keys()
    kwargs = {k: float(d.get(k, 0.0) or 0.0) for k in fields}
    return ConversionRates(**kwargs)


def _funnel_to_response(snap: FunnelSnapshot) -> FunnelResponse:
    msg = (
        ""
        if snap.is_sufficient_data
        else f"Not enough data yet ({snap.total} / {MIN_APPLICATIONS_FOR_FUNNEL} needed)"
    )
    return FunnelResponse(
        applied=snap.applied,
        responded=snap.responded,
        interviewed=snap.interviewed,
        offered=snap.offered,
        accepted=snap.accepted,
        total=snap.total,
        conversion_rates=_conv_to_response(snap.conversion_rates),
        is_sufficient_data=snap.is_sufficient_data,
        message=msg,
    )


def _partitioned_to_response(p: PartitionedFunnel) -> PartitionedFunnelResponse:
    entries: list[PartitionEntry] = []
    total = 0
    for row in p.partitions:
        entries.append(
            PartitionEntry(
                key=str(row.get("key") or row.get("partition_value") or "unknown"),
                applied=int(row.get("applied", 0) or 0),
                responded=int(row.get("responded", 0) or 0),
                interviewed=int(row.get("interviewed", 0) or 0),
                offered=int(row.get("offered", 0) or 0),
                accepted=int(row.get("accepted", 0) or 0),
                total=int(row.get("total", 0) or 0),
                conversion_rates=_conv_to_response(row.get("conversion_rates")),
            )
        )
        total += int(row.get("total", 0) or 0)

    msg = (
        ""
        if p.is_sufficient_data
        else f"Not enough data yet ({total} / {MIN_APPLICATIONS_FOR_FUNNEL} needed)"
    )
    return PartitionedFunnelResponse(
        dimension=p.dimension,
        partitions=entries,
        is_sufficient_data=p.is_sufficient_data,
        message=msg,
    )


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/funnel", response_model=FunnelResponse)
async def funnel(
    response: Response,
    user: User = Depends(get_current_user),
) -> FunnelResponse:
    """Top-level application funnel for the current user."""
    _set_cache(response)
    db = get_supabase()
    snap = analyze_funnel(user.id, db)
    return _funnel_to_response(snap)


@router.get("/funnel-by-grade", response_model=PartitionedFunnelResponse)
async def funnel_by_grade(
    response: Response,
    user: User = Depends(get_current_user),
) -> PartitionedFunnelResponse:
    """Funnel partitioned by jobs.letter_grade (A-F, NULL → '?')."""
    _set_cache(response)
    db = get_supabase()
    return _partitioned_to_response(analyze_funnel_by(user.id, "grade", db))


@router.get("/funnel-by-archetype", response_model=PartitionedFunnelResponse)
async def funnel_by_archetype(
    response: Response,
    user: User = Depends(get_current_user),
) -> PartitionedFunnelResponse:
    """Funnel partitioned by jobs.archetype."""
    _set_cache(response)
    db = get_supabase()
    return _partitioned_to_response(analyze_funnel_by(user.id, "archetype", db))


@router.get("/funnel-by-size", response_model=PartitionedFunnelResponse)
async def funnel_by_size(
    response: Response,
    user: User = Depends(get_current_user),
) -> PartitionedFunnelResponse:
    """Funnel partitioned by companies.headcount band."""
    _set_cache(response)
    db = get_supabase()
    return _partitioned_to_response(analyze_funnel_by(user.id, "size", db))


@router.get("/rejection-patterns", response_model=RejectionPatternsResponse)
async def rejection_patterns(
    response: Response,
    user: User = Depends(get_current_user),
    min_sample: int = Query(default=MIN_REJECTIONS_FOR_PATTERNS, ge=1, le=1000),
) -> RejectionPatternsResponse:
    """Anomalous rejection clusters by dimension."""
    _set_cache(response)
    db = get_supabase()
    patterns = find_rejection_patterns(user.id, db, min_sample=min_sample)
    items = [
        RejectionPatternItem(
            dimension=p.dimension,
            value=p.value,
            n=p.n,
            rate=p.rate,
            baseline_rate=p.baseline_rate,
            lift=p.lift,
            avg_days_to_rejection=p.avg_days_to_rejection,
        )
        for p in patterns
    ]
    is_sufficient = len(items) > 0
    return RejectionPatternsResponse(
        patterns=items,
        is_sufficient_data=is_sufficient,
        message=(
            ""
            if is_sufficient
            else f"Not enough rejection data yet (need at least {min_sample} clustered)"
        ),
    )


@router.get("/cost-efficiency", response_model=CostEfficiencyResponse)
async def cost_efficiency_endpoint(
    response: Response,
    user: User = Depends(get_current_user),
    min_outcomes: int = Query(default=1, ge=0, le=1000),
) -> CostEfficiencyResponse:
    """Per-company total LLM spend ÷ outcomes."""
    _set_cache(response)
    db = get_supabase()
    rows = cost_efficiency(user.id, db, min_outcomes=min_outcomes)
    items = [
        CostEfficiencyItem(
            company_name=r.company_name,
            total_cost_usd=r.total_cost_usd,
            total_calls=r.total_calls,
            interviews=r.interviews,
            offers=r.offers,
            cost_per_outcome=r.cost_per_outcome,
            is_sufficient_data=r.is_sufficient_data,
        )
        for r in rows
    ]
    return CostEfficiencyResponse(
        rows=items,
        message="" if items else "No company spend recorded yet",
    )
