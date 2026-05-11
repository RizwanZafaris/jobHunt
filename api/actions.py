"""
api/actions.py — /actions/today: the ranked action queue for the home page.

Answers ONE question for the user: "what should I do right now?"

Output contract (must match dashboard/src/lib/types/today.ts::TodayAction):
    {
      id: str,
      kind: 'resume_ready' | 'score_high_no_resume' | 'score_below_threshold'
            | 'stale_application' | 'persona_stale' | 'linkedin_post_due',
      title: str,
      subtitle: str | None,
      state: 'ready' | 'blocked' | 'stale' | 'pending',
      primary: { label: str, href?: str, onClick?: 'copy' | 'kickoff_g2' | 'log_outcome' },
      secondary: { label: str, href?: str } | None,
      meta: { score?: int, company?: str, date?: str } | None,
    }

Ranking (descending priority):
  1. linkedin_post_due       — today's approved draft, top of the stack for visibility
  2. resume_ready            — resume URL exists, application not yet applied — ready to ship
  3. score_high_no_resume    — score ≥ 85, no resume yet — kick off G2
  4. stale_application       — applied 7+ days ago with no outcome logged
  5. score_below_threshold   — 80 ≤ score < 85 — surface but muted
  6. persona_stale           — last_synthesized > 14 days — refresh recommended

Returns top N (default 8) plus total counts for the "View all" badge.

Closed listings (jobs.posting_closed_at IS NOT NULL) are filtered out
across all kinds. The /today page never surfaces a dead URL.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from api.context import get_current_user
from api.users import User
from db.client import get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/actions", tags=["actions"])


# ─── tunables ─────────────────────────────────────────────────────────────
HIGH_SCORE_THRESHOLD = 85          # score_high_no_resume
MIN_SCORE_THRESHOLD = 80           # below this, jobs don't show on /today
STALE_APPLICATION_DAYS = 7
STALE_PERSONA_DAYS = 14
DEFAULT_TOP_N = 8


# ─── helpers ──────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_local() -> date:
    """Server's local 'today'. /today is per-user but all rows are UTC for now."""
    return _utcnow().date()


def _days_ago(n: int) -> str:
    return (_utcnow() - timedelta(days=n)).isoformat()


def _action(
    *,
    id: str,
    kind: str,
    title: str,
    state: str,
    primary_label: str,
    primary_href: Optional[str] = None,
    primary_on_click: Optional[str] = None,
    subtitle: Optional[str] = None,
    secondary_label: Optional[str] = None,
    secondary_href: Optional[str] = None,
    score: Optional[int] = None,
    company: Optional[str] = None,
    date_str: Optional[str] = None,
) -> dict[str, Any]:
    """Build one TodayAction matching the TS shape exactly."""
    primary: dict[str, Any] = {"label": primary_label}
    if primary_href is not None:
        primary["href"] = primary_href
    if primary_on_click is not None:
        primary["onClick"] = primary_on_click

    out: dict[str, Any] = {
        "id": id,
        "kind": kind,
        "title": title,
        "state": state,
        "primary": primary,
    }
    if subtitle:
        out["subtitle"] = subtitle
    if secondary_label:
        secondary: dict[str, Any] = {"label": secondary_label}
        if secondary_href:
            secondary["href"] = secondary_href
        out["secondary"] = secondary

    meta: dict[str, Any] = {}
    if score is not None:
        meta["score"] = int(score)
    if company:
        meta["company"] = company
    if date_str:
        meta["date"] = date_str
    if meta:
        out["meta"] = meta
    return out


# ─── kind builders ────────────────────────────────────────────────────────
def _build_linkedin_post_due(user_id: UUID) -> Optional[dict[str, Any]]:
    """Today's approved LinkedIn draft (status='approved' AND scheduled_for is today)."""
    db = get_supabase()
    today = _today_local()
    start = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc).isoformat()
    end = (datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)).isoformat()

    # 2026-05-12 fix: linkedin_drafts has source_company_id (uuid) not
    # source_company_name (the column name the code assumed never existed
    # on this schema — surfaced as 400 from postgrest, breaking the
    # linkedin_post_due card builder on /actions/today). Resolve the
    # company name from the FK via a second lookup so the subtitle stays
    # informative without a JOIN (PostgREST joins via select syntax are
    # brittle here because companies.name is RLS-scoped per user).
    rows = (
        db.table("linkedin_drafts")
        .select("id, hook, angle, source_company_id, scheduled_for, status")
        .eq("user_id", str(user_id))
        .in_("status", ["approved", "scheduled"])
        .gte("scheduled_for", start)
        .lt("scheduled_for", end)
        .order("scheduled_for", desc=False)
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        return None
    d = rows[0]
    hook = (d.get("hook") or "").splitlines()[0] if d.get("hook") else "Today's draft"
    title = hook[:120].strip() or "Today's LinkedIn draft is ready"
    angle = (d.get("angle") or "").replace("_", " ")
    # Resolve company name from source_company_id with a defensive try —
    # if the lookup fails we just omit the company from the subtitle
    # rather than killing the whole card builder again.
    company: Optional[str] = None
    source_id = d.get("source_company_id")
    if source_id:
        try:
            crow = (
                db.table("companies")
                .select("name")
                .eq("id", str(source_id))
                .limit(1)
                .execute()
                .data
            ) or []
            if crow:
                company = crow[0].get("name")
        except Exception:
            company = None
    parts = []
    if angle:
        parts.append(angle)
    if company:
        parts.append(company)
    subtitle = " · ".join(parts) if parts else None
    return _action(
        id=f"linkedin-{d['id']}",
        kind="linkedin_post_due",
        title=title,
        subtitle=subtitle,
        state="pending",
        primary_label="Copy & post on LinkedIn",
        primary_on_click="copy",
        secondary_label="View draft",
        secondary_href=f"/linkedin?draft={d['id']}",
    )


def _build_job_actions(user_id: UUID) -> list[dict[str, Any]]:
    """resume_ready + score_high_no_resume + score_below_threshold."""
    db = get_supabase()

    # Pull candidate jobs once: open listings, score >= MIN, ordered by score.
    #
    # 2026-05-12: tightened to require JobScout v2 validation. Legacy v1
    # rows (confidence_score IS NULL) and validation-failed candidates
    # (validation_failed IS NOT NULL) are excluded at the query layer.
    # Migration 011 added the columns; the companion SQL soft-closed
    # legacy rows on the same day, so this filter and the
    # posting_closed_at filter form defence-in-depth.
    # See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §C.
    job_rows = (
        db.table("jobs")
        .select("id, title, company, match_score, resume_generated_at, posting_closed_at, validation_status, confidence_score, validation_failed")
        .eq("user_id", str(user_id))
        .is_("posting_closed_at", None)
        .is_("validation_failed", None)
        .not_.is_("confidence_score", None)
        .gte("confidence_score", 50)
        .gte("match_score", MIN_SCORE_THRESHOLD)
        .order("confidence_score", desc=True)
        .order("match_score", desc=True)
        .limit(50)
        .execute()
    ).data or []
    if not job_rows:
        return []

    job_ids = [int(r["id"]) for r in job_rows if r.get("id") is not None]

    # Find applications for those jobs that are already submitted (status = applied/...).
    applied_status_set = {"applied", "interviewing", "offered", "rejected", "withdrawn"}
    applied_job_ids: set[int] = set()
    if job_ids:
        app_rows = (
            db.table("applications")
            .select("job_id, status")
            .eq("user_id", str(user_id))
            .in_("job_id", job_ids)
            .execute()
        ).data or []
        for ar in app_rows:
            if ar.get("status") in applied_status_set and ar.get("job_id") is not None:
                applied_job_ids.add(int(ar["job_id"]))

    out: list[dict[str, Any]] = []
    for j in job_rows:
        job_id = int(j["id"])
        if job_id in applied_job_ids:
            continue  # already moved on
        score = int(j.get("match_score") or 0)
        company = j.get("company") or ""
        title = (j.get("title") or "").strip()
        has_resume = bool(j.get("resume_generated_at"))
        score_label = f"Score {score}/100"
        card_title = f"{company} — {title}" if title else company or "Job"
        workspace_href = f"/applications/{job_id}/workspace"

        if has_resume:
            out.append(_action(
                id=f"job-{job_id}-ready",
                kind="resume_ready",
                title=card_title,
                subtitle=f"{score_label} · resume tailored, ready to apply",
                state="ready",
                primary_label="Start application process",
                primary_href=workspace_href,
                secondary_label="View resume",
                secondary_href=f"/applications/{job_id}/workspace?tab=resume",
                score=score,
                company=company,
            ))
        elif score >= HIGH_SCORE_THRESHOLD:
            out.append(_action(
                id=f"job-{job_id}-high",
                kind="score_high_no_resume",
                title=card_title,
                subtitle=f"{score_label} · resume not built — kick off G2",
                state="ready",
                primary_label="Start application process",
                primary_href=workspace_href,
                primary_on_click="kickoff_g2",
                score=score,
                company=company,
            ))
        else:
            # 80-84 — surface but muted.
            out.append(_action(
                id=f"job-{job_id}-mid",
                kind="score_below_threshold",
                title=card_title,
                subtitle=f"{score_label} · below 85 threshold — review fit before generating",
                state="blocked",
                primary_label="Review",
                primary_href=workspace_href,
                score=score,
                company=company,
            ))
    return out


def _build_stale_applications(user_id: UUID) -> list[dict[str, Any]]:
    """Applications still 'applied' 7+ days ago with no outcome logged."""
    db = get_supabase()
    cutoff_date = (date.today() - timedelta(days=STALE_APPLICATION_DAYS)).isoformat()
    rows = (
        db.table("applications")
        .select("id, company, role, applied_date, status, job_id")
        .eq("user_id", str(user_id))
        .eq("status", "applied")
        .lt("applied_date", cutoff_date)
        .order("applied_date", desc=False)
        .limit(10)
        .execute()
    ).data or []
    out: list[dict[str, Any]] = []
    for a in rows:
        applied = a.get("applied_date") or "unknown"
        company = a.get("company") or ""
        role = (a.get("role") or "").strip()
        out.append(_action(
            id=f"app-{a['id']}-stale",
            kind="stale_application",
            title=f"{company} — {role}" if role else company or "Application",
            subtitle=f"Applied {applied} · log outcome to keep persona learning",
            state="stale",
            primary_label="Log outcome",
            primary_on_click="log_outcome",
            primary_href=f"/applications/{a['id']}",
            company=company,
            date_str=applied,
        ))
    return out


def _build_stale_personas(user_id: UUID) -> list[dict[str, Any]]:
    """Personas where last_synthesized_at is older than 14 days."""
    db = get_supabase()
    rows = (
        db.table("company_personas")
        .select("id, company_name, last_synthesized_at, persona_version")
        .eq("user_id", str(user_id))
        .lt("last_synthesized_at", _days_ago(STALE_PERSONA_DAYS))
        .order("last_synthesized_at", desc=False)
        .limit(3)
        .execute()
    ).data or []
    out: list[dict[str, Any]] = []
    for p in rows:
        company = p.get("company_name") or "persona"
        last = (p.get("last_synthesized_at") or "")[:10]
        version = p.get("persona_version")
        out.append(_action(
            id=f"persona-{p['id']}-stale",
            kind="persona_stale",
            title=f"Refresh {company} persona",
            subtitle=f"v{version} last synthesized {last} — news may have moved",
            state="stale",
            primary_label="Refresh news",
            primary_href=f"/insights?tab=personas&company={company}",
            company=company,
            date_str=last,
        ))
    return out


# ─── ranking ──────────────────────────────────────────────────────────────
# Lower priority value = appears first in the stack.
_KIND_PRIORITY = {
    "linkedin_post_due":      0,
    "resume_ready":           1,
    "score_high_no_resume":   2,
    "stale_application":      3,
    "score_below_threshold":  4,
    "persona_stale":          5,
}


def _rank(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort by (kind priority, descending score for jobs, ascending date for stale)."""
    def key(a: dict[str, Any]) -> tuple:
        kind = a.get("kind", "")
        meta = a.get("meta") or {}
        # Higher score → earlier within the same kind for job kinds.
        score_key = -int(meta.get("score") or 0)
        # Older date → earlier within stale_application.
        date_key = meta.get("date") or ""
        return (_KIND_PRIORITY.get(kind, 99), score_key, date_key)
    return sorted(actions, key=key)


# ─── route ────────────────────────────────────────────────────────────────
@router.get("/today")
def get_today_actions(
    limit: int = Query(default=DEFAULT_TOP_N, ge=1, le=20),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the ranked action queue for /today."""
    user_id = user.id
    actions: list[dict[str, Any]] = []

    # Each builder is independently safe — failures shouldn't black out /today.
    try:
        post = _build_linkedin_post_due(user_id)
        if post is not None:
            actions.append(post)
    except Exception:
        logger.exception("linkedin_post_due builder failed")

    try:
        actions.extend(_build_job_actions(user_id))
    except Exception:
        logger.exception("job actions builder failed")

    try:
        actions.extend(_build_stale_applications(user_id))
    except Exception:
        logger.exception("stale_application builder failed")

    try:
        actions.extend(_build_stale_personas(user_id))
    except Exception:
        logger.exception("persona_stale builder failed")

    ranked = _rank(actions)
    top = ranked[:limit]

    counts: dict[str, int] = {}
    for a in ranked:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1

    return {
        "actions": top,
        "total": len(ranked),
        "counts": counts,
        "generated_at": _utcnow().isoformat(),
    }
