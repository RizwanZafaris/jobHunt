"""
api/offers.py — Tier 4 G8 Offer Evaluation endpoints.

Routes (prefixed with /offers):
    POST   /offers/evaluate-offer
            Body: {application_id: UUID, offer_text: str, force?: bool}
            Returns: full evaluation row (synchronous; the graph is fast
            enough — ~$0.40 cost, ~10-30s end-to-end — that we don't need
            the RQ enqueue dance G2 uses).
    GET    /offers/{evaluation_id}
            Returns: the offer_evaluations row.
    PATCH  /offers/{evaluation_id}/decision
            Body: {user_decision: enum, final_total_comp?: float}
            Records the human decision.
    POST   /offers/{evaluation_id}/regenerate
            Body: {offer_text?: str, force?: bool}
            Re-runs the graph for an existing evaluation row.

Engineering principles
----------------------
- Wallet guard: hydration in agents/g8_io.py calls
  api/_job_guards.py::load_open_job which raises HTTPException(409)
  on stale jobs. Pass `force=true` to override.
- Per-tenant: every endpoint takes `user: User = Depends(get_current_user)`
  and scopes all DB queries to user.id.
- Identity-lock + cites: persisted automatically by the G8 graph.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from agents.g8_io import (
    fetch_offer_evaluation,
    regenerate_offer_evaluation,
    run_g8_for_offer,
    update_offer_decision,
)
from api.context import get_current_user
from api.users import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/offers", tags=["offers-g8"])


# ── Request / response models ──────────────────────────────────────────
class EvaluateOfferRequest(BaseModel):
    application_id: UUID = Field(..., description="FK to applications.id (UUID)")
    offer_text: str = Field(..., min_length=10, max_length=20_000)
    force: bool = Field(
        default=False,
        description="Override wallet guard for closed/failed parent jobs.",
    )


class OfferDecisionRequest(BaseModel):
    user_decision: str = Field(
        ...,
        description="One of: accepted | negotiated_accepted | negotiated_declined | declined | expired",
    )
    final_total_comp: Optional[float] = Field(
        default=None,
        ge=0,
        description="Final negotiated total comp in USD.",
    )


class RegenerateOfferRequest(BaseModel):
    force: bool = Field(default=False)


class OfferEvaluationResponse(BaseModel):
    """Full evaluation row (all 41 columns of offer_evaluations).

    Anything `None` means the upstream node didn't produce a value
    (e.g. equity_grant_usd None when no RSU was detected in the offer).
    """
    id: UUID
    user_id: UUID
    application_id: UUID
    job_id: Optional[int] = None
    # Comp components
    base_salary_usd: Optional[float] = None
    target_bonus_usd: Optional[float] = None
    equity_grant_usd: Optional[float] = None
    sign_on_bonus_usd: Optional[float] = None
    other_benefits_usd: Optional[float] = None
    total_comp_year_1: Optional[float] = None
    total_comp_4yr_avg: Optional[float] = None
    currency: str = "USD"
    # Role descriptors
    level: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    start_date: Optional[str] = None
    # Market research
    market_p25: Optional[float] = None
    market_p50: Optional[float] = None
    market_p75: Optional[float] = None
    market_p90: Optional[float] = None
    market_summary: Optional[str] = None
    market_citations: list[dict] = Field(default_factory=list)
    # Negotiation plan
    negotiation_anchor: Optional[float] = None
    negotiation_walkaway: Optional[float] = None
    negotiation_script: Optional[str] = None
    archetype_strategy: Optional[str] = None
    # Risk
    risk_score: Optional[float] = None
    risk_factors: list[dict] = Field(default_factory=list)
    recommendation: Optional[str] = None
    confidence: Optional[float] = None
    risk_adjusted_total_comp: Optional[float] = None
    sleep_on_it_score: Optional[float] = None
    rationale_md: Optional[str] = None
    # Telemetry
    total_cost_usd: Optional[float] = None
    total_latency_ms: Optional[int] = None
    cites: list[dict] = Field(default_factory=list)
    # Outcome capture
    user_decision: Optional[str] = None
    final_total_comp: Optional[float] = None
    user_decision_at: Optional[str] = None
    # Timestamps
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _row_to_response(row: dict[str, Any]) -> OfferEvaluationResponse:
    """Coerce a DB row into the response model."""
    return OfferEvaluationResponse(**{
        k: v for k, v in row.items()
        if k in OfferEvaluationResponse.model_fields
    })


# ══════════════════════════════════════════════════════════════════════════
# POST /offers/evaluate-offer
# ══════════════════════════════════════════════════════════════════════════
@router.post("/evaluate-offer", response_model=OfferEvaluationResponse)
async def evaluate_offer(
    body: EvaluateOfferRequest,
    user: User = Depends(get_current_user),
) -> OfferEvaluationResponse:
    """Run the full 5-node G8 graph for this offer + application.

    Synchronous: G8 runs in ~10-30s for ~$0.40. The frontend can show a
    spinner; if we ever need to async this, the pattern is the same RQ
    enqueue + poll loop used by G2 (see api/queue.py::enqueue_g2_build).
    """
    evaluation_id = await run_g8_for_offer(
        user_id=str(user.id),
        application_id=str(body.application_id),
        offer_text=body.offer_text,
        force=body.force,
    )
    row = fetch_offer_evaluation(evaluation_id, str(user.id))
    return _row_to_response(row)


# ══════════════════════════════════════════════════════════════════════════
# GET /offers/{evaluation_id}
# ══════════════════════════════════════════════════════════════════════════
@router.get("/{evaluation_id}", response_model=OfferEvaluationResponse)
async def get_offer(
    evaluation_id: UUID = Path(...),
    user: User = Depends(get_current_user),
) -> OfferEvaluationResponse:
    """Fetch a single offer evaluation by id."""
    row = fetch_offer_evaluation(str(evaluation_id), str(user.id))
    return _row_to_response(row)


# ══════════════════════════════════════════════════════════════════════════
# PATCH /offers/{evaluation_id}/decision
# ══════════════════════════════════════════════════════════════════════════
@router.patch("/{evaluation_id}/decision")
async def update_decision(
    body: OfferDecisionRequest,
    evaluation_id: UUID = Path(...),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Record the human decision on an offer evaluation.

    Writes user_decision + final_total_comp + user_decision_at.
    These power the outcome-attribution loop (cite credits in
    outcome_to_persona).
    """
    return update_offer_decision(
        str(evaluation_id),
        str(user.id),
        user_decision=body.user_decision,
        final_total_comp=body.final_total_comp,
    )


# ══════════════════════════════════════════════════════════════════════════
# POST /offers/{evaluation_id}/regenerate
# ══════════════════════════════════════════════════════════════════════════
@router.post("/{evaluation_id}/regenerate", response_model=OfferEvaluationResponse)
async def regenerate(
    body: RegenerateOfferRequest,
    evaluation_id: UUID = Path(...),
    user: User = Depends(get_current_user),
) -> OfferEvaluationResponse:
    """Re-run the G8 graph for an existing evaluation row (in place).

    Useful when:
      - market_analyzer cache miss + Perplexity returned thin data, user
        wants a retry
      - User pasted a new revised offer letter and wants the row updated
    """
    new_id = await regenerate_offer_evaluation(
        str(evaluation_id),
        str(user.id),
        force=body.force,
    )
    row = fetch_offer_evaluation(new_id, str(user.id))
    return _row_to_response(row)
