"""
api/profile.py — FastAPI router for the G11 voice-calibration surface.

URL shape (all gated by Depends(get_current_user), filtered by user.id):

    POST   /profile/writing-samples            — upload a writing sample
    GET    /profile/writing-samples            — list the user's samples
    DELETE /profile/writing-samples/{id}       — delete a sample
    POST   /profile/voice-calibration/run      — enqueue G11
    GET    /profile/voice-calibration          — read the cached JSON

Auto-trigger semantics (POST /profile/writing-samples):
    When a new upload pushes the user's total sample count to ≥ 5
    AND no calibration has run in the last 24h, the upload endpoint
    auto-enqueues G11. The response includes auto_triggered: bool +
    run_id so the frontend can poll /jobs-runs/{run_id} for status.
    The user can also POST /profile/voice-calibration/run manually
    at any time.

Pattern mirrors api/stories.py — Phase-1.2 prior art.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.context import get_current_user
from api.users import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile", "voice-calibration"])


# Minimum samples + cooldown for the auto-trigger. We don't want to spend
# $0.08 every time the user adds a sample — only when the corpus has
# enough signal to produce a meaningfully different voice profile.
_AUTO_TRIGGER_MIN_SAMPLES = 5
_AUTO_TRIGGER_COOLDOWN_HOURS = 24


# ─── Request bodies ────────────────────────────────────────────────────────
class WritingSampleBody(BaseModel):
    """POST /profile/writing-samples body."""
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=50_000)
    kind: str = Field(
        ...,
        description="One of: cover_letter, linkedin_post, email, essay, other",
    )
    source: Optional[str] = Field(default=None, max_length=80)


_ALLOWED_KINDS = frozenset({"cover_letter", "linkedin_post", "email", "essay", "other"})


# ─── Endpoints ─────────────────────────────────────────────────────────────
@router.post("/writing-samples")
async def upload_writing_sample(
    body: WritingSampleBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload a writing sample for the current user.

    Side-effect (auto-trigger): when this brings the user's total sample
    count to ≥ 5 AND ≥ 24h have passed since the last calibration,
    G11 is automatically enqueued. The response includes `auto_triggered`
    and `run_id` (when triggered) so the frontend can poll
    /jobs-runs/{run_id} for status.
    """
    if body.kind not in _ALLOWED_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid_kind — must be one of {sorted(_ALLOWED_KINDS)}",
        )

    from agents.g11_io import (
        count_writing_samples,
        insert_writing_sample,
        load_last_calibration_at,
    )

    row = insert_writing_sample(
        user_id=str(user.id),
        title=body.title.strip(),
        body=body.body.strip(),
        kind=body.kind,
        source=body.source,
    )

    # ── Auto-trigger check ──────────────────────────────────────────────
    auto_triggered = False
    run_id: Optional[str] = None
    try:
        total = count_writing_samples(str(user.id))
        last_at = load_last_calibration_at(str(user.id))
        cooldown_ok = (
            last_at is None
            or (datetime.now(timezone.utc) - last_at)
                >= timedelta(hours=_AUTO_TRIGGER_COOLDOWN_HOURS)
        )
        if total >= _AUTO_TRIGGER_MIN_SAMPLES and cooldown_ok:
            from api.queue import enqueue_g11_voice_calibration
            run_id = enqueue_g11_voice_calibration(user.id, force=False)
            auto_triggered = True
            logger.info(
                f"profile.upload_writing_sample: auto-triggered G11 for "
                f"user_id={user.id} (total={total}, run_id={run_id})"
            )
    except Exception as e:
        # Auto-trigger must never break the upload — log and continue.
        logger.warning(
            f"profile.upload_writing_sample: auto-trigger check failed "
            f"for user_id={user.id}: {type(e).__name__}: {e}"
        )

    return {
        "sample": row,
        "auto_triggered": auto_triggered,
        "run_id": run_id,
    }


@router.get("/writing-samples")
async def list_writing_samples_endpoint(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List the user's writing samples, newest first."""
    from agents.g11_io import list_writing_samples
    rows = list_writing_samples(str(user.id), limit=200)
    return {"samples": rows, "count": len(rows)}


@router.delete("/writing-samples/{sample_id}")
async def delete_writing_sample_endpoint(
    sample_id: UUID,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete one of the user's writing samples. 404 if not owned."""
    from agents.g11_io import delete_writing_sample
    hit = delete_writing_sample(user_id=str(user.id), sample_id=str(sample_id))
    if not hit:
        raise HTTPException(status_code=404, detail="writing_sample_not_found")
    return {"deleted": True, "id": str(sample_id)}


@router.post("/voice-calibration/run")
async def run_voice_calibration(
    force: bool = False,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Manually enqueue a G11 voice-calibration run for the current user.

    Idempotent within the same queued/running window (the queue layer
    dedups on hash(user|kind|payload)). Pass force=true to bypass dedup
    (changes the payload, changes the hash).

    Cost: ~$0.08 per run. Returns the jobs_runs.id (UUID string) for
    polling via GET /jobs-runs/{run_id}.
    """
    from agents.g11_io import count_writing_samples
    if count_writing_samples(str(user.id)) == 0:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_writing_samples",
                "message": (
                    "Cannot calibrate voice with zero writing samples. "
                    "Upload samples via POST /profile/writing-samples first."
                ),
            },
        )

    from api.queue import enqueue_g11_voice_calibration
    run_id = enqueue_g11_voice_calibration(user.id, force=force)
    return {
        "run_id": run_id,
        "kind": "g11_voice_calibration",
    }


@router.get("/voice-calibration")
async def get_voice_calibration(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the cached voice_calibration JSONB for the current user.

    Returns `{"voice_calibration": {}}` when the user hasn't run G11 yet
    (cold-start) — never 404. The frontend can render an empty state
    when voice_calibration is empty.
    """
    from agents.g11_io import load_voice_calibration
    blob = load_voice_calibration(str(user.id))
    if blob is None:
        # User has no profile_master row yet — same as cold-start.
        return {"voice_calibration": {}}
    return {"voice_calibration": blob}
