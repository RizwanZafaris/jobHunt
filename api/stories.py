"""
api/stories.py — FastAPI router for the STAR+R story bank.

URL shape:
    GET    /workspace/stories                  — list current user's stories
    POST   /workspace/stories/search           — top-k semantic search
    POST   /workspace/stories/build            — kick off a G9 extraction run
    GET    /workspace/stories/{story_id}       — single story detail
    PATCH  /workspace/stories/{story_id}       — edit / refine

All endpoints filter by `Depends(get_current_user)`. In single-user mode
this is Rizwan; in multi-tenant mode it's the Supabase JWT subject. RLS
on story_bank backstops the tenant filter at the DB layer.

Why a separate router (not api/workspace.py)?
---------------------------------------------
The stories surface is consumed by THREE places: the workspace tab (single
job), the global "My Stories" page (cross-job), and the application form
co-pilot (Phase 3). Keeping it in its own file makes the future
multi-page work easier — no need to refactor workspace.py to share a
list endpoint. We do reuse the same `/workspace` prefix so the dashboard
routing stays consistent.

Pattern mirrors `api/workspace.py` (Phase 2 prior art): get_current_user
dependency, Pydantic Field-validated bodies, jobs_runs polling via
build endpoint.
"""
from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.context import get_current_user
from api.queue import enqueue_g9_story_extract
from api.users import User

logger = logging.getLogger(__name__)

# B2: was "/workspace/stories" — collided with /workspace/{job_id} (int)
# because workspace_router is included first in server.py. FastAPI matched
# "stories" as job_id="stories" → 422 int_parsing. Top-level prefix fixes it.
router = APIRouter(prefix="/stories", tags=["stories"])


# ─── Request bodies ────────────────────────────────────────────────────────
class SearchStoriesBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=25)
    competency: Optional[str] = Field(
        default=None,
        max_length=100,
        description=(
            "Optional pre-filter on the competencies tag (GIN-indexed). "
            "e.g. 'leadership' returns only leadership-tagged stories."
        ),
    )


class PatchStoryBody(BaseModel):
    """Whitelisted edit surface. Server enforces additional whitelist."""
    title: Optional[str] = Field(default=None, max_length=200)
    situation: Optional[str] = Field(default=None, max_length=2000)
    task: Optional[str] = Field(default=None, max_length=2000)
    action: Optional[str] = Field(default=None, max_length=4000)
    result: Optional[str] = Field(default=None, max_length=2000)
    reflection: Optional[str] = Field(default=None, max_length=2000)
    competencies: Optional[list[str]] = None
    archetypes: Optional[list[str]] = None
    quantified_metrics: Optional[list[str]] = None


# ─── Endpoints ─────────────────────────────────────────────────────────────
@router.get("")
async def list_user_stories(
    limit: int = Query(default=50, ge=1, le=200),
    competency: Optional[str] = Query(default=None),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List the current user's STAR+R stories, ordered by outcome_score DESC.

    Used by the global "My Stories" page and the workspace's behavioural-prep
    side panel. Returns up to `limit` rows. Embeddings are stripped (large).
    """
    from agents.story_bank_agent import list_stories, story_to_dict
    rows = await list_stories(user.id, limit=limit, competency=competency)
    return {
        "stories": [story_to_dict(r) for r in rows],
        "count": len(rows),
    }


@router.post("/search")
async def search_user_stories(
    body: SearchStoriesBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Top-k semantic search across the user's story_bank.

    Embeds the query (text-embedding-3-small) and ranks by cosine
    similarity. Bayesian tiebreaker boosts stories with higher
    outcome_score so successful stories surface first on near-ties.

    Cost: 1 embedding call (~$0.00002). Negligible per-call; users can
    hit this freely.
    """
    from agents.story_bank_agent import match_to_dict, search_stories
    matches = await search_stories(
        user.id,
        query=body.query,
        k=body.k,
        competency=body.competency,
    )
    return {
        "matches": [match_to_dict(m) for m in matches],
        "count": len(matches),
        "query": body.query,
    }


@router.post("/build")
async def build_user_stories(
    force: bool = Query(default=False),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Kick off a G9 STAR+R extraction run for the current user's CV.

    Idempotent on (user_id, cv_hash): re-uploading the SAME CV markdown
    collapses to one queued run. Pass force=true to bypass dedup.

    Returns the jobs_runs.id; the UI polls /jobs-runs/{run_id} until
    status is terminal (the dashboard already has the polling
    infrastructure from G2 / G3).
    """
    from agents.g9_io import hash_cv, render_master_cv_md
    try:
        md = render_master_cv_md()
    except RuntimeError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_master_cv",
                "message": (
                    "Cannot extract stories without a master CV. Run the "
                    "profile_build pipeline first."
                ),
                "underlying": str(e),
            },
        )
    cv_hash = hash_cv(md)
    run_id = enqueue_g9_story_extract(user.id, cv_hash=cv_hash, force=force)
    return {
        "run_id": run_id,
        "kind": "g9_story_extract",
        "cv_hash": cv_hash,
        "cv_chars": len(md),
    }


@router.get("/{story_id}")
async def get_user_story(
    story_id: UUID,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Fetch one story by id. 404 if not the user's."""
    from agents.story_bank_agent import get_story, story_to_dict
    row = await get_story(user.id, story_id)
    if not row:
        raise HTTPException(status_code=404, detail="story_not_found")
    return story_to_dict(row)


@router.patch("/{story_id}")
async def patch_user_story(
    story_id: UUID,
    body: PatchStoryBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Edit a generated story (user-driven refinement).

    Re-embeds when STAR text changes (the agent does this transparently).
    """
    from agents.story_bank_agent import story_to_dict, update_story
    patch = body.model_dump(exclude_none=True)
    if not patch:
        raise HTTPException(status_code=400, detail="empty_patch")
    row = await update_story(user.id, story_id, patch=patch)
    if not row:
        raise HTTPException(status_code=404, detail="story_not_found")
    return story_to_dict(row)
