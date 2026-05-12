"""
api/proof_points.py — FastAPI router for the proof-point library.

URL shape (mirrors api/stories.py — Phase 1.2 prior art):
    GET    /profile/proof-points              — list (kind, tags, limit query)
    POST   /profile/proof-points              — add new proof point
    GET    /profile/proof-points/{pid}        — single
    PATCH  /profile/proof-points/{pid}        — edit
    DELETE /profile/proof-points/{pid}        — delete
    POST   /profile/proof-points/search       — top-k semantic search
    POST   /profile/proof-points/extract-from-cv — one-shot extractor trigger

All endpoints filter by `Depends(get_current_user)`. RLS on proof_points
backstops the tenant filter at the DB layer.

URL prefix
----------
The Tier 4 spec calls these `/profile/proof-points/*`. We expose them under
that prefix so the future /profile/* surface (CV uploads, sources view,
keyword categories) groups them naturally.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from api.context import get_current_user
from api.users import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile/proof-points", tags=["proof-points"])


KIND_LITERAL = {"launch", "metric", "thought_leadership", "milestone", "press", "other"}


# ─── Request bodies ────────────────────────────────────────────────────────
class AddProofPointBody(BaseModel):
    content: str = Field(min_length=5, max_length=1500)
    kind: str = Field(min_length=1, max_length=40)
    context: Optional[str] = Field(default=None, max_length=2000)
    url: Optional[str] = Field(default=None, max_length=600)
    tags: Optional[list[str]] = Field(default=None, max_length=20)
    quantified_metrics: Optional[list[str]] = Field(default=None, max_length=20)

    @field_validator("kind")
    @classmethod
    def _kind_in_enum(cls, v: str) -> str:
        if v not in KIND_LITERAL:
            raise ValueError(
                f"kind must be one of {sorted(KIND_LITERAL)} (got {v!r})"
            )
        return v


class PatchProofPointBody(BaseModel):
    """Whitelisted edit surface. Server enforces additional whitelist."""
    content: Optional[str] = Field(default=None, min_length=5, max_length=1500)
    kind: Optional[str] = Field(default=None, max_length=40)
    context: Optional[str] = Field(default=None, max_length=2000)
    url: Optional[str] = Field(default=None, max_length=600)
    tags: Optional[list[str]] = None
    quantified_metrics: Optional[list[str]] = None

    @field_validator("kind")
    @classmethod
    def _kind_in_enum(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in KIND_LITERAL:
            raise ValueError(
                f"kind must be one of {sorted(KIND_LITERAL)} (got {v!r})"
            )
        return v


class SearchProofPointsBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=25)
    kind: Optional[str | list[str]] = Field(
        default=None,
        description=(
            "Optional kind pre-filter. String or list — e.g. 'launch' or "
            "['launch','press']."
        ),
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Optional tag-overlap pre-filter (GIN-indexed).",
    )


# ─── Endpoints ─────────────────────────────────────────────────────────────
@router.get("")
async def list_user_proof_points(
    limit: int = Query(default=50, ge=1, le=200),
    kind: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List the current user's proof points, ordered by outcome_score DESC.

    Used by the /profile page and (eventually) the workspace's
    cover-letter side panel. Returns up to `limit` rows. Embeddings are
    stripped (large).
    """
    from agents.proof_point_agent import list_proof_points, proof_point_to_dict
    rows = await list_proof_points(user.id, kind=kind, limit=limit)
    return {
        "proof_points": [proof_point_to_dict(r) for r in rows],
        "count": len(rows),
    }


@router.post("", status_code=201)
async def add_user_proof_point(
    body: AddProofPointBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Add a new proof point. source='user_manual_add' is forced server-side.

    Cost: one embedding call (~$0.00002). Negligible per-call.
    """
    from agents.proof_point_agent import add_proof_point, get_proof_point, proof_point_to_dict
    new_id = await add_proof_point(
        user_id=user.id,
        content=body.content,
        kind=body.kind,
        context=body.context,
        url=body.url,
        tags=body.tags,
        quantified_metrics=body.quantified_metrics,
        source="user_manual_add",
    )
    if new_id is None:
        raise HTTPException(status_code=500, detail="insert_failed")
    row = await get_proof_point(user.id, new_id)
    if not row:
        raise HTTPException(status_code=500, detail="readback_failed")
    return proof_point_to_dict(row)


@router.get("/{pid}")
async def get_user_proof_point(
    pid: UUID,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch one proof point by id. 404 if not the user's."""
    from agents.proof_point_agent import get_proof_point, proof_point_to_dict
    row = await get_proof_point(user.id, pid)
    if not row:
        raise HTTPException(status_code=404, detail="proof_point_not_found")
    return proof_point_to_dict(row)


@router.patch("/{pid}")
async def patch_user_proof_point(
    pid: UUID,
    body: PatchProofPointBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit a proof point (user-driven refinement).

    Re-embeds when content / context / tags change (transparent to caller).
    """
    from agents.proof_point_agent import proof_point_to_dict, update_proof_point
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")
    row = await update_proof_point(user.id, pid, patch=patch)
    if not row:
        raise HTTPException(status_code=404, detail="proof_point_not_found")
    return proof_point_to_dict(row)


@router.delete("/{pid}", status_code=204)
async def delete_user_proof_point(
    pid: UUID,
    user: User = Depends(get_current_user),
) -> None:
    """Delete a proof point."""
    from agents.proof_point_agent import delete_proof_point
    ok = await delete_proof_point(user.id, pid)
    if not ok:
        raise HTTPException(status_code=404, detail="proof_point_not_found")
    return None


@router.post("/search")
async def search_user_proof_points(
    body: SearchProofPointsBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Top-k semantic search across the user's proof points.

    Embeds the query (text-embedding-3-small), then hits the
    `search_proof_points_v2` RPC for DB-side cosine ranking. Falls back
    to client-side cosine when the RPC is unavailable.

    Cost: 1 embedding call (~$0.00002).
    """
    from agents.proof_point_agent import match_to_dict, search_proof_points
    matches = await search_proof_points(
        user.id,
        query=body.query,
        k=body.k,
        kind=body.kind,
        tags=body.tags,
    )
    return {
        "matches": [match_to_dict(m) for m in matches],
        "count": len(matches),
        "query": body.query,
    }


@router.post("/extract-from-cv")
async def extract_from_cv(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """One-shot trigger of the proof-point extractor against
    profile_experience.

    Idempotent — re-runs against unchanged CV produce zero new rows
    (the (user_id, content_hash) unique partial index dedups).

    Cost: ~$0.05 one-time for typical 20-experience CV with 50-100
    highlights (one Sonnet call with cache_system=True, all candidates
    batched into one user message).
    """
    from agents.proof_point_extractor import extract_proof_points_from_experience
    inserted = await extract_proof_points_from_experience(user.id)
    return {"inserted": inserted}
