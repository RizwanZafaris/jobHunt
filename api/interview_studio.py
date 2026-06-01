"""
api/interview_studio.py — Phase 3 router for the Interview Studio.

Mounts under /interview-studio/{application_id}.

Surfaces (matches dashboard/src/lib/types/interview-studio.ts::InterviewStudio):

    GET  /interview-studio/{application_id}                 — full payload
    POST /interview-studio/{application_id}/tutor-chat      — one tutor turn
    POST /interview-studio/{application_id}/log-outcome     — record a round
    POST /interview-studio/{application_id}/build-prep-pack — enqueue G3

Authentication
  Every endpoint depends on get_current_user. In single-user mode this
  resolves to Rizwan's row; in multi-tenant mode it parses the bearer JWT.
  All DB queries filter by user.id so a user only ever sees their own
  application + prep + outcomes.

Cost discipline
  - tutor-chat enforces TUTOR_MAX_COST_USD per turn (capped in
    agents/interview_tutor).
  - log-outcome triggers credit_outcome inline (cheap, pure DB) so the
    user sees "logging this outcome will refine your <company> persona on
    the next Sunday cron" message immediately.

Why no server.py wiring here
  Phase 3 contract: DO NOT modify api/server.py. The wire-up line is
  written to _pending_server_includes_phase3.txt; Rizwan reviews + applies.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agents.interview_tutor import (
    TUTOR_MAX_COST_USD,
    ConceptLevel,
    TutorChatTurn,
    TutorPrepPack,
    TutorState,
    tutor_respond,
)
from api.context import get_current_user
from api.users import User
from db.client import aexecute, get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/interview-studio", tags=["interview-studio"])


# ─── tunables ─────────────────────────────────────────────────────────────
TUTOR_HISTORY_LIMIT = 50
DEFAULT_CONCEPT_LEVEL: ConceptLevel = "basics"


# ─── Pydantic shapes (must mirror dashboard/src/lib/types/interview-studio.ts) ──
class TutorChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    concept_level: Optional[str] = Field(default=None, description="basics|intermediate|advanced")


class TutorChatResponse(BaseModel):
    response: str
    suggested_next_question: Optional[str] = None
    concept_level_recommended: str
    cited_section: Optional[str] = None
    cost_usd: float = 0.0
    cost_capped: bool = False
    user_message_id: Optional[str] = None
    tutor_message_id: Optional[str] = None


class LogOutcomeRequest(BaseModel):
    round_number: int = Field(..., ge=1, le=20)
    round_type: str = Field(..., min_length=1, max_length=40)
    passed: Optional[bool] = None
    questions_asked: list[str] = Field(default_factory=list)
    feedback_text: Optional[str] = None
    interviewer_names: list[str] = Field(default_factory=list)
    conducted_at: Optional[str] = None  # ISO 8601


class LogOutcomeResponse(BaseModel):
    outcome_id: str
    credit_summary: dict
    refines_persona_on_next_cron: bool = True


class BuildPrepPackRequest(BaseModel):
    round_type: str = Field(default="hm", min_length=1, max_length=40)
    round_number: int = Field(default=1, ge=1, le=20)
    max_cost_usd: Optional[float] = Field(default=None, ge=0, le=20)


class BuildPrepPackResponse(BaseModel):
    job_run_id: str
    enqueued: bool = True


# ─── helpers ──────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_concept_level(value: Optional[str], fallback: ConceptLevel = DEFAULT_CONCEPT_LEVEL) -> ConceptLevel:
    if isinstance(value, str) and value in ("basics", "intermediate", "advanced"):
        return value  # type: ignore[return-value]
    return fallback


def _load_application(application_id: UUID, user_id: UUID) -> dict:
    db = get_supabase()
    rows = (
        db.table("applications")
        .select("*")
        .eq("id", str(application_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="application_not_found")
    return rows[0]


def _load_job_for_application(application: dict) -> dict:
    job_id = application.get("job_id")
    if not job_id:
        return {}
    db = get_supabase()
    rows = (
        db.table("jobs")
        .select("id, title, company, location, url, description, status, match_score")
        .eq("id", job_id)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else {}


def _load_latest_interview_prep(application_id: UUID, user_id: UUID) -> Optional[dict]:
    """Most-recent converged prep pack for this application; falls back to any.
    """
    db = get_supabase()
    base = (
        db.table("interview_prep")
        .select(
            "id, application_id, status, round_number, round_type, prep_pack_md, "
            "prep_pack_url, likely_questions, star_stories, company_hooks, "
            "red_flag_questions, salary_negotiation_notes, company_name, "
            "created_at, finalized_at, cost_usd_total"
        )
        .eq("application_id", str(application_id))
        .eq("user_id", str(user_id))
    )
    # Try converged first.
    rows = (
        base.eq("status", "converged")
        .order("finalized_at", desc=True, nullsfirst=False)
        .limit(1)
        .execute()
    ).data or []
    if rows:
        return rows[0]
    rows = (
        base.order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def _load_outcomes(application_id: UUID, user_id: UUID) -> list[dict]:
    db = get_supabase()
    rows = (
        db.table("interview_outcomes")
        .select("*")
        .eq("application_id", str(application_id))
        .eq("user_id", str(user_id))
        .order("round_number", desc=False)
        .execute()
    ).data or []
    return rows


def _load_tutor_history(prep_id: str, user_id: UUID, limit: int = TUTOR_HISTORY_LIMIT) -> list[dict]:
    db = get_supabase()
    rows = (
        db.table("interview_tutor_messages")
        .select("id, role, content, concept_level, cited_section, suggested_next, created_at")
        .eq("interview_prep_id", prep_id)
        .eq("user_id", str(user_id))
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    ).data or []
    return rows


def _persist_tutor_message(
    *,
    user_id: UUID,
    interview_prep_id: str,
    role: str,
    content: str,
    concept_level: Optional[str] = None,
    cited_section: Optional[str] = None,
    suggested_next: Optional[str] = None,
    cost_usd: float = 0.0,
    latency_ms: int = 0,
) -> Optional[dict]:
    db = get_supabase()
    payload: dict[str, Any] = {
        "user_id": str(user_id),
        "interview_prep_id": interview_prep_id,
        "role": role,
        "content": content,
        "concept_level": concept_level,
        "cited_section": cited_section,
        "suggested_next": suggested_next,
        "cost_usd": float(cost_usd or 0),
        "latency_ms": int(latency_ms or 0),
    }
    try:
        result = db.table("interview_tutor_messages").insert(payload).execute()
        return (result.data or [None])[0]
    except Exception as e:
        logger.warning(f"persist tutor message failed: {type(e).__name__}: {e}")
        return None


def _current_concept_level(history: list[dict], default: ConceptLevel = DEFAULT_CONCEPT_LEVEL) -> ConceptLevel:
    """Most recent non-null concept_level on a tutor or user message."""
    for m in reversed(history):
        lvl = m.get("concept_level")
        if isinstance(lvl, str) and lvl in ("basics", "intermediate", "advanced"):
            return lvl  # type: ignore[return-value]
    return default


# ─── GET /interview-studio/{application_id} ───────────────────────────────
@router.get("/{application_id}")
def get_interview_studio(
    application_id: UUID,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Full studio payload — application, job, prep pack, outcomes, chat history."""
    application = _load_application(application_id, user.id)
    job = _load_job_for_application(application)
    prep = _load_latest_interview_prep(application_id, user.id)
    outcomes = _load_outcomes(application_id, user.id)

    has_pack = bool(prep and prep.get("status") == "converged")
    tutor_history: list[dict] = []
    current_level: ConceptLevel = DEFAULT_CONCEPT_LEVEL
    if prep and prep.get("id"):
        tutor_history = _load_tutor_history(str(prep["id"]), user.id)
        current_level = _current_concept_level(tutor_history)

    return {
        "application": {
            "id": application.get("id"),
            "company": application.get("company"),
            "role": application.get("role"),
            "score": application.get("score"),
            "status": application.get("status"),
            "applied_date": application.get("applied_date"),
            "job_id": application.get("job_id"),
            "notes": application.get("notes"),
        },
        "job": {
            "id": job.get("id"),
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "url": job.get("url"),
            "match_score": job.get("match_score"),
            "description": (job.get("description") or "")[:4000] if job else None,
        },
        "interview_prep": {
            "id": (prep or {}).get("id"),
            "has_pack": has_pack,
            "status": (prep or {}).get("status"),
            "round_number": (prep or {}).get("round_number"),
            "round_type": (prep or {}).get("round_type"),
            "prep_pack_md": (prep or {}).get("prep_pack_md"),
            "prep_pack_url": (prep or {}).get("prep_pack_url"),
            "likely_questions": (prep or {}).get("likely_questions") or [],
            "star_stories": (prep or {}).get("star_stories") or [],
            "company_hooks": (prep or {}).get("company_hooks") or [],
            "red_flag_questions": (prep or {}).get("red_flag_questions") or [],
            "salary_negotiation_notes": (prep or {}).get("salary_negotiation_notes"),
            "company_name": (prep or {}).get("company_name") or application.get("company"),
            "created_at": (prep or {}).get("created_at"),
            "finalized_at": (prep or {}).get("finalized_at"),
        },
        "outcomes_logged": outcomes,
        "tutor_chat_history": tutor_history,
        "current_concept_level": current_level,
        "tutor_max_cost_usd": TUTOR_MAX_COST_USD,
        "generated_at": _utcnow().isoformat(),
    }


# ─── POST /interview-studio/{application_id}/tutor-chat ───────────────────
@router.post("/{application_id}/tutor-chat", response_model=TutorChatResponse)
async def post_tutor_chat(
    application_id: UUID,
    body: TutorChatRequest,
    user: User = Depends(get_current_user),
) -> TutorChatResponse:
    """One tutor turn. Persists both messages and returns the tutor's reply."""
    application = _load_application(application_id, user.id)
    prep = _load_latest_interview_prep(application_id, user.id)
    if not prep or not prep.get("id"):
        raise HTTPException(
            status_code=409,
            detail="prep_pack_missing — call /build-prep-pack first or wait for it to finish",
        )
    prep_id = str(prep["id"])

    history_rows = _load_tutor_history(prep_id, user.id)
    concept_level = _coerce_concept_level(
        body.concept_level,
        _current_concept_level(history_rows, DEFAULT_CONCEPT_LEVEL),
    )

    chat_history: list[TutorChatTurn] = [
        {  # type: ignore[misc]
            "role": (h.get("role") or "user"),
            "content": h.get("content") or "",
            "concept_level": h.get("concept_level"),
        }
        for h in history_rows
        if (h.get("content") or "").strip()
    ]

    pack: TutorPrepPack = {
        "company_name": prep.get("company_name") or application.get("company") or "the company",
        "round_type": prep.get("round_type") or "hm",
        "round_number": prep.get("round_number") or 1,
        "prep_pack_md": prep.get("prep_pack_md"),
        "likely_questions": prep.get("likely_questions") or [],
        "star_stories": prep.get("star_stories") or [],
        "company_hooks": prep.get("company_hooks") or [],
        "red_flag_questions": prep.get("red_flag_questions") or [],
        "salary_negotiation_notes": prep.get("salary_negotiation_notes"),
    }
    state: TutorState = {  # type: ignore[typeddict-item]
        "interview_prep": pack,
        "user_message": body.message,
        "concept_level": concept_level,
        "chat_history": chat_history,
        "role_title": application.get("role"),
        "user_name": user.full_name or "the candidate",
    }

    # Persist the user message FIRST so it survives even if the LLM call fails.
    user_row = _persist_tutor_message(
        user_id=user.id,
        interview_prep_id=prep_id,
        role="user",
        content=body.message,
        concept_level=concept_level,
    )

    result = await tutor_respond(state)

    tutor_row = _persist_tutor_message(
        user_id=user.id,
        interview_prep_id=prep_id,
        role="tutor",
        content=result.get("response") or "",
        concept_level=result.get("concept_level_recommended") or concept_level,
        cited_section=result.get("cited_section"),
        suggested_next=result.get("suggested_next_question"),
        cost_usd=float(result.get("cost_usd") or 0),
        latency_ms=int(result.get("latency_ms") or 0),
    )

    return TutorChatResponse(
        response=result.get("response") or "",
        suggested_next_question=result.get("suggested_next_question"),
        concept_level_recommended=str(result.get("concept_level_recommended") or concept_level),
        cited_section=result.get("cited_section"),
        cost_usd=float(result.get("cost_usd") or 0),
        cost_capped=bool(result.get("cost_capped") or False),
        user_message_id=(user_row or {}).get("id"),
        tutor_message_id=(tutor_row or {}).get("id"),
    )


# ─── POST /interview-studio/{application_id}/log-outcome ──────────────────
@router.post("/{application_id}/log-outcome", response_model=LogOutcomeResponse)
async def post_log_outcome(
    application_id: UUID,
    body: LogOutcomeRequest,
    user: User = Depends(get_current_user),
) -> LogOutcomeResponse:
    """Record an interview round outcome and trigger credit_outcome.

    Idempotency: interview_outcomes has UNIQUE(application_id, round_number).
    Re-posting the same round number returns 409 — the UI must explicitly
    delete + re-create or update via a different endpoint (out of scope).

    Async since 2026-05-12: credit_outcome is now `async def` (see
    docs/AGENT_REVIEW_2026_05_11.md §31).
    """
    application = _load_application(application_id, user.id)
    db = get_supabase()
    payload: dict[str, Any] = {
        "user_id": str(user.id),
        "application_id": str(application_id),
        "job_id": application.get("job_id"),
        "company_name": application.get("company"),
        "round_number": int(body.round_number),
        "round_type": body.round_type,
        "passed": body.passed,
        "questions_asked": body.questions_asked or [],
        "feedback_text": body.feedback_text,
        "interviewer_names": body.interviewer_names or [],
        "conducted_at": body.conducted_at or _utcnow().isoformat(),
    }
    try:
        result = await aexecute(db.table("interview_outcomes").insert(payload))
    except Exception as e:
        # Most likely cause: UNIQUE(application_id, round_number) violation.
        msg = str(e)
        if "duplicate key" in msg.lower() or "unique" in msg.lower():
            raise HTTPException(
                status_code=409,
                detail=f"round_already_logged: round_number={body.round_number} exists for this application",
            )
        logger.warning(f"log_outcome insert failed: {type(e).__name__}: {msg[:200]}")
        raise HTTPException(status_code=500, detail="failed_to_log_outcome")

    outcome_row = (result.data or [{}])[0]
    outcome_id = outcome_row.get("id")
    if not outcome_id:
        raise HTTPException(status_code=500, detail="outcome_insert_returned_no_id")

    # Trigger credit_outcome inline. It's pure DB ops — no LLM.
    credit_summary: dict[str, Any] = {}
    try:
        from agents.outcome_to_persona import credit_outcome
        credit_summary = await credit_outcome(str(outcome_id), kind="interview")
    except Exception as e:
        logger.warning(f"credit_outcome failed (non-fatal): {type(e).__name__}: {e}")
        credit_summary = {"error": f"credit_failed: {type(e).__name__}"}

    # Tier 2 G3 §4.2 — outcome attribution to story_bank.
    # The G3 prep pack's `retrieved_stories` field records exactly which
    # stories were surfaced for this interview. When an outcome lands
    # (callback / pass / fail / offer), we credit those stories so the
    # next prep pack's pgvector ranking can re-weight them by past
    # success. Independent from credit_outcome above, which credits
    # company_knowledge rows on the resume side.
    story_credit_summary = await _credit_stories_for_outcome(
        application_id=application_id,
        user_id=user.id,
        passed=body.passed,
    )
    credit_summary["story_bank_credits"] = story_credit_summary

    return LogOutcomeResponse(
        outcome_id=str(outcome_id),
        credit_summary=credit_summary,
        refines_persona_on_next_cron=True,
    )


async def _credit_stories_for_outcome(
    *,
    application_id: UUID,
    user_id: UUID,
    passed: Optional[bool],
) -> dict[str, Any]:
    """Bump outcome_score on every story that was retrieved for this prep.

    Resolution: load the most-recent converged interview_prep for this
    application, read its `retrieved_stories` JSON column, and bump each
    referenced story_bank row by the interview-outcome delta.

    Delta is small (+1 for pass, -1 for fail, 0 for unknown) — we want
    the boost to influence ranking over many runs, not dominate one.
    The story_bank.outcome_score column is a raw count (clamped ≥0 by
    update_outcome_score), distinct from company_knowledge's [0,1]
    score.
    """
    summary: dict[str, Any] = {
        "credited_story_count": 0,
        "delta": 0,
        "story_ids": [],
        "error": None,
    }
    if passed is None:
        summary["error"] = "no_pass_signal_skipped"
        return summary
    delta = 1 if passed else -1
    summary["delta"] = delta

    try:
        db = get_supabase()
        rows = (await aexecute(
            db.table("interview_prep")
            .select("id, retrieved_stories")
            .eq("application_id", str(application_id))
            .eq("user_id", str(user_id))
            .order("finalized_at", desc=True, nullsfirst=False)
            .limit(1)
        )).data or []
    except Exception as e:
        summary["error"] = f"prep_lookup_failed: {type(e).__name__}"
        return summary

    if not rows:
        summary["error"] = "no_prep_to_credit"
        return summary
    retrieved = (rows[0] or {}).get("retrieved_stories") or {}
    if not isinstance(retrieved, dict):
        summary["error"] = "retrieved_stories_not_dict"
        return summary

    story_ids: list[str] = []
    for _idx, rm in retrieved.items():
        if not isinstance(rm, dict):
            continue
        sid = (rm.get("story_id") or (rm.get("story") or {}).get("id") or "").strip()
        if sid and sid not in story_ids:
            story_ids.append(sid)

    try:
        from agents.story_bank_agent import update_outcome_score
        for sid in story_ids:
            await update_outcome_score(sid, delta)
    except Exception as e:
        summary["error"] = f"credit_write_failed: {type(e).__name__}"
        return summary

    summary["credited_story_count"] = len(story_ids)
    summary["story_ids"] = story_ids
    return summary


# ─── POST /interview-studio/{application_id}/build-prep-pack ──────────────
@router.post("/{application_id}/build-prep-pack", response_model=BuildPrepPackResponse)
def post_build_prep_pack(
    application_id: UUID,
    body: BuildPrepPackRequest,
    user: User = Depends(get_current_user),
) -> BuildPrepPackResponse:
    """Enqueue a G3 prep-pack build. Idempotent via the queue's dedup key."""
    _ = _load_application(application_id, user.id)  # 404 if not found / not yours.
    try:
        from api.queue import enqueue_g3_interview_prep
        job_run_id = enqueue_g3_interview_prep(
            user_id=user.id,
            application_id=application_id,
            round_type=body.round_type,
            round_number=body.round_number,
            max_cost_usd=body.max_cost_usd,
        )
    except Exception as e:
        logger.warning(f"enqueue_g3 failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"enqueue_failed: {type(e).__name__}")
    return BuildPrepPackResponse(job_run_id=str(job_run_id), enqueued=True)


__all__ = ["router"]
