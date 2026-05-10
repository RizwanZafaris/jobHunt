"""
api/linkedin.py — FastAPI router for the LinkedIn presence engine (P1.2).

Owns every endpoint under /linkedin/*. The dashboard tab /linkedin calls
these. Wire-up is documented in api/LINKEDIN.md — a single line in
api/server.py (`app.include_router(linkedin_router)`) is the integration.

Auth: every handler depends on `get_current_user` (api/context.py),
which falls back to single-user mode (Rizwan) when
RIZWAN_SINGLE_USER_MODE=1 — same pattern as every other multi-tenant
endpoint shipped this week.

V1 publish model:
- The engine NEVER auto-posts. Drafts go through a user-approval gate.
- "Approve & Schedule" sets status='approved' and scheduled_for = next slot.
- "Copy to clipboard" records manual_copy_at = now() (this is the actual
  publish — the user pastes into LinkedIn manually).
- A future Career-tier integration will add Buffer/Hypefury, but the
  default path stays manual. See api/LINKEDIN.md §"Risk stance" for why.

Endpoints:
  POST   /linkedin/drafts/generate
  GET    /linkedin/drafts
  GET    /linkedin/drafts/{id}
  PATCH  /linkedin/drafts/{id}
  POST   /linkedin/drafts/{id}/approve
  POST   /linkedin/drafts/{id}/copy
  POST   /linkedin/drafts/{id}/reject
  GET    /linkedin/voice-profile
  PUT    /linkedin/voice-profile
  GET    /linkedin/posting-schedule
  PUT    /linkedin/posting-schedule
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from api.context import get_current_user
from api.users import User
from db.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/linkedin", tags=["linkedin"])


# ═════════════════════════════════════════════════════════════════════════
# Pydantic request/response models
# ═════════════════════════════════════════════════════════════════════════
ANGLE_VALUES = (
    "news_commentary", "contrarian_take", "build_in_public",
    "lesson_learned", "industry_analysis",
)
STATUS_VALUES = ("draft", "approved", "scheduled", "posted", "rejected", "expired")


class GenerateBody(BaseModel):
    """Body for POST /linkedin/drafts/generate."""
    count: int = Field(default=1, ge=1, le=5)
    angle: Optional[str] = None
    target_company_id: Optional[UUID] = None

    @field_validator("angle")
    @classmethod
    def _angle_in_range(cls, v: Optional[str]) -> Optional[str]:
        if v and v not in ANGLE_VALUES:
            raise ValueError(f"angle must be one of {ANGLE_VALUES}")
        return v


class GenerateResponse(BaseModel):
    queued: int
    run_ids: list[str]


class DraftPatchBody(BaseModel):
    """Body for PATCH /linkedin/drafts/{id} — edit before approving."""
    hook: Optional[str] = None
    body: Optional[str] = None
    cta: Optional[str] = None
    hashtags: Optional[list[str]] = None


class ApproveBody(BaseModel):
    """Body for POST /linkedin/drafts/{id}/approve.

    `scheduled_for` is optional; default is "next available slot from
    your posting_schedule".
    """
    scheduled_for: Optional[datetime] = None


class VoiceProfileBody(BaseModel):
    """Body for PUT /linkedin/voice-profile."""
    profile_md: Optional[str] = None
    tone_directives: Optional[str] = None
    avoid_phrases: Optional[list[str]] = None
    example_posts: Optional[list[str]] = None


class ScheduleSlotBody(BaseModel):
    """One slot in the posting-schedule PUT body."""
    day_of_week: int = Field(ge=0, le=6)
    time_of_day: str = Field(default="09:00:00",
                             pattern=r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")


class PutScheduleBody(BaseModel):
    """Body for PUT /linkedin/posting-schedule.

    Replaces the user's entire schedule with the supplied slots.
    `posts_per_week` is denormalised onto each row from the row count.
    """
    slots: list[ScheduleSlotBody] = Field(min_length=0, max_length=7)
    pause_until: Optional[datetime] = None
    paused_reason: Optional[str] = None


# ═════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════
def _draft_row(draft_id: UUID, user_id: UUID) -> dict:
    """Fetch one draft, raising 404 if not found or not owned by user."""
    db = get_supabase()
    rs = (
        db.table("linkedin_drafts")
        .select("*")
        .eq("id", str(draft_id))
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    )
    rows = rs.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="draft_not_found")
    return rows[0]


def _next_scheduled_slot(user_id: UUID, after: Optional[datetime] = None) -> Optional[datetime]:
    """Compute the next posting slot from the user's schedule.

    Returns the first datetime in the user's schedule >= `after` (default
    now()). If the user has paused, advances past the pause_until window.
    Returns None if the user has no schedule rows.
    """
    db = get_supabase()
    rs = (
        db.table("linkedin_posting_schedule")
        .select("day_of_week, time_of_day, pause_until")
        .eq("user_id", str(user_id))
        .execute()
    )
    rows = rs.data or []
    if not rows:
        return None

    now = after or datetime.now(timezone.utc)
    pause_until: Optional[datetime] = None
    slots: list[tuple[int, time]] = []
    for r in rows:
        dow = int(r["day_of_week"])
        # supabase-py returns time as "09:00:00" string
        tod_raw = r["time_of_day"]
        if isinstance(tod_raw, str):
            parts = tod_raw.split(":")
            tod = time(int(parts[0]), int(parts[1]),
                       int(parts[2]) if len(parts) > 2 else 0)
        else:
            tod = tod_raw
        slots.append((dow, tod))
        if r.get("pause_until"):
            pu = r["pause_until"]
            if isinstance(pu, str):
                pu = datetime.fromisoformat(pu.replace("Z", "+00:00"))
            if pause_until is None or pu > pause_until:
                pause_until = pu

    if pause_until and pause_until > now:
        now = pause_until

    # Walk forward up to 14 days looking for the next matching slot.
    for delta in range(0, 15):
        candidate_date = (now + timedelta(days=delta)).date()
        # Python: Monday=0..Sunday=6. Our schema: Sunday=0..Saturday=6.
        # Convert: dow_schema = (weekday + 1) % 7
        dow_schema = (candidate_date.weekday() + 1) % 7
        for slot_dow, slot_time in slots:
            if slot_dow != dow_schema:
                continue
            cand = datetime.combine(candidate_date, slot_time, tzinfo=timezone.utc)
            if cand >= now:
                return cand
    return None


# ═════════════════════════════════════════════════════════════════════════
# Drafts — generate
# ═════════════════════════════════════════════════════════════════════════
@router.post("/drafts/generate", response_model=GenerateResponse, status_code=202)
async def generate_drafts(
    body: GenerateBody,
    user: User = Depends(get_current_user),
) -> GenerateResponse:
    """Enqueue N draft-generation runs for the current user.

    Each run is a separate G4 graph invocation — different runs may
    pick different angles / different anchor news. Idempotency is
    handled by api/queue.py via the (user_id, kind, payload) hash.

    Returns 202 with the list of jobs_runs ids; the dashboard polls
    GET /jobs-runs/{id} to know when each draft lands.
    """
    try:
        from api.queue import enqueue_g4_linkedin_post
    except ImportError as e:
        # Defensive: until the queue addition lands, surface a clear error.
        # See _pending_queue_additions.md for the patch.
        raise HTTPException(
            status_code=503,
            detail="enqueue_g4_linkedin_post not available — see _pending_queue_additions.md",
        ) from e

    run_ids: list[str] = []
    for i in range(body.count):
        run_id = enqueue_g4_linkedin_post(
            user_id=user.id,
            count=1,
            angle=body.angle,
            target_company_id=str(body.target_company_id) if body.target_company_id else None,
            # nonce ensures count>1 produces distinct idempotency keys
            nonce=i,
        )
        run_ids.append(run_id)

    return GenerateResponse(queued=len(run_ids), run_ids=run_ids)


# ═════════════════════════════════════════════════════════════════════════
# Drafts — list / detail / patch / lifecycle
# ═════════════════════════════════════════════════════════════════════════
@router.get("/drafts")
async def list_drafts(
    status: Optional[Literal[
        "draft", "approved", "scheduled", "posted", "rejected", "expired"
    ]] = Query(None, description="Filter by draft status."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List the user's drafts, newest first. Filterable by status."""
    db = get_supabase()
    q = (
        db.table("linkedin_drafts")
        .select("*", count="exact")
        .eq("user_id", str(user.id))
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
    )
    if status is not None:
        q = q.eq("status", status)
    result = q.execute()
    return {
        "items": result.data or [],
        "total": getattr(result, "count", None) or len(result.data or []),
        "limit": limit,
        "offset": offset,
    }


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: UUID,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return one draft by id (must be owned by the current user)."""
    return _draft_row(draft_id, user.id)


@router.patch("/drafts/{draft_id}")
async def patch_draft(
    draft_id: UUID,
    body: DraftPatchBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit a draft's hook/body/cta/hashtags. Only valid in 'draft' status."""
    cur = _draft_row(draft_id, user.id)
    if cur["status"] not in ("draft", "approved", "scheduled"):
        raise HTTPException(status_code=409,
                            detail=f"cannot edit draft in status={cur['status']}")

    update: dict[str, Any] = {}
    if body.hook is not None:
        update["hook"] = body.hook
    if body.body is not None:
        update["body"] = body.body
    if body.cta is not None:
        update["cta"] = body.cta
    if body.hashtags is not None:
        update["hashtags"] = [h.strip() for h in body.hashtags if h.strip()][:8]
    if not update:
        return cur

    db = get_supabase()
    rs = (
        db.table("linkedin_drafts")
        .update(update)
        .eq("id", str(draft_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    return (rs.data or [cur])[0]


@router.post("/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: UUID,
    body: ApproveBody = ApproveBody(),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Approve a draft and put it on the schedule.

    `scheduled_for` defaults to the next slot from the user's posting_schedule.
    Status moves draft → approved → scheduled (the latter is a derived
    badge for any approved row whose scheduled_for is in the future).
    """
    cur = _draft_row(draft_id, user.id)
    if cur["status"] not in ("draft", "rejected"):
        raise HTTPException(status_code=409,
                            detail=f"cannot approve draft in status={cur['status']}")

    sched = body.scheduled_for or _next_scheduled_slot(user.id)
    if sched is None:
        raise HTTPException(
            status_code=400,
            detail="no_posting_schedule — set one via PUT /linkedin/posting-schedule",
        )

    new_status = "scheduled" if sched > datetime.now(timezone.utc) else "approved"
    db = get_supabase()
    rs = (
        db.table("linkedin_drafts")
        .update({"status": new_status, "scheduled_for": sched.isoformat()})
        .eq("id", str(draft_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    return (rs.data or [cur])[0]


@router.post("/drafts/{draft_id}/copy")
async def copy_draft(
    draft_id: UUID,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark the draft as manually-copied. The actual clipboard write happens
    client-side; this records that the user took the publish action.

    Side-effect: posts whose scheduled_for is past and that get a copy
    event also flip status='posted' so the dashboard reflects reality.
    """
    cur = _draft_row(draft_id, user.id)
    now = datetime.now(timezone.utc)

    update: dict[str, Any] = {"manual_copy_at": now.isoformat()}
    # If the user copy-pastes within the scheduled window, flip to posted.
    if cur["status"] in ("approved", "scheduled"):
        update["status"] = "posted"
        update["posted_at"] = now.isoformat()

    db = get_supabase()
    rs = (
        db.table("linkedin_drafts")
        .update(update)
        .eq("id", str(draft_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    return (rs.data or [cur])[0]


@router.post("/drafts/{draft_id}/reject")
async def reject_draft(
    draft_id: UUID,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Mark the draft as rejected. Kept for analytics; not deleted."""
    cur = _draft_row(draft_id, user.id)
    if cur["status"] in ("posted",):
        raise HTTPException(status_code=409,
                            detail="cannot reject a posted draft")
    db = get_supabase()
    rs = (
        db.table("linkedin_drafts")
        .update({"status": "rejected"})
        .eq("id", str(draft_id))
        .eq("user_id", str(user.id))
        .execute()
    )
    return (rs.data or [cur])[0]


# ═════════════════════════════════════════════════════════════════════════
# Voice profile
# ═════════════════════════════════════════════════════════════════════════
@router.get("/voice-profile")
async def get_voice_profile(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the user's voice profile, or 404 if not yet seeded.

    Onboarding clients should call POST to the voice extractor (CLI or
    dedicated endpoint) when they get a 404 here. Until then we fall back
    to defaults inside the G4 graph.
    """
    db = get_supabase()
    rs = (
        db.table("linkedin_voice_profile")
        .select("*")
        .eq("user_id", str(user.id))
        .limit(1)
        .execute()
    )
    rows = rs.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="voice_profile_not_found")
    return rows[0]


@router.put("/voice-profile")
async def put_voice_profile(
    body: VoiceProfileBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Upsert the user's voice profile."""
    payload: dict[str, Any] = {"user_id": str(user.id)}
    if body.profile_md is not None:
        payload["profile_md"] = body.profile_md
    if body.tone_directives is not None:
        payload["tone_directives"] = body.tone_directives
    if body.avoid_phrases is not None:
        payload["avoid_phrases"] = [
            p.strip() for p in body.avoid_phrases if isinstance(p, str) and p.strip()
        ]
    if body.example_posts is not None:
        payload["example_posts"] = [
            p for p in body.example_posts if isinstance(p, str)
        ][:5]

    db = get_supabase()
    rs = (
        db.table("linkedin_voice_profile")
        .upsert(payload, on_conflict="user_id")
        .execute()
    )
    rows = rs.data or []
    if not rows:
        # Some postgrest versions don't echo on upsert — re-read.
        existing = (
            db.table("linkedin_voice_profile")
            .select("*").eq("user_id", str(user.id)).limit(1).execute()
        )
        rows = existing.data or []
    if not rows:
        raise HTTPException(status_code=500, detail="upsert_failed")
    return rows[0]


# ═════════════════════════════════════════════════════════════════════════
# Posting schedule
# ═════════════════════════════════════════════════════════════════════════
@router.get("/posting-schedule")
async def get_posting_schedule(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the user's full posting schedule (one row per slot)."""
    db = get_supabase()
    rs = (
        db.table("linkedin_posting_schedule")
        .select("*")
        .eq("user_id", str(user.id))
        .order("day_of_week")
        .execute()
    )
    rows = rs.data or []
    return {
        "slots": rows,
        "posts_per_week": rows[0]["posts_per_week"] if rows else 0,
        "next_slot": _serialize_dt(_next_scheduled_slot(user.id)),
    }


@router.put("/posting-schedule")
async def put_posting_schedule(
    body: PutScheduleBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Replace the user's posting schedule with the supplied slots.

    Strategy: delete-all then insert-all in one DB round-trip pair.
    posts_per_week is derived from len(slots). A slot's pause_until +
    paused_reason are denormalised to every row so the GET response can
    surface them without a join.
    """
    db = get_supabase()
    db.table("linkedin_posting_schedule").delete().eq(
        "user_id", str(user.id)
    ).execute()

    posts_per_week = max(0, min(7, len(body.slots)))
    pu = body.pause_until.isoformat() if body.pause_until else None
    rows_to_insert = [
        {
            "user_id":        str(user.id),
            "day_of_week":    s.day_of_week,
            "time_of_day":    s.time_of_day,
            "posts_per_week": posts_per_week,
            "pause_until":    pu,
            "paused_reason":  body.paused_reason,
        }
        for s in body.slots
    ]
    if rows_to_insert:
        db.table("linkedin_posting_schedule").insert(rows_to_insert).execute()

    # Return the freshly-set schedule so the client doesn't need a second GET.
    return await get_posting_schedule(user=user)


def _serialize_dt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None
