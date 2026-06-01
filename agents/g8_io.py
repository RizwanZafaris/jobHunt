"""
agents/g8_io.py — All Supabase reads/writes + public orchestrator for G8.

Tables touched:
    READ   applications, jobs, company_personas, company_knowledge
    WRITE  offer_evaluations

Public entry points:
  - run_g8_for_offer(user_id, application_id, offer_text, *, force=False)
      → runs the full 5-node graph synchronously, persists, returns
        the offer_evaluations.id (UUID str).
  - regenerate_offer_evaluation(evaluation_id, user_id, *, force=False)
      → re-runs the graph for an existing row (idempotent).
  - fetch_offer_evaluation(evaluation_id, user_id)
      → row dict.
  - update_offer_decision(evaluation_id, user_id, user_decision,
                          final_total_comp)
      → writes user_decision + final_total_comp + user_decision_at.

Engineering principles
----------------------
1. Wallet protection: every entry point calls
   `api/_job_guards.py::load_open_job(db, job_id=..., user_id=...,
   force=..., cost_label="G8:evaluate-offer")` BEFORE any LLM spend.
   The guard raises HTTPException(409) on closed/failed jobs.
2. FK shapes: user_id UUID, application_id UUID, job_id INTEGER. See
   db/SCHEMA_LIVE_STATE.md §2.
3. Cost telemetry: every LLM call inside the nodes auto-writes to
   `agent_call_log` via `agents/llm_router.py`. The persist node also
   rolls up the per-row total to `offer_evaluations.total_cost_usd`.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import HTTPException

from agents.g8_state import OfferEvaluationState
from agents.g8 import (
    market_analyzer_node,
    negotiation_strategist_node,
    offer_parser_node,
    risk_detector_node,
    synthesizer_node,
)
from api._job_guards import load_open_job

logger = logging.getLogger(__name__)

_GRAPH_NAME = "G8"


# ══════════════════════════════════════════════════════════════════════════
# Hydration — read all the rows the nodes need before spending tokens
# ══════════════════════════════════════════════════════════════════════════
def _hydrate(application_id: str, user_id: str) -> dict[str, Any]:
    """Read application + job + company_persona + company_knowledge."""
    from db.client import get_supabase

    sb = get_supabase()

    app_rows = (
        sb.table("applications")
        .select("*")
        .eq("id", application_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not app_rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "application_not_found", "application_id": application_id},
        )
    app_row = app_rows[0]

    job_id = app_row.get("job_id")
    job_row: dict | None = None
    if job_id is not None:
        # Wallet guard — closes the OKX-1641 archetype before any LLM spend.
        # load_open_job raises HTTPException(409) on closed/validation_failed.
        # We don't pass force here; the caller's force flag is applied below.
        try:
            job_row = load_open_job(
                sb,
                job_id=int(job_id),
                user_id=UUID(user_id),
                force=False,
                cost_label="G8:evaluate-offer",
            )
        except HTTPException:
            # Re-raise so the orchestrator entry point can pass through.
            raise

    # Company persona (banned_keywords + success_patterns)
    persona_rows: list[dict] = []
    company_name = (job_row or {}).get("company") or app_row.get("company")
    if company_name:
        persona_rows = (
            sb.table("company_personas")
            .select("*")
            .eq("user_id", user_id)
            .ilike("company_name", company_name)
            .limit(1)
            .execute()
            .data
        ) or []
    company_persona = persona_rows[0] if persona_rows else None

    # Company knowledge (last 90d) — for risk_detector RAG.
    knowledge: list[dict] = []
    if company_name:
        knowledge = (
            sb.table("company_knowledge")
            .select("id, kind, title, content, snippet, published_at, captured_at")
            .eq("user_id", user_id)
            .ilike("company_name", company_name)
            .order("captured_at", desc=True)
            .limit(30)
            .execute()
            .data
        ) or []

    return {
        "application_row": app_row,
        "job_row": job_row,
        "company_persona": company_persona,
        "company_knowledge": knowledge,
    }


# ══════════════════════════════════════════════════════════════════════════
# Persist — write the final state to offer_evaluations
# ══════════════════════════════════════════════════════════════════════════
def _persist(state: OfferEvaluationState, existing_id: str | None = None) -> str:
    """Insert or update an offer_evaluations row from terminal state."""
    from db.client import get_supabase

    sb = get_supabase()

    parsed = state.get("parsed_offer") or {}
    bands = state.get("market_bands") or {}

    row = {
        "user_id": state.get("user_id"),
        "application_id": state.get("application_id"),
        "job_id": state.get("job_id"),
        # Comp components
        "base_salary_usd": parsed.get("base_salary_usd"),
        "target_bonus_usd": parsed.get("target_bonus_usd"),
        "equity_grant_usd": parsed.get("equity_grant_usd"),
        "sign_on_bonus_usd": parsed.get("sign_on_bonus_usd"),
        "other_benefits_usd": parsed.get("other_benefits_usd"),
        "total_comp_year_1": parsed.get("total_comp_year_1"),
        "total_comp_4yr_avg": parsed.get("total_comp_4yr_avg"),
        "currency": (parsed.get("raw_currency") or "usd").upper(),
        # Role descriptors
        "level": parsed.get("level"),
        "title": parsed.get("title"),
        "location": parsed.get("location"),
        "start_date": parsed.get("start_date"),
        # Market research
        "market_p25": bands.get("p25"),
        "market_p50": bands.get("p50"),
        "market_p75": bands.get("p75"),
        "market_p90": bands.get("p90"),
        "market_summary": state.get("market_summary"),
        "market_citations": state.get("market_citations") or [],
        # Negotiation
        "negotiation_anchor": state.get("negotiation_anchor"),
        "negotiation_walkaway": state.get("negotiation_walkaway"),
        "negotiation_script": state.get("negotiation_script"),
        "archetype_strategy": state.get("archetype_strategy"),
        # Risk
        "risk_score": state.get("risk_score"),
        "risk_factors": state.get("risk_factors") or [],
        # Synth
        "recommendation": state.get("recommendation"),
        "confidence": state.get("confidence"),
        "risk_adjusted_total_comp": state.get("risk_adjusted_total_comp"),
        "sleep_on_it_score": state.get("sleep_on_it_score"),
        "rationale_md": state.get("rationale_md"),
        # Telemetry
        "total_cost_usd": state.get("total_cost_usd", 0.0),
        "total_latency_ms": state.get("total_latency_ms", 0),
        "cites": state.get("cites") or [],
    }

    if existing_id:
        sb.table("offer_evaluations").update(row).eq("id", existing_id).execute()
        return existing_id

    # New row — let Postgres mint the UUID via DEFAULT gen_random_uuid().
    inserted = (
        sb.table("offer_evaluations").insert(row).execute().data or []
    )
    if not inserted:
        raise RuntimeError("offer_evaluations insert returned no row")
    return str(inserted[0]["id"])


# ══════════════════════════════════════════════════════════════════════════
# Graph build
# ══════════════════════════════════════════════════════════════════════════
def _build_g8_graph():
    """Build (and cache) the compiled LangGraph for G8."""
    from langgraph.graph import END, START, StateGraph

    if getattr(_build_g8_graph, "_cached", None) is not None:
        return _build_g8_graph._cached  # type: ignore[attr-defined]

    g = StateGraph(OfferEvaluationState)
    g.add_node("offer_parser", offer_parser_node)
    g.add_node("market_analyzer", market_analyzer_node)
    g.add_node("negotiation_strategist", negotiation_strategist_node)
    g.add_node("risk_detector", risk_detector_node)
    g.add_node("synthesizer", synthesizer_node)

    g.add_edge(START, "offer_parser")
    g.add_edge("offer_parser", "market_analyzer")
    g.add_edge("market_analyzer", "negotiation_strategist")
    g.add_edge("negotiation_strategist", "risk_detector")
    g.add_edge("risk_detector", "synthesizer")
    g.add_edge("synthesizer", END)

    compiled = g.compile()
    _build_g8_graph._cached = compiled  # type: ignore[attr-defined]
    return compiled


# ══════════════════════════════════════════════════════════════════════════
# Public entry points
# ══════════════════════════════════════════════════════════════════════════
async def run_g8_for_offer(
    user_id: str,
    application_id: str,
    offer_text: str,
    *,
    force: bool = False,
) -> str:
    """Run the full G8 graph. Returns offer_evaluations.id (UUID str).

    Raises HTTPException(404) if the application isn't found, HTTPException(409)
    if the parent job is closed and force=False.
    """
    # Hydrate (also runs the wallet guard)
    if force:
        # Still hydrate but tolerate stale job — re-invoke without guard.
        hydrated = _hydrate_force(application_id, user_id)
    else:
        hydrated = _hydrate(application_id, user_id)

    job_id_raw = (hydrated["application_row"] or {}).get("job_id")
    initial_state: OfferEvaluationState = {
        "user_id": user_id,
        "application_id": application_id,
        "job_id": int(job_id_raw) if job_id_raw is not None else None,
        "offer_text": offer_text or "",
        "application_row": hydrated["application_row"],
        "job_row": hydrated["job_row"],
        "company_persona": hydrated["company_persona"],
        "company_knowledge": hydrated["company_knowledge"],
        "archetype": None,
        "force": force,
        "cites": [],
        "transcript": [],
        "total_cost_usd": 0.0,
        "total_latency_ms": 0,
    }

    graph = _build_g8_graph()
    final_state = await graph.ainvoke(initial_state)

    evaluation_id = _persist(final_state)
    logger.info(
        "[g8.run_g8_for_offer] persisted id=%s user_id=%s application_id=%s cost=$%.4f latency_ms=%d",
        evaluation_id,
        user_id,
        application_id,
        final_state.get("total_cost_usd", 0.0),
        final_state.get("total_latency_ms", 0),
    )
    return evaluation_id


def _hydrate_force(application_id: str, user_id: str) -> dict[str, Any]:
    """Hydrate variant that skips the wallet guard. Used when force=True."""
    from db.client import get_supabase

    sb = get_supabase()
    app_rows = (
        sb.table("applications")
        .select("*")
        .eq("id", application_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not app_rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "application_not_found", "application_id": application_id},
        )
    app_row = app_rows[0]

    job_id = app_row.get("job_id")
    job_row: dict | None = None
    if job_id is not None:
        job_rows = (
            sb.table("jobs").select("*").eq("id", int(job_id)).limit(1).execute().data
        ) or []
        job_row = job_rows[0] if job_rows else None

    company_name = (job_row or {}).get("company") or app_row.get("company")
    persona_rows: list[dict] = []
    knowledge: list[dict] = []
    if company_name:
        persona_rows = (
            sb.table("company_personas")
            .select("*")
            .eq("user_id", user_id)
            .ilike("company_name", company_name)
            .limit(1)
            .execute()
            .data
        ) or []
        knowledge = (
            sb.table("company_knowledge")
            .select("id, kind, title, content, snippet, published_at, captured_at")
            .eq("user_id", user_id)
            .ilike("company_name", company_name)
            .order("captured_at", desc=True)
            .limit(30)
            .execute()
            .data
        ) or []

    return {
        "application_row": app_row,
        "job_row": job_row,
        "company_persona": persona_rows[0] if persona_rows else None,
        "company_knowledge": knowledge,
    }


async def regenerate_offer_evaluation(
    evaluation_id: str,
    user_id: str,
    *,
    force: bool = False,
) -> str:
    """Re-run G8 for an existing offer_evaluations row.

    Returns the evaluation_id (same as input — idempotent).
    """
    from db.client import get_supabase

    sb = get_supabase()
    rows = (
        sb.table("offer_evaluations")
        .select("application_id, job_id, offer_text_excerpt")
        .eq("id", evaluation_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "evaluation_not_found", "evaluation_id": evaluation_id},
        )
    row = rows[0]
    application_id = row["application_id"]

    # We don't store the raw offer_text, so regenerate from the existing
    # parsed_offer rationale isn't possible. The caller must POST the
    # offer_text again. For this endpoint we re-run with empty text and
    # let offer_parser flag the warning.
    # Future iteration: store offer_text_hash on the row + look up via
    # offer_text_cache table.
    offer_text = ""

    # Hydrate
    hydrated = (
        _hydrate_force(application_id, user_id)
        if force
        else _hydrate(application_id, user_id)
    )

    job_id_raw = (hydrated["application_row"] or {}).get("job_id")
    initial_state: OfferEvaluationState = {
        "user_id": user_id,
        "application_id": application_id,
        "job_id": int(job_id_raw) if job_id_raw is not None else None,
        "offer_text": offer_text,
        "application_row": hydrated["application_row"],
        "job_row": hydrated["job_row"],
        "company_persona": hydrated["company_persona"],
        "company_knowledge": hydrated["company_knowledge"],
        "archetype": None,
        "force": force,
        "cites": [],
        "transcript": [],
        "total_cost_usd": 0.0,
        "total_latency_ms": 0,
    }

    graph = _build_g8_graph()
    final_state = await graph.ainvoke(initial_state)
    _persist(final_state, existing_id=evaluation_id)
    return evaluation_id


def fetch_offer_evaluation(evaluation_id: str, user_id: str) -> dict[str, Any]:
    """Return the full offer_evaluations row, scoped to the user."""
    from db.client import get_supabase

    sb = get_supabase()
    rows = (
        sb.table("offer_evaluations")
        .select("*")
        .eq("id", evaluation_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "evaluation_not_found", "evaluation_id": evaluation_id},
        )
    return rows[0]


def update_offer_decision(
    evaluation_id: str,
    user_id: str,
    *,
    user_decision: str,
    final_total_comp: float | None = None,
) -> dict[str, Any]:
    """Record the human decision on an offer evaluation.

    user_decision must match the schema CHECK constraint values:
      'accepted' | 'negotiated_accepted' | 'negotiated_declined' |
      'declined' | 'expired'.

    Writes user_decision_at = NOW().
    """
    from datetime import datetime, timezone

    from db.client import get_supabase

    VALID = {"accepted", "negotiated_accepted", "negotiated_declined", "declined", "expired"}
    if user_decision not in VALID:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_user_decision",
                "message": f"user_decision must be one of {sorted(VALID)}",
                "got": user_decision,
            },
        )

    sb = get_supabase()
    # Verify ownership first (RLS belt-and-suspenders).
    existing = (
        sb.table("offer_evaluations")
        .select("id")
        .eq("id", evaluation_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not existing:
        raise HTTPException(
            status_code=404,
            detail={"code": "evaluation_not_found", "evaluation_id": evaluation_id},
        )

    patch = {
        "user_decision": user_decision,
        "user_decision_at": datetime.now(timezone.utc).isoformat(),
    }
    if final_total_comp is not None:
        patch["final_total_comp"] = final_total_comp

    sb.table("offer_evaluations").update(patch).eq("id", evaluation_id).execute()
    return {"id": evaluation_id, **patch}
