"""
api/follow_ups.py — Phase 1.3 endpoints for the G6 Follow-up Graph.

The user's follow-up queue is exposed through five routes:

    GET    /workspace/follow-ups             — list URGENT + OVERDUE for user
    GET    /workspace/follow-ups/{id}        — single follow-up detail
    POST   /workspace/follow-ups/{id}/approve     — record approval, advance cadence
    POST   /workspace/follow-ups/{id}/skip        — mark COLD; close app if needed
    POST   /workspace/follow-ups/{id}/regenerate  — re-run G6 with optional bias

HITL discipline: NONE of these endpoints send email. "Approve" records the
user's intent and updates the cadence row. The user copy-pastes from
draft_email into their own client. Sending via Gmail/SendGrid is OUT OF
SCOPE for this phase (separate ship, requires OAuth + threading work).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agents.g6_cadence import CADENCE_RULES, compute_next_follow_up_date
from agents.g6_io import (
    close_application_cold,
    run_g6_for_application,
    update_cadence_row,
)
from api.context import get_current_user
from api.users import User
from db.client import get_supabase

logger = logging.getLogger(__name__)

# B2: was "/workspace/follow-ups" — collided with /workspace/{job_id} (int)
# because workspace_router is included first in server.py. Same fix as stories.
router = APIRouter(prefix="/follow-ups", tags=["follow-ups"])


# ── Helpers ───────────────────────────────────────────────────────────────
def _get_cadence_row(cadence_id: str, user_id: UUID) -> dict:
    db = get_supabase()
    rows = (
        db.table("follow_up_cadence")
        .select("*")
        .eq("id", cadence_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise HTTPException(status_code=404, detail="follow_up_not_found")
    return rows[0]


def _get_application_summary(application_id: int, user_id: UUID) -> Optional[dict]:
    db = get_supabase()
    rows = (
        db.table("applications")
        .select("id, company, role, status, applied_date, job_id")
        .eq("id", application_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def _shape_card(cadence: dict, application: Optional[dict]) -> dict:
    """Build the UI card payload — used by both the list and detail endpoints."""
    return {
        "id": cadence["id"],
        "application_id": cadence["application_id"],
        "company": (application or {}).get("company"),
        "role": (application or {}).get("role"),
        "status": cadence.get("current_status"),
        "urgency": cadence.get("urgency"),
        "follow_up_count": cadence.get("follow_up_count"),
        "next_follow_up_date": cadence.get("next_follow_up_date"),
        "last_contact_date": cadence.get("last_contact_date"),
        "draft_email": cadence.get("draft_email"),
        "draft_cites": cadence.get("draft_cites") or [],
        "draft_persona_critique": cadence.get("draft_persona_critique") or {},
        "cost_usd": float(cadence.get("cost_usd") or 0.0),
        "created_at": cadence.get("created_at"),
        "outcome": cadence.get("outcome"),
        "job_id": (application or {}).get("job_id"),
    }


# ══════════════════════════════════════════════════════════════════════════
# GET /workspace/follow-ups
# ══════════════════════════════════════════════════════════════════════════
@router.get("")
def list_follow_ups(
    urgency: Optional[str] = Query(
        default=None,
        description="Filter by urgency: URGENT, OVERDUE, waiting, COLD. "
                    "Default: URGENT + OVERDUE only.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the user's follow-up queue.

    Default: only URGENT + OVERDUE rows are returned, because that's the
    user's actionable surface. Pass ?urgency=all to include waiting+COLD.

    Important: we return the LATEST cadence row per application, not every
    row. The follow_up_cadence table accumulates a row per draft attempt;
    older rows are kept for audit but they aren't actionable.
    """
    db = get_supabase()
    target_urgencies: list[str]
    if urgency is None:
        target_urgencies = ["URGENT", "OVERDUE"]
    elif urgency.lower() == "all":
        target_urgencies = ["URGENT", "OVERDUE", "waiting", "COLD"]
    else:
        target_urgencies = [urgency]

    # Pull the candidate rows, then dedupe to the latest per application_id
    # client-side. PostgREST doesn't expose DISTINCT ON; we cap the limit
    # generously so dedup overhead stays trivial.
    rows = (
        db.table("follow_up_cadence")
        .select("*")
        .eq("user_id", str(user.id))
        .in_("urgency", target_urgencies)
        .order("created_at", desc=True)
        .limit(limit * 3)
        .execute()
        .data
    ) or []

    seen: set[int] = set()
    latest: list[dict] = []
    for r in rows:
        aid = r.get("application_id")
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        latest.append(r)
        if len(latest) >= limit:
            break

    application_ids = [r["application_id"] for r in latest]
    apps_by_id: dict[int, dict] = {}
    if application_ids:
        app_rows = (
            db.table("applications")
            .select("id, company, role, status, applied_date, job_id")
            .in_("id", application_ids)
            .eq("user_id", str(user.id))
            .execute()
            .data
        ) or []
        apps_by_id = {a["id"]: a for a in app_rows}

    cards = [_shape_card(r, apps_by_id.get(r["application_id"])) for r in latest]

    counts = {"URGENT": 0, "OVERDUE": 0, "waiting": 0, "COLD": 0}
    for c in cards:
        u = c.get("urgency")
        if u in counts:
            counts[u] += 1

    return {
        "follow_ups": cards,
        "total": len(cards),
        "counts": counts,
        "filter": target_urgencies,
    }


# ══════════════════════════════════════════════════════════════════════════
# GET /workspace/follow-ups/{id}
# ══════════════════════════════════════════════════════════════════════════
@router.get("/{cadence_id}")
def get_follow_up_detail(
    cadence_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    cadence = _get_cadence_row(cadence_id, user.id)
    app = _get_application_summary(cadence["application_id"], user.id)
    return _shape_card(cadence, app)


# ══════════════════════════════════════════════════════════════════════════
# POST /workspace/follow-ups/{id}/approve
# ══════════════════════════════════════════════════════════════════════════
class ApproveBody(BaseModel):
    """Optional fields for the approve endpoint. Empty body is fine."""
    notes: Optional[str] = Field(default=None, description="Free-form notes from the user")


@router.post("/{cadence_id}/approve")
def approve_follow_up(
    cadence_id: str,
    body: ApproveBody = ApproveBody(),    # noqa: B008
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """User approved the draft.

    Does NOT send the email. Sending is OUT OF SCOPE for this phase. The
    user copies draft_email from the card, pastes into their own client,
    and clicks Approve to record that the message went out.

    Side effects:
      - last_contact_date := NOW()
      - follow_up_count += 1
      - next_follow_up_date := NOW() + rule.follow_up_after_days
      - urgency := 'waiting'  (the next cron pass will re-evaluate)
    """
    cadence = _get_cadence_row(cadence_id, user.id)
    if not cadence.get("draft_email"):
        raise HTTPException(
            status_code=400,
            detail="no_draft_to_approve",
        )

    now = datetime.now(timezone.utc)
    new_count = int(cadence.get("follow_up_count") or 0) + 1
    next_fu = compute_next_follow_up_date(status=cadence.get("current_status"))

    updated = update_cadence_row(
        cadence_id=cadence_id,
        follow_up_count=new_count,
        urgency="waiting",
        last_contact_date=now,
        next_follow_up_date=next_fu,
    )

    return {
        "ok": True,
        "approved_at": now.isoformat(),
        "follow_up_count": new_count,
        "next_follow_up_date": next_fu.isoformat() if next_fu else None,
        "cadence": updated,
    }


# ══════════════════════════════════════════════════════════════════════════
# POST /workspace/follow-ups/{id}/skip
# ══════════════════════════════════════════════════════════════════════════
class SkipBody(BaseModel):
    """Optional reason for the user's skip. Captured for outcome learning."""
    reason: Optional[str] = Field(default=None)
    close_application: bool = Field(
        default=False,
        description="If true, also transitions applications.status to "
                    "'withdrawn' — the terminal post-apply state in the "
                    "application_status enum. Spec calls this 'closed_cold'.",
    )


@router.post("/{cadence_id}/skip")
def skip_follow_up(
    cadence_id: str,
    body: SkipBody = SkipBody(),    # noqa: B008
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """User skipped the follow-up — mark the cadence row COLD.

    When `close_application=True` (or the rules engine says we've hit max
    follow-ups for this status), also transitions the application itself
    to 'withdrawn'.
    """
    cadence = _get_cadence_row(cadence_id, user.id)

    # Auto-close logic: if we've hit max follow-ups for this status, the
    # client doesn't need to ask — closure is the correct action.
    status = cadence.get("current_status")
    rule = CADENCE_RULES.get(status)
    follow_up_count = int(cadence.get("follow_up_count") or 0)
    should_close = body.close_application or (
        rule is not None
        and rule.max_follow_ups > 0
        and follow_up_count >= rule.max_follow_ups
    )

    updated = update_cadence_row(
        cadence_id=cadence_id,
        urgency="COLD",
    )

    closed = False
    if should_close:
        try:
            close_application_cold(cadence["application_id"], str(user.id))
            closed = True
        except Exception:
            logger.exception(
                f"skip_follow_up: close_application_cold failed for "
                f"application {cadence.get('application_id')}"
            )

    return {
        "ok": True,
        "closed_application": closed,
        "cadence": updated,
        "reason_recorded": body.reason,
    }


# ══════════════════════════════════════════════════════════════════════════
# POST /workspace/follow-ups/{id}/regenerate
# ══════════════════════════════════════════════════════════════════════════
@router.post("/{cadence_id}/regenerate")
async def regenerate_follow_up(
    cadence_id: str,
    force_news_signal: bool = Query(
        default=False,
        description="When true, bias the G6 RAG fetch towards fresh news "
                    "even if the personal-win pool is fuller.",
    ),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Re-run G6 for the application backing this cadence row.

    Useful when the user thinks the draft missed the strongest available
    signal. We don't update the existing row; G6 inserts a fresh row. The
    /list endpoint returns the latest row per application so the new draft
    naturally replaces the old one in the UI.
    """
    cadence = _get_cadence_row(cadence_id, user.id)
    result = await run_g6_for_application(
        application_id=int(cadence["application_id"]),
        user_id=str(user.id),
        force_news_signal=force_news_signal,
    )
    return {
        "ok": True,
        "regenerated": True,
        "new_cadence_row_id": result.get("cadence_row_id"),
        "urgency": result.get("urgency"),
        "draft_email": result.get("draft_email"),
        "draft_cites": result.get("draft_cites"),
        "cost_usd_total": result.get("cost_usd_total"),
        "short_circuit": result.get("short_circuit"),
    }
