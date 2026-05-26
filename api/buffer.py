"""
api/buffer.py — FastAPI router for Buffer OAuth + scheduling.

Endpoints (all gated by X-Secret-Key + Depends(get_current_user)):

  GET   /buffer/connect/start                Returns OAuth URL + state to persist
  GET   /buffer/connect/callback             OAuth callback (Buffer redirects here)
  GET   /buffer/profiles                     User's connected LinkedIn profile ids
  POST  /buffer/disconnect                   Revoke + delete tokens
  POST  /buffer/autopost/enable              Flip profile_master.buffer_autopost_enabled
  POST  /buffer/autopost/disable
  POST  /linkedin/drafts/{id}/schedule-to-buffer
                                            Schedule an approved draft via Buffer
  POST  /linkedin/drafts/{id}/cancel-buffer   Cancel a still-pending Buffer post
  GET   /buffer/status                       Aggregate status for the dashboard

Gating rules:
  • OAuth must be connected (buffer_oauth_tokens row, not revoked)
  • profile_master.buffer_autopost_enabled MUST be true
  • The draft MUST be status='approved' (G4 HITL preservation)

This router preserves the existing G4 contract — manual copy/paste flow
is untouched. Buffer is purely additive.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.context import get_current_user
from api.users import User
from db.client import get_supabase
from integrations.buffer_client import (
    BufferAPIError,
    BufferConfigError,
    get_buffer_client,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/buffer", tags=["buffer"])
# Draft-scheduling endpoints live under /linkedin/ to keep the LinkedIn
# router's surface contiguous from the dashboard's POV.
linkedin_buffer_router = APIRouter(prefix="/linkedin", tags=["buffer"])


# ─── helpers ──────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_buffer_client():
    try:
        return get_buffer_client()
    except BufferConfigError as e:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Buffer integration not configured on this deployment: {e}. "
                "Set BUFFER_CLIENT_ID, BUFFER_CLIENT_SECRET, BUFFER_REDIRECT_URI "
                "in env and restart."
            ),
        )


def _get_active_token(user_id) -> Optional[dict]:
    """Return the most-recent non-revoked Buffer OAuth row, or None."""
    rows = (
        get_supabase()
        .table("buffer_oauth_tokens")
        .select("*")
        .eq("user_id", str(user_id))
        .is_("revoked_at", None)
        .order("connected_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def _autopost_enabled(user_id) -> bool:
    rows = (
        get_supabase()
        .table("profile_master")
        .select("buffer_autopost_enabled")
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        return False
    return bool(rows[0].get("buffer_autopost_enabled"))


# ─── OAuth start / callback ───────────────────────────────────────────────
class ConnectStartResponse(BaseModel):
    authorize_url: str
    state: str


@router.get("/connect/start", response_model=ConnectStartResponse)
def buffer_connect_start(user: User = Depends(get_current_user)) -> ConnectStartResponse:
    """Return the URL to send the user to for Buffer OAuth consent.

    The caller must persist `state` and verify it on the callback.
    The dashboard stores it in the session cookie.
    """
    client = _safe_buffer_client()
    state = secrets.token_urlsafe(32)
    url = client.build_authorize_url(state=state)
    return ConnectStartResponse(authorize_url=url, state=state)


class ConnectCallbackBody(BaseModel):
    code: str = Field(min_length=4)
    state: str = Field(min_length=4)
    expected_state: str = Field(min_length=4)


@router.post("/connect/callback")
async def buffer_connect_callback(
    body: ConnectCallbackBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Exchange Buffer OAuth code for tokens; persist; return profiles.

    The dashboard POSTs here from the OAuth redirect page after
    extracting code + state from the query string and reading
    expected_state from its session cookie.
    """
    if not secrets.compare_digest(body.state, body.expected_state):
        raise HTTPException(
            status_code=400,
            detail="OAuth state mismatch — refusing to complete flow.",
        )

    client = _safe_buffer_client()
    try:
        token_resp = await client.exchange_code(body.code)
    except BufferAPIError as e:
        raise HTTPException(status_code=502, detail=f"Buffer token exchange failed: {e}")

    access_token = token_resp.get("access_token")
    if not access_token:
        raise HTTPException(
            status_code=502,
            detail=f"Buffer returned no access_token: {token_resp}",
        )

    # Fetch profiles so we can persist one row per LinkedIn profile.
    try:
        profiles = await client.list_linkedin_profiles(access_token)
    except BufferAPIError as e:
        raise HTTPException(status_code=502, detail=f"Buffer profiles fetch failed: {e}")
    if not profiles:
        raise HTTPException(
            status_code=422,
            detail=(
                "No LinkedIn profile connected to this Buffer account. "
                "Connect LinkedIn in Buffer first, then retry."
            ),
        )

    expires_at = None
    if token_resp.get("expires_in"):
        try:
            expires_at = (_utcnow().timestamp() + int(token_resp["expires_in"]))
            expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
        except (ValueError, TypeError):
            expires_at = None

    db = get_supabase()
    saved: list[dict] = []
    for p in profiles:
        row = {
            "user_id": str(user.id),
            "buffer_profile_id": p.id,
            "buffer_profile_name": p.name or p.service_username,
            "access_token": access_token,
            "refresh_token": token_resp.get("refresh_token"),
            "expires_at": expires_at,
            "scopes": token_resp.get("scope", "").split(",") if token_resp.get("scope") else None,
            "connected_at": _utcnow().isoformat(),
        }
        try:
            db.table("buffer_oauth_tokens").upsert(
                row, on_conflict="user_id,buffer_profile_id"
            ).execute()
            saved.append({"profile_id": p.id, "name": row["buffer_profile_name"]})
        except Exception:
            logger.exception("Buffer token persist failed for profile %s", p.id)

    # If user has no default profile set, default to the first connected one.
    try:
        pm = (
            db.table("profile_master")
            .select("buffer_default_profile_id")
            .eq("user_id", str(user.id))
            .limit(1)
            .execute()
            .data
        ) or []
        if pm and not pm[0].get("buffer_default_profile_id") and saved:
            db.table("profile_master").update({
                "buffer_default_profile_id": saved[0]["profile_id"]
            }).eq("user_id", str(user.id)).execute()
    except Exception:
        logger.exception("default profile assignment failed (non-fatal)")

    return {"ok": True, "profiles": saved}


# ─── Read / disconnect ────────────────────────────────────────────────────
@router.get("/profiles")
def buffer_list_profiles(user: User = Depends(get_current_user)) -> dict[str, Any]:
    rows = (
        get_supabase()
        .table("buffer_oauth_tokens")
        .select("buffer_profile_id, buffer_profile_name, connected_at, last_used_at")
        .eq("user_id", str(user.id))
        .is_("revoked_at", None)
        .order("connected_at", desc=True)
        .execute()
        .data
    ) or []
    return {"profiles": rows}


@router.post("/disconnect")
def buffer_disconnect(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Mark all the user's Buffer tokens as revoked. Drafts already
    scheduled at Buffer are NOT cancelled — call /linkedin/drafts/{id}/cancel-buffer
    individually for that."""
    now = _utcnow().isoformat()
    get_supabase().table("buffer_oauth_tokens").update({
        "revoked_at": now,
    }).eq("user_id", str(user.id)).is_("revoked_at", None).execute()
    # Also flip the autopost toggle off so the user must re-consent.
    get_supabase().table("profile_master").update({
        "buffer_autopost_enabled": False,
    }).eq("user_id", str(user.id)).execute()
    return {"ok": True, "revoked_at": now}


# ─── Autopost toggle ──────────────────────────────────────────────────────
@router.post("/autopost/enable")
def buffer_enable_autopost(user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Opt this tenant into Buffer auto-posting. Requires connected OAuth."""
    if not _get_active_token(user.id):
        raise HTTPException(
            status_code=409,
            detail="No active Buffer OAuth token — connect Buffer first.",
        )
    get_supabase().table("profile_master").update({
        "buffer_autopost_enabled": True,
    }).eq("user_id", str(user.id)).execute()
    return {"ok": True, "autopost_enabled": True}


@router.post("/autopost/disable")
def buffer_disable_autopost(user: User = Depends(get_current_user)) -> dict[str, Any]:
    get_supabase().table("profile_master").update({
        "buffer_autopost_enabled": False,
    }).eq("user_id", str(user.id)).execute()
    return {"ok": True, "autopost_enabled": False}


@router.get("/status")
def buffer_status(user: User = Depends(get_current_user)) -> dict[str, Any]:
    tok = _get_active_token(user.id)
    profiles = (
        get_supabase()
        .table("buffer_oauth_tokens")
        .select("buffer_profile_id, buffer_profile_name")
        .eq("user_id", str(user.id))
        .is_("revoked_at", None)
        .execute()
        .data
    ) or []
    return {
        "connected": tok is not None,
        "autopost_enabled": _autopost_enabled(user.id),
        "profiles": profiles,
    }


# ─── Schedule / cancel draft via Buffer ───────────────────────────────────
class ScheduleToBufferBody(BaseModel):
    """Optional override of buffer_profile_id + scheduled_at.

    If buffer_profile_id is omitted, uses profile_master.buffer_default_profile_id.
    If scheduled_at is omitted, Buffer adds to the profile's posting queue
    (Buffer schedules per the user's Buffer-side time slots).
    """
    buffer_profile_id: Optional[str] = None
    scheduled_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 UTC. Must be in the future. Omit to use Buffer's queue.",
    )
    link: Optional[str] = None
    media_photo_url: Optional[str] = None


@linkedin_buffer_router.post("/drafts/{draft_id}/schedule-to-buffer")
async def schedule_draft_to_buffer(
    draft_id: str,
    body: ScheduleToBufferBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Push an approved draft to Buffer for auto-posting.

    Gates (all must pass):
      1. buffer_autopost_enabled = true on profile_master
      2. Active (non-revoked) Buffer OAuth token exists
      3. Draft exists, belongs to user, status='approved'
    """
    # Gate 1
    if not _autopost_enabled(user.id):
        raise HTTPException(
            status_code=403,
            detail=(
                "Buffer auto-post is disabled for this account. "
                "POST /buffer/autopost/enable first."
            ),
        )

    # Gate 2
    tok = _get_active_token(user.id)
    if not tok:
        raise HTTPException(
            status_code=409,
            detail="No active Buffer OAuth — call /buffer/connect/start.",
        )

    # Gate 3
    db = get_supabase()
    draft_rows = (
        db.table("linkedin_drafts")
        .select("id, body, hook, status, buffer_post_id, buffer_status")
        .eq("user_id", str(user.id))
        .eq("id", draft_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not draft_rows:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    d = draft_rows[0]
    if (d.get("status") or "").lower() != "approved":
        raise HTTPException(
            status_code=409,
            detail=(
                f"draft status is '{d.get('status')}', must be 'approved' "
                "before scheduling to Buffer (HITL preservation)."
            ),
        )
    if d.get("buffer_post_id") and (d.get("buffer_status") or "").lower() in (
        "pending", "scheduled"
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"draft is already at Buffer (buffer_post_id={d['buffer_post_id']}, "
                f"status={d['buffer_status']}). Cancel first or use a new draft."
            ),
        )

    # Resolve profile id
    profile_id = body.buffer_profile_id
    if not profile_id:
        pm = (
            db.table("profile_master")
            .select("buffer_default_profile_id")
            .eq("user_id", str(user.id))
            .limit(1)
            .execute()
            .data
        ) or []
        if pm:
            profile_id = pm[0].get("buffer_default_profile_id")
    if not profile_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "No buffer_profile_id provided and no default set. "
                "Pass buffer_profile_id in body or set buffer_default_profile_id."
            ),
        )

    # Compose post text: hook + body (standard LinkedIn draft format).
    text_parts = []
    if d.get("hook"):
        text_parts.append(str(d["hook"]).strip())
    if d.get("body"):
        text_parts.append(str(d["body"]).strip())
    text = "\n\n".join(p for p in text_parts if p)
    if not text:
        raise HTTPException(status_code=422, detail="draft has no body or hook")

    # Fire Buffer create.
    client = _safe_buffer_client()
    try:
        updates = await client.create_update(
            tok["access_token"],
            profile_ids=[profile_id],
            text=text,
            scheduled_at=body.scheduled_at,
            link=body.link,
            media_photo_url=body.media_photo_url,
        )
    except BufferAPIError as e:
        # Persist the failure so the dashboard can surface it.
        db.table("linkedin_drafts").update({
            "buffer_status": "failed",
            "buffer_error": str(e)[:1000],
        }).eq("id", draft_id).execute()
        raise HTTPException(status_code=502, detail=f"Buffer rejected the post: {e}")

    if not updates:
        raise HTTPException(status_code=502, detail="Buffer returned no updates")
    u = updates[0]

    # Persist Buffer state on the draft.
    db.table("linkedin_drafts").update({
        "buffer_post_id": u.id,
        "buffer_status": "scheduled" if body.scheduled_at else "pending",
        "buffer_scheduled_at": body.scheduled_at,
        "buffer_error": None,
    }).eq("id", draft_id).execute()

    # Stamp last_used_at on the token row.
    db.table("buffer_oauth_tokens").update({
        "last_used_at": _utcnow().isoformat(),
    }).eq("id", tok["id"]).execute()

    return {
        "ok": True,
        "draft_id": draft_id,
        "buffer_post_id": u.id,
        "buffer_status": "scheduled" if body.scheduled_at else "pending",
        "scheduled_at": body.scheduled_at,
    }


@linkedin_buffer_router.post("/drafts/{draft_id}/cancel-buffer")
async def cancel_buffer_post(
    draft_id: str,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    db = get_supabase()
    rows = (
        db.table("linkedin_drafts")
        .select("id, buffer_post_id, buffer_status")
        .eq("user_id", str(user.id))
        .eq("id", draft_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    d = rows[0]
    if not d.get("buffer_post_id"):
        raise HTTPException(status_code=409, detail="draft has no Buffer post id")
    if (d.get("buffer_status") or "").lower() in ("posted", "cancelled"):
        return {"ok": True, "noop": True, "buffer_status": d.get("buffer_status")}

    tok = _get_active_token(user.id)
    if not tok:
        raise HTTPException(status_code=409, detail="No active Buffer OAuth.")

    client = _safe_buffer_client()
    try:
        cancelled = await client.cancel_update(tok["access_token"], d["buffer_post_id"])
    except BufferAPIError as e:
        raise HTTPException(status_code=502, detail=f"Buffer cancel failed: {e}")

    if cancelled:
        db.table("linkedin_drafts").update({
            "buffer_status": "cancelled",
        }).eq("id", draft_id).execute()
    return {"ok": True, "cancelled": cancelled}
