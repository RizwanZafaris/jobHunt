"""
api/workspace.py — FastAPI router for the Application Workspace surface.

The workspace is the page a user lands on when they click "Start
application process" on a /today action card. URL shape:

    /applications/{job_id}/workspace

This router serves the BUNDLE that page renders (one round-trip; the UI
does not chain calls), plus the mutation endpoints for the five tabs:

    GET    /workspace/{job_id}                  — full bundle
    POST   /workspace/{job_id}/build-resume     — enqueue G2
    POST   /workspace/{job_id}/edit-resume      — Quick tweak (Opus 4.7)
    POST   /workspace/{job_id}/save-resume-edit — persist user edit
    POST   /workspace/{job_id}/mark-applied     — applications row → applied
    GET    /workspace/{job_id}/resume.{format}  — md / pdf / docx download

All endpoints filter by `Depends(get_current_user)`. In single-user
mode this is Rizwan; in multi-tenant mode it's the Supabase JWT subject.

Design choices (recorded in api/WORKSPACE.md):

  • Bundle endpoint denormalises every piece of context the page needs,
    keyed off `job_id`. The cost is one extra DB round-trip vs. the
    smallest possible response — but the benefit is that the workspace
    page is a pure server-component fetch with no client-side waterfall.

  • Warm-intro paths reuse `agents.referral_graph.ReferralGraph.find_paths`
    via a fuzzy-resolved `target_company_id`. We don't 404 if there's no
    target_companies row yet — we degrade to `warm_intros_available: false`
    and the Network tab shows the import-CSV empty state.

  • Resume edit chat is stateless on the server today — the chat history
    rides in the request body. Persisting the conversation belongs to a
    later phase (add an `editor_chat_messages` table); see WORKSPACE.md
    for the contract.

  • PDF/DOCX downloads redirect to the existing `resume_pdf_url` /
    `resume_docx_url` URLs on `resume_builds` if those exist; otherwise
    501 with a clear message. PDF rendering from markdown is wired in
    Phase 3 alongside the cover-email rendering work.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from agents.referral_graph import (
    DEFAULT_MAX_HOPS,
    DEFAULT_MIN_STRENGTH,
    ReferralGraph,
    ReferralPath,
)
from agents.resume_edit_assistant import (
    CostCapExceeded,
    quick_tweak,
)
from api.context import get_current_user
from api.queue import enqueue_g2_build
from api.users import User
from db.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspace", tags=["workspace"])


# ─── Request models ────────────────────────────────────────────────────────
class EditChatTurn(BaseModel):
    role: str = Field(description="user | assistant")
    content: str


class EditResumeBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    current_md: str = Field(min_length=1)
    chat_history: Optional[list[EditChatTurn]] = None
    mode: str = Field(default="quick_tweak", description="quick_tweak | rebuild_section | full_rebuild")


class SaveResumeEditBody(BaseModel):
    edited_md: str = Field(min_length=1)
    build_id: Optional[str] = Field(
        default=None,
        description="Resume build to save under. If omitted, the latest converged build for this job is used.",
    )


class MarkAppliedBody(BaseModel):
    applied_date: Optional[str] = Field(
        default=None,
        description="ISO date (YYYY-MM-DD). Defaults to today (UTC).",
    )
    notes: Optional[str] = None


# ─── Helpers ───────────────────────────────────────────────────────────────
def _get_job_for_user(db, *, job_id: int, user_id: UUID) -> dict[str, Any]:
    """Fetch a job row scoped to the current user. 404s on miss."""
    rows = (
        db.table("jobs")
        .select("*")
        .eq("id", job_id)
        .eq("user_id", str(user_id))
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="job_not_found")
    return rows[0]


def _get_application_for_job(db, *, job_id: int, user_id: UUID) -> Optional[dict[str, Any]]:
    """Most-recent application for this job. None if not yet applied."""
    rows = (
        db.table("applications")
        .select("*")
        .eq("job_id", job_id)
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def _get_latest_resume_build(db, *, job_id: int, user_id: UUID) -> Optional[dict[str, Any]]:
    """Latest build for this job + user. Prefer 'converged'; fall back to most recent."""
    rows = (
        db.table("resume_builds")
        .select(
            "id, job_id, application_id, company_name, persona_version, status, "
            "iterations, resume_md, resume_pdf_url, resume_docx_url, "
            "user_edited_md, user_edited_at, cost_usd_total, latency_ms_total, "
            "created_at, finalized_at, error"
        )
        .eq("job_id", job_id)
        .eq("user_id", str(user_id))
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    ).data or []
    if not rows:
        return None
    converged = next((r for r in rows if r.get("status") == "converged"), None)
    return converged or rows[0]


def _get_persona_for_company(db, *, company_name: str, user_id: UUID) -> Optional[dict[str, Any]]:
    """Pull the company persona for the user. Tolerates missing rows."""
    if not company_name:
        return None
    rows = (
        db.table("company_personas")
        .select(
            "id, company_id, company_name, persona_version, "
            "ats_keyword_bank, success_patterns, failure_patterns, "
            "metadata, last_synthesized_at"
        )
        .eq("company_name", company_name)
        .eq("user_id", str(user_id))
        .order("persona_version", desc=True)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def _get_interview_prep_summary(db, *, job_id: int, user_id: UUID) -> Optional[dict[str, Any]]:
    """interview_prep summary if any pack exists for this job."""
    try:
        rows = (
            db.table("interview_prep")
            .select("id, application_id, status, prep_pack_url, round_type, round_number, created_at")
            .eq("job_id", job_id)
            .eq("user_id", str(user_id))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        ).data or []
    except Exception as exc:
        # interview_prep may not have user_id wired in some tenants —
        # degrade gracefully.
        logger.warning("interview_prep lookup failed for job %s: %s", job_id, exc)
        return None
    if not rows:
        return None
    row = rows[0]
    has_pack = bool(row.get("prep_pack_url")) and row.get("status") == "converged"
    return {
        "has_pack": has_pack,
        "prep_pack_url": row.get("prep_pack_url"),
        "status": row.get("status"),
        "round_type": row.get("round_type"),
        "round_number": row.get("round_number"),
        "interview_prep_id": row.get("id"),
    }


def _resolve_target_company_id(db, *, company_name: str, user_id: UUID) -> Optional[str]:
    """Map a job's company name → target_companies.id for THIS user.

    Path-finder requires a target_company_id, not a name. We try exact,
    then case-insensitive, before giving up.

    Returns None if the user has no target_companies row for this
    company. The Network tab handles that case with an explicit "No warm
    intros to {company} yet" empty state.
    """
    if not company_name:
        return None
    try:
        # 1. Exact match on `company_name` column.
        rows = (
            db.table("target_companies")
            .select("id, company_name, name")
            .eq("user_id", str(user_id))
            .eq("company_name", company_name)
            .limit(1)
            .execute()
        ).data or []
        if rows:
            return str(rows[0]["id"])
        # 2. ILIKE fallback (handles "Stripe" vs "Stripe, Inc."-style drift).
        rows = (
            db.table("target_companies")
            .select("id, company_name, name")
            .eq("user_id", str(user_id))
            .ilike("company_name", f"%{company_name}%")
            .limit(1)
            .execute()
        ).data or []
        if rows:
            return str(rows[0]["id"])
        # 3. ILIKE on the display `name` column.
        rows = (
            db.table("target_companies")
            .select("id, company_name, name")
            .eq("user_id", str(user_id))
            .ilike("name", f"%{company_name}%")
            .limit(1)
            .execute()
        ).data or []
        if rows:
            return str(rows[0]["id"])
    except Exception as exc:
        logger.warning("target_company resolve failed for %s: %s", company_name, exc)
    return None


def _network_size_for_user(db, *, user_id: UUID) -> int:
    """Cheap HEAD-style count: how many people the user has on file.

    Used by the workspace bundle so the Network tab can render the
    "Import your LinkedIn CSV" empty state vs. "No warm intros to
    {company} yet — N contacts on file" message.
    """
    try:
        result = (
            db.table("people")
            .select("id", count="exact")
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        # Supabase-py returns the count on the response object.
        count = getattr(result, "count", None)
        if isinstance(count, int):
            return count
        return len(result.data or [])
    except Exception as exc:
        logger.warning("network size count failed: %s", exc)
        return 0


def _serialize_resume_build(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Trim the resume_build row to what the workspace UI actually needs."""
    if not row:
        return None
    return {
        "build_id": row.get("id"),
        "status": row.get("status"),
        "iterations": row.get("iterations"),
        "resume_md": row.get("resume_md"),
        "user_edited_md": row.get("user_edited_md"),
        "user_edited_at": row.get("user_edited_at"),
        "resume_pdf_url": row.get("resume_pdf_url"),
        "resume_docx_url": row.get("resume_docx_url"),
        "cost_usd_total": row.get("cost_usd_total"),
        "latency_ms_total": row.get("latency_ms_total"),
        "company_name": row.get("company_name"),
        "persona_version": row.get("persona_version"),
        "created_at": row.get("created_at"),
        "finalized_at": row.get("finalized_at"),
        "error": row.get("error"),
    }


def _serialize_persona(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return {
        "company_id": row.get("company_id"),
        "company_name": row.get("company_name"),
        "persona_version": row.get("persona_version"),
        "ats_keyword_bank": row.get("ats_keyword_bank") or {},
        "success_patterns": row.get("success_patterns") or [],
        "failure_patterns": row.get("failure_patterns") or [],
        "metadata": row.get("metadata") or {},
        "last_synthesized_at": row.get("last_synthesized_at"),
    }


def _serialize_job(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "company": row.get("company"),
        "company_id": row.get("company_id"),
        "location": row.get("location"),
        "url": row.get("url"),
        "description": row.get("description"),
        "match_score": row.get("match_score"),
        "fit_details": row.get("fit_details") or {},
        "status": row.get("status"),
        "discovered_at": row.get("discovered_at"),
        "applied_at": row.get("applied_at"),
        "resume_generated_at": row.get("resume_generated_at"),
        "validation_status": row.get("validation_status"),
        "archetype": row.get("archetype"),
    }


# ─── GET /workspace/{job_id} ───────────────────────────────────────────────
@router.get("/{job_id}")
def get_workspace(
    job_id: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the full workspace bundle for a job.

    One denormalised response so the page loads with no client-side
    waterfall. See module docstring for design rationale.
    """
    db = get_supabase()
    job = _get_job_for_user(db, job_id=job_id, user_id=user.id)
    application = _get_application_for_job(db, job_id=job_id, user_id=user.id)
    resume = _get_latest_resume_build(db, job_id=job_id, user_id=user.id)
    persona = _get_persona_for_company(db, company_name=job.get("company") or "", user_id=user.id)
    interview_prep = _get_interview_prep_summary(db, job_id=job_id, user_id=user.id)

    # Warm intros — only call the path-finder if we resolved a target.
    warm_paths: list[ReferralPath] = []
    target_company_id = _resolve_target_company_id(
        db, company_name=job.get("company") or "", user_id=user.id
    )
    if target_company_id:
        try:
            rg = ReferralGraph(user.id)
            warm_paths = rg.find_paths(
                target_company_id=target_company_id,
                max_hops=DEFAULT_MAX_HOPS,
                min_strength=DEFAULT_MIN_STRENGTH,
                limit=5,
            )
        except Exception as exc:
            logger.warning(
                "find_paths failed for job %s target %s: %s",
                job_id, target_company_id, exc,
            )
            warm_paths = []

    network_size = _network_size_for_user(db, user_id=user.id)

    return {
        "job": _serialize_job(job),
        "application": application,
        "resume": _serialize_resume_build(resume),
        "persona": _serialize_persona(persona),
        "interview_prep": interview_prep,
        "target_company_id": target_company_id,
        "warm_intro_paths": [p.model_dump() for p in warm_paths],
        "warm_intros_available": bool(warm_paths),
        "network_size": network_size,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ─── POST /workspace/{job_id}/build-resume ─────────────────────────────────
@router.post("/{job_id}/build-resume")
def build_resume(
    job_id: int,
    force: bool = Query(default=False),
    max_cost_usd: Optional[float] = Query(default=None, ge=0.5, le=20.0),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Enqueue a fresh G2 build for this job.

    Idempotent on (user_id, kind=g2_resume, payload) — the queue's
    `_enqueue_or_dedup` guard returns the existing run id if one is
    already queued/running.

    Returns the jobs_runs.id; the UI polls `/jobs-runs/{run_id}` (already
    shipped in api/jobs_runs.py) every ~8s until status is terminal.
    """
    db = get_supabase()
    _get_job_for_user(db, job_id=job_id, user_id=user.id)  # 404 if not theirs
    try:
        run_id = enqueue_g2_build(
            user_id=user.id,
            job_id=job_id,
            force=force,
            max_cost_usd=max_cost_usd,
        )
    except RuntimeError as exc:
        # Redis/RQ not installed in this environment — surface clearly.
        raise HTTPException(
            status_code=503,
            detail=f"queue_unavailable: {exc}",
        ) from exc
    return {
        "run_id": run_id,
        "status": "queued",
        "kind": "g2_resume",
        "job_id": job_id,
        "force": force,
        "max_cost_usd": max_cost_usd,
        "poll_url": f"/jobs-runs/{run_id}",
    }


# ─── POST /workspace/{job_id}/edit-resume ──────────────────────────────────
@router.post("/{job_id}/edit-resume")
async def edit_resume(
    job_id: int,
    body: EditResumeBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Apply ONE edit instruction to the resume markdown.

    Phase 2 ships only the `quick_tweak` mode. The other two modes return
    501 with a clear "coming next session" message — keeps the chat panel
    UI stable while the backend catches up.

    Stateless on the server: the chat history rides in the request body.
    Persistence to a future `editor_chat_messages` table is documented
    in api/WORKSPACE.md — out of scope for Phase 2.
    """
    db = get_supabase()
    job = _get_job_for_user(db, job_id=job_id, user_id=user.id)

    if body.mode == "rebuild_section":
        raise HTTPException(
            status_code=501,
            detail={
                "code": "mode_not_implemented",
                "mode": "rebuild_section",
                "message": "Rebuild-section is wired in the next session. Use Quick tweak for now.",
            },
        )
    if body.mode == "full_rebuild":
        raise HTTPException(
            status_code=501,
            detail={
                "code": "mode_not_implemented",
                "mode": "full_rebuild",
                "message": (
                    "Full rebuild is wired in the next session. Use the 'Build resume' "
                    "button on the Resume tab to enqueue a fresh G2 run."
                ),
            },
        )
    if body.mode != "quick_tweak":
        raise HTTPException(status_code=400, detail=f"unknown_mode:{body.mode}")

    persona = _get_persona_for_company(db, company_name=job.get("company") or "", user_id=user.id)
    chat_history = (
        [{"role": t.role, "content": t.content} for t in body.chat_history]
        if body.chat_history else None
    )

    try:
        result = await quick_tweak(
            current_md=body.current_md,
            instruction=body.instruction,
            persona=persona,
            jd={
                "company": job.get("company"),
                "title": job.get("title"),
                "description": job.get("description"),
            },
            chat_history=chat_history,
        )
    except CostCapExceeded as exc:
        # 429 is the right shape: "you tried, the system refused, try again".
        raise HTTPException(
            status_code=429,
            detail={
                "code": "cost_cap_exceeded",
                "message": str(exc),
            },
        ) from exc
    except ValueError as exc:
        # Empty input or malformed model output.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("quick_tweak failed for job %s", job_id)
        raise HTTPException(
            status_code=502,
            detail=f"resume_editor_unavailable: {type(exc).__name__}",
        ) from exc

    return result


# ─── POST /workspace/{job_id}/save-resume-edit ─────────────────────────────
@router.post("/{job_id}/save-resume-edit")
def save_resume_edit(
    job_id: int,
    body: SaveResumeEditBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Persist the user-edited markdown onto the resume_builds row.

    This is the EXACT same write the existing
    PATCH /resume-builds/{id}/markdown does, but scoped to the workspace
    (job_id is the source of truth on this surface; the UI doesn't care
    about build ids unless explicitly switching builds).

    If `build_id` is supplied we honour it — otherwise we save against
    the latest converged build for this job + user.
    """
    db = get_supabase()
    job = _get_job_for_user(db, job_id=job_id, user_id=user.id)

    build_row = None
    if body.build_id:
        rows = (
            db.table("resume_builds")
            .select("*")
            .eq("id", body.build_id)
            .eq("user_id", str(user.id))
            .eq("job_id", job_id)
            .limit(1)
            .execute()
        ).data or []
        if rows:
            build_row = rows[0]
    if build_row is None:
        build_row = _get_latest_resume_build(db, job_id=job_id, user_id=user.id)
    if build_row is None:
        raise HTTPException(status_code=404, detail="no_resume_build_for_job")

    update = {
        "user_edited_md": body.edited_md,
        "user_edited_at": datetime.now(timezone.utc).isoformat(),
    }
    result = (
        db.table("resume_builds")
        .update(update)
        .eq("id", build_row["id"])
        .execute()
    )
    saved = (result.data or [None])[0]
    if not saved:
        # Some postgrest versions don't echo on update; re-read.
        saved_rows = (
            db.table("resume_builds")
            .select("*")
            .eq("id", build_row["id"])
            .limit(1)
            .execute()
        ).data or []
        saved = saved_rows[0] if saved_rows else build_row

    return {
        "saved": True,
        "build_id": build_row["id"],
        "job_id": job_id,
        "company": job.get("company"),
        "user_edited_at": saved.get("user_edited_at"),
        "byte_size": len((body.edited_md or "").encode("utf-8")),
    }


# ─── POST /workspace/{job_id}/mark-applied ─────────────────────────────────
@router.post("/{job_id}/mark-applied")
def mark_applied(
    job_id: int,
    body: MarkAppliedBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Move the application to 'applied'. Creates the row if absent.

    After this call the home action card (resume_ready) drops off /today
    and the Applications board shows the row in its 'applied' column.
    """
    db = get_supabase()
    job = _get_job_for_user(db, job_id=job_id, user_id=user.id)
    applied_iso = body.applied_date or date.today().isoformat()

    existing = _get_application_for_job(db, job_id=job_id, user_id=user.id)
    if existing:
        update: dict[str, Any] = {
            "status": "applied",
            "applied_date": applied_iso,
        }
        if body.notes is not None:
            update["notes"] = body.notes
        result = (
            db.table("applications")
            .update(update)
            .eq("id", existing["id"])
            .execute()
        )
        row = (result.data or [None])[0] or {**existing, **update}
        # Update the jobs row's applied_at + status so /today drops the card.
        try:
            db.table("jobs").update({
                "status": "applied",
                "applied_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", job_id).eq("user_id", str(user.id)).execute()
        except Exception as exc:
            logger.warning("jobs.applied_at backfill failed: %s", exc)
        return {"created": False, "updated": True, "application": row}

    # Create new
    new_row: dict[str, Any] = {
        "user_id": str(user.id),
        "job_id": job_id,
        "company": job.get("company") or "",
        "role": job.get("title") or "",
        "status": "applied",
        "applied_date": applied_iso,
        "score": (job.get("match_score") or 0) / 20.0,  # 0-100 → 0-5
        "company_id": job.get("company_id"),
    }
    if body.notes is not None:
        new_row["notes"] = body.notes

    result = db.table("applications").insert(new_row).execute()
    row = (result.data or [None])[0] or new_row

    # Mirror onto the jobs row — same as the existing /applications POST.
    try:
        db.table("jobs").update({
            "status": "applied",
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", job_id).eq("user_id", str(user.id)).execute()
    except Exception as exc:
        logger.warning("jobs.applied_at backfill failed: %s", exc)

    return {"created": True, "updated": False, "application": row}


# ─── GET /workspace/{job_id}/resume.{format} ───────────────────────────────
@router.get("/{job_id}/resume.{fmt}")
def download_resume(
    job_id: int,
    fmt: str,
    user: User = Depends(get_current_user),
):
    """Server-side download proxy for resume artifacts.

    Phase 2 contract:
      • md   → returns the latest user_edited_md (or resume_md) inline
               as text/markdown. Always works if a build exists.
      • pdf  → 302 redirect to resume_pdf_url if present, else 501.
      • docx → 302 redirect to resume_docx_url if present, else 501.

    Phase 3 will add server-side rendering (markdown → PDF/DOCX) so that
    user edits flow through to PDF without re-running G2.
    """
    fmt_norm = (fmt or "").strip().lower()
    if fmt_norm not in ("md", "pdf", "docx"):
        raise HTTPException(status_code=400, detail=f"unsupported_format:{fmt_norm}")

    db = get_supabase()
    _get_job_for_user(db, job_id=job_id, user_id=user.id)
    build = _get_latest_resume_build(db, job_id=job_id, user_id=user.id)
    if not build:
        raise HTTPException(status_code=404, detail="no_resume_build_for_job")

    if fmt_norm == "md":
        md = build.get("user_edited_md") or build.get("resume_md") or ""
        if not md:
            raise HTTPException(status_code=404, detail="resume_markdown_empty")
        filename = f"resume-{job_id}.md"
        return PlainTextResponse(
            md,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if fmt_norm == "pdf":
        url = build.get("resume_pdf_url")
        if url:
            return RedirectResponse(url=url, status_code=302)
        raise HTTPException(
            status_code=501,
            detail={
                "code": "pdf_render_not_implemented",
                "message": (
                    "PDF rendering from markdown is wired in the next session. "
                    "Until then PDF is only available if the G2 build emitted a "
                    "Storage URL for it."
                ),
            },
        )

    # docx
    url = build.get("resume_docx_url")
    if url:
        return RedirectResponse(url=url, status_code=302)
    raise HTTPException(
        status_code=501,
        detail={
            "code": "docx_render_not_implemented",
            "message": (
                "DOCX rendering from markdown is wired in the next session. "
                "Until then DOCX is only available if the G2 build emitted a "
                "Storage URL for it."
            ),
        },
    )
