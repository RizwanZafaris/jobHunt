"""
api/g7.py — Tier 3 G7 Application Graph endpoints.

Routes (prefixed with /workspace):
    POST   /workspace/{job_id}/apply
                                      → Kick off G7. Returns
                                        application_answer_id + poll url.
    GET    /workspace/applications/{application_answer_id}
                                      → Fetch state for dashboard polling.
    PATCH  /workspace/applications/{application_answer_id}/questions/{idx}
                                      → Edit one answer pre-approval.
    POST   /workspace/applications/{application_answer_id}/approve
                                      → Resume the graph past HITL.
    POST   /workspace/applications/{application_answer_id}/regenerate/{idx}
                                      → Re-run answer_generator for one Q.
    POST   /workspace/applications/{application_answer_id}/cancel
                                      → Kill the run.

HITL flow (concrete):
    /apply  →  graph runs form_scanner → classifier → retriever →
              generator/critic → merge → (interrupt) → returns to caller.
              At this point status='awaiting_review'; dashboard polls.
    User edits per-field via PATCH or clicks Regenerate per Q.
    /approve  → resume_g7_run() calls graph.invoke(Command(resume=approvals))
              which lands inside human_review_node, then runs form_filler →
              persist → END. Browser opens (headless=False) for the user to
              eyeball + click Submit.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from agents.g7_io import (
    cancel_g7_run,
    fetch_application_answers,
    regenerate_one,
    resume_g7_run,
    start_g7_run,
    update_question,
)
from api._job_guards import load_open_job
from api.context import get_current_user
from api.users import User
from db.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["applications-g7"])


# ── Request / response models ──────────────────────────────────────────
class ApplyRequest(BaseModel):
    form_url: str = Field(..., min_length=12)
    ats_type: str = Field(default="greenhouse")
    force: bool = Field(default=False, description="Override wallet guard for closed jobs.")


class ApplyResponse(BaseModel):
    application_answer_id: str
    status: str
    questions: list[dict[str, Any]] = Field(default_factory=list)
    classification_summary: dict[str, Any] = Field(default_factory=dict)
    persona_critique_summary: dict[str, Any] = Field(default_factory=dict)
    cost_usd_total: float = 0.0
    poll_url: str


class QuestionEdit(BaseModel):
    user_edited: Optional[str] = None
    approved: Optional[bool] = None


class ApproveRequest(BaseModel):
    approvals: list[dict[str, Any]] = Field(
        ...,
        description="[{idx, approved: bool, user_edited?: str}, ...]",
    )


# ── Helpers ────────────────────────────────────────────────────────────
def _resolve_app_id_for_job(*, job_id: int, user_id: UUID) -> int:
    """Find the application row matching this job_id for this user. We
    require one to exist BEFORE /apply runs — the user should have hit
    "Mark as planning" first. Raises 404 otherwise."""
    db = get_supabase()
    rows = (
        db.table("applications")
        .select("id")
        .eq("user_id", str(user_id))
        .eq("job_id", job_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No application row for this job — mark it as planning first.",
        )
    return int(rows[0]["id"])


# ══════════════════════════════════════════════════════════════════════════
# POST /workspace/{job_id}/apply
# ══════════════════════════════════════════════════════════════════════════
@router.post("/{job_id}/apply", response_model=ApplyResponse)
async def apply(
    body: ApplyRequest,
    job_id: int = Path(..., ge=1),
    user: User = Depends(get_current_user),
) -> ApplyResponse:
    """Kick off G7 for the application that matches this job.

    Returns the application_answer_id + a poll_url. The graph runs to
    the HITL interrupt synchronously (typical wall-clock: 8-15 seconds
    for scan + classify + retrieve + generate + critic). When the
    function returns, status='awaiting_review' and the dashboard fetches
    the row for per-field approval.
    """
    # Wallet guard FIRST so we 409 before incurring any LLM spend.
    db = get_supabase()
    try:
        job = load_open_job(
            db, job_id=job_id, user_id=user.id,
            force=body.force, cost_label="G7 form-fill (~$0.30)",
        )
    except HTTPException:
        raise

    application_id = _resolve_app_id_for_job(job_id=job_id, user_id=user.id)

    try:
        result = await start_g7_run(
            application_id=application_id,
            form_url=body.form_url,
            user_id=str(user.id),
            ats_type=body.ats_type,
            force=body.force,
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.exception("G7 /apply failed for job_id=%s", job_id)
        raise HTTPException(status_code=500, detail=f"g7_failed:{type(e).__name__}")

    aaid = result["application_answer_id"]
    return ApplyResponse(
        application_answer_id=aaid,
        status=result.get("status") or "awaiting_review",
        questions=result.get("questions") or [],
        classification_summary=result.get("classification_summary") or {},
        persona_critique_summary=result.get("persona_critique_summary") or {},
        cost_usd_total=float(result.get("cost_usd_total") or 0.0),
        poll_url=f"/workspace/applications/{aaid}",
    )


# ══════════════════════════════════════════════════════════════════════════
# GET /workspace/applications/{application_answer_id}
# ══════════════════════════════════════════════════════════════════════════
@router.get("/applications/{application_answer_id}")
def get_application_answers(
    application_answer_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch the current state of an in-flight or completed G7 run."""
    row = fetch_application_answers(application_answer_id, user_id=str(user.id))
    if not row:
        raise HTTPException(status_code=404, detail="application_answer_not_found")
    return row


# ══════════════════════════════════════════════════════════════════════════
# PATCH /workspace/applications/{id}/questions/{idx}
# ══════════════════════════════════════════════════════════════════════════
@router.patch("/applications/{application_answer_id}/questions/{idx}")
def edit_question(
    body: QuestionEdit,
    application_answer_id: str,
    idx: int = Path(..., ge=0),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit ONE question's draft or approval flag.

    Used by the dashboard's per-field edit UI. Doesn't touch the graph
    state — just updates the JSONB array in place.
    """
    try:
        row = update_question(
            application_answer_id=application_answer_id,
            user_id=str(user.id),
            idx=idx,
            user_edited=body.user_edited,
            approved=body.approved,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return row


# ══════════════════════════════════════════════════════════════════════════
# POST /workspace/applications/{id}/approve
# ══════════════════════════════════════════════════════════════════════════
@router.post("/applications/{application_answer_id}/approve")
async def approve(
    body: ApproveRequest,
    application_answer_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Resume the graph past the HITL interrupt.

    The `approvals` payload feeds back to interrupt() inside
    human_review_node, which then merges them into questions[i].approved
    / questions[i].user_edited, after which the graph runs form_filler
    + persist to completion.
    """
    if not body.approvals:
        raise HTTPException(status_code=400, detail="approvals_required")
    try:
        result = await resume_g7_run(
            application_answer_id=application_answer_id,
            approvals=body.approvals,
            user_id=str(user.id),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.exception("G7 /approve failed")
        raise HTTPException(status_code=500, detail=f"g7_resume_failed:{type(e).__name__}")
    return result


# ══════════════════════════════════════════════════════════════════════════
# POST /workspace/applications/{id}/regenerate/{idx}
# ══════════════════════════════════════════════════════════════════════════
@router.post("/applications/{application_answer_id}/regenerate/{idx}")
async def regenerate(
    application_answer_id: str,
    idx: int = Path(..., ge=0),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-run answer_generator + persona_critic for a single question."""
    try:
        result = await regenerate_one(
            application_answer_id=application_answer_id,
            user_id=str(user.id),
            idx=idx,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


# ══════════════════════════════════════════════════════════════════════════
# POST /workspace/applications/{id}/cancel
# ══════════════════════════════════════════════════════════════════════════
@router.post("/applications/{application_answer_id}/cancel")
async def cancel(
    application_answer_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark the run as cancelled. Does NOT terminate Playwright — the
    form_filler node is wrapped in its own try/except and won't run
    after the row is cancelled because resume_g7_run checks status."""
    try:
        row = await cancel_g7_run(
            application_answer_id=application_answer_id,
            user_id=str(user.id),
        )
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return row
