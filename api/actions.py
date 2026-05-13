"""
api/actions.py — /actions/today: the ranked action queue for the home page.

Answers ONE question for the user: "what should I do right now?"

Output contract (must match dashboard/src/lib/types/today.ts::TodayAction):
    {
      id: str,
      kind: 'resume_ready' | 'score_high_no_resume' | 'score_below_threshold'
            | 'stale_application' | 'persona_stale' | 'linkedin_post_due'
            | 'follow_up_urgent' | 'follow_up_overdue',
      title: str,
      subtitle: str | None,
      state: 'ready' | 'blocked' | 'stale' | 'pending',
      primary: { label: str, href?: str, onClick?: 'copy' | 'kickoff_g2'
                 | 'log_outcome' | 'approve_follow_up' },
      secondary: { label: str, href?: str } | None,
      meta: { score?: int, company?: str, date?: str } | None,
    }

Ranking (descending priority):
  0. follow_up_overdue      — past 2x cadence window: severe time decay
  1. follow_up_urgent       — past cadence window: every day = ~5% callback loss
  2. linkedin_post_due      — today's approved draft, top of the stack for visibility
  3. resume_ready           — resume URL exists, application not yet applied — ready to ship
  4. score_high_no_resume   — score ≥ 85, no resume yet — kick off G2
  5. stale_application      — applied 7+ days ago with no outcome logged
  6. score_below_threshold  — 80 ≤ score < 85 — surface but muted
  7. persona_stale          — last_synthesized > 14 days — refresh recommended

Follow-ups rank ABOVE resume builds because unanswered follow-ups have
time decay (every day without follow-up reduces callback probability ~5%
per career-ops data). Resume builds don't carry the same penalty — a
job posting that's still open today will still be open tomorrow.

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


def _phantom_company_names(user_id: UUID) -> frozenset[str]:
    """Return lowercase company name strings flagged as phantoms.

    Schema correction (2026-05-13): `is_phantom` lives on the `companies`
    table (added by BUG-013), NOT `company_personas`. Earlier draft of
    this function targeted the wrong table and would have failed at
    runtime with "column is_phantom does not exist".

    Post migration 028 there should be zero rows with is_phantom=TRUE
    (the migration deleted them), so this query returns an empty frozenset
    in steady state. The filter remains as defence-in-depth against any
    future bad scrape that inserts a phantom row.

    Returns frozenset for fast `lower(company) in <frozenset>` membership.
    Defensive — DB error returns empty (no filter applied).
    """
    try:
        rows = (
            get_supabase()
            .table("companies")
            .select("name")
            .eq("user_id", str(user_id))
            .eq("is_phantom", True)
            .execute()
            .data
        ) or []
        return frozenset((r.get("name") or "").strip().lower() for r in rows)
    except Exception as e:  # pragma: no cover — defensive
        import logging
        logging.getLogger(__name__).warning(
            "_phantom_company_names lookup failed (no filter applied): %s", e
        )
        return frozenset()


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
    letter_grade: Optional[str] = None,
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
    # Phase 2 §4.1 (G5) — letterGrade rides on action.meta so the
    # /today chip group can filter without a second fetch.
    if letter_grade:
        meta["letterGrade"] = letter_grade
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

    # 2026-05-12 (migration 012): linkedin_drafts now carries
    # `source_company_name` denormalised alongside `source_company_id`, so
    # we read it directly in a single round-trip. Pre-migration rows (and
    # rows where the writer didn't have a chosen company — e.g. timeless
    # 'industry_analysis' angles) have NULL source_company_name, in which
    # case we fall back to the companies-table lookup via the FK.
    #
    # Background: the original code assumed source_company_name existed
    # on linkedin_drafts and surfaced as a 400 from PostgREST (the column
    # is source_company_id, a uuid). PR #60 patched it with the lookup;
    # this migration eliminates the extra round-trip for new rows.
    rows = (
        db.table("linkedin_drafts")
        .select("id, hook, angle, source_company_id, source_company_name, scheduled_for, status")
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
    # Prefer the denormalised column; fall back to a FK lookup only if it's
    # NULL (legacy rows pre-migration 012). The fallback stays defensive —
    # any lookup failure just omits the company from the subtitle rather
    # than killing the whole card builder.
    company: Optional[str] = d.get("source_company_name") or None
    if not company:
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


def _build_job_actions(
    user_id: UUID,
    *,
    include_suspicious: bool = False,
) -> list[dict[str, Any]]:
    """resume_ready + score_high_no_resume + score_below_threshold.

    Tier 2 §4.3: rows with `legitimacy_tier='suspicious'` are filtered out
    by default. Pass `include_suspicious=True` (or `?include_suspicious=true`
    on /actions/today) to surface them for debugging — they still rank
    below legitimate/caution rows because match_score is unchanged.

    The filter is application-side (not SQL) so a NULL `legitimacy_tier`
    (job hasn't been scored yet — race window between scout insert and
    queue worker pickup) keeps surfacing. Only an explicit 'suspicious'
    verdict hides the row.
    """
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
    #
    # 2026-05-12 (Tier 2 §4.3): added legitimacy_tier + legitimacy_score
    # to the select list. Filter `legitimacy_tier='suspicious'` is
    # application-side so NULL (unscored) still renders.
    job_rows = (
        db.table("jobs")
        # Tier 2 select carries TWO new columns into /today:
        #   - letter_grade (Tier 2 §4.1, G5): A-F chip group on each card.
        #   - legitimacy_tier + legitimacy_score (Tier 2 §4.3, Legit v1):
        #     ghost-posting filter + per-card legitimacy badge.
        # Both are needed downstream; carrying them together keeps /today
        # to one round-trip.
        .select(
            "id, title, company, match_score, resume_generated_at, "
            "posting_closed_at, validation_status, confidence_score, "
            "validation_failed, letter_grade, legitimacy_tier, legitimacy_score"
        )
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

    if not include_suspicious:
        before = len(job_rows)
        job_rows = [
            r for r in job_rows
            if (r.get("legitimacy_tier") or "").lower() != "suspicious"
        ]
        dropped = before - len(job_rows)
        if dropped:
            logger.info(
                "actions: dropped %d suspicious job(s) from /today "
                "(use ?include_suspicious=true to surface)",
                dropped,
            )

    # Stream B (2026-05-13): filter phantom-company rows so /today never
    # surfaces fabricated names ("Adyen Careers", "SuperApp", "Merchant
    # Acquiring …", "68 Vacancies Apr 2026"). The is_phantom flag was
    # added by BUG-013; this consumes it. Migration 028 cleans the rows
    # out completely — this filter remains as runtime defense against
    # future phantom inserts that beat the scraper guards.
    phantoms = _phantom_company_names(user_id)
    if phantoms:
        before = len(job_rows)
        job_rows = [
            r for r in job_rows
            if (r.get("company") or "").strip().lower() not in phantoms
        ]
        dropped = before - len(job_rows)
        if dropped:
            logger.info(
                "actions: dropped %d phantom-company job(s) from /today "
                "(phantoms=%s)", dropped, sorted(phantoms),
            )

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
        # Phase 2 §4.1 — surface the G5 letter grade on every card so the
        # /today chip filter can narrow the visible list.
        letter_grade = j.get("letter_grade")

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
                letter_grade=letter_grade,
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
                letter_grade=letter_grade,
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
                letter_grade=letter_grade,
            ))
    return out


def _build_follow_up_actions(user_id: UUID) -> list[dict[str, Any]]:
    """URGENT + OVERDUE follow-ups from follow_up_cadence.

    Returns the latest cadence row per application (deduped client-side
    because PostgREST doesn't expose DISTINCT ON). Only rows with a
    non-empty draft_email are surfaced — a row without a draft means G6
    couldn't build one (no signal pool) and there's nothing actionable.

    The cards ride at the TOP of /today because unanswered follow-ups
    have time decay — every day without contact reduces callback
    probability by roughly 5% per career-ops data. This is a higher
    expected-value action than building another resume.
    """
    db = get_supabase()
    rows = (
        db.table("follow_up_cadence")
        .select(
            "id, application_id, current_status, urgency, "
            "follow_up_count, next_follow_up_date, draft_email"
        )
        .eq("user_id", str(user_id))
        .in_("urgency", ["URGENT", "OVERDUE"])
        .order("created_at", desc=True)
        .limit(50)
        .execute()
        .data
    ) or []

    # Dedupe to the latest row per application_id.
    seen: set[int] = set()
    latest: list[dict] = []
    for r in rows:
        aid = r.get("application_id")
        if aid is None or aid in seen:
            continue
        seen.add(aid)
        latest.append(r)

    # Filter out rows with no draft — they have nothing actionable.
    latest = [r for r in latest if (r.get("draft_email") or "").strip()]
    if not latest:
        return []

    # Pull application metadata in a single round-trip for the cards.
    app_ids = [r["application_id"] for r in latest]
    apps_by_id: dict[int, dict] = {}
    if app_ids:
        app_rows = (
            db.table("applications")
            .select("id, company, role, job_id")
            .in_("id", app_ids)
            .eq("user_id", str(user_id))
            .execute()
            .data
        ) or []
        apps_by_id = {a["id"]: a for a in app_rows}

    out: list[dict[str, Any]] = []
    for r in latest:
        urgency = r.get("urgency")
        kind = (
            "follow_up_overdue" if urgency == "OVERDUE" else "follow_up_urgent"
        )
        app_info = apps_by_id.get(r["application_id"]) or {}
        company = app_info.get("company") or ""
        role = (app_info.get("role") or "").strip()
        title = f"Follow up: {company}"
        if role:
            title = f"{title} — {role}"
        count = r.get("follow_up_count") or 0
        next_human = (r.get("next_follow_up_date") or "")[:10]
        subtitle_parts = [
            f"#{int(count) + 1} follow-up",
        ]
        if urgency == "OVERDUE":
            subtitle_parts.append("OVERDUE — every day costs ~5% callback rate")
        else:
            subtitle_parts.append("past cadence window")
        if next_human:
            subtitle_parts.append(f"next: {next_human}")
        out.append(_action(
            id=f"followup-{r['id']}",
            kind=kind,
            title=title,
            subtitle=" · ".join(subtitle_parts),
            state="ready",
            primary_label="Review & approve",
            primary_href=f"/applications/{app_info.get('job_id') or r['application_id']}/follow-ups/{r['id']}",
            primary_on_click="approve_follow_up",
            secondary_label="Skip",
            secondary_href=f"/applications/{app_info.get('job_id') or r['application_id']}/follow-ups/{r['id']}?action=skip",
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
#
# Phase 1.3 (G6): follow_up_overdue + follow_up_urgent slot ABOVE every
# other kind. Reasoning: unanswered follow-ups carry time decay (~5%
# callback probability lost per day past the cadence window). Other
# kinds — resume builds, persona refreshes — don't have the same
# perishability, so the math favours surfacing follow-ups first.
_KIND_PRIORITY = {
    "follow_up_overdue":      0,
    "follow_up_urgent":       1,
    "linkedin_post_due":      2,
    "resume_ready":           3,
    "score_high_no_resume":   4,
    "stale_application":      5,
    "score_below_threshold":  6,
    "persona_stale":          7,
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
    include_suspicious: bool = Query(
        default=False,
        description=(
            "Tier 2 §4.3: include jobs where legitimacy_tier='suspicious'. "
            "Default false (hidden) — useful for debugging the agent."
        ),
    ),
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

    # Phase 1.3: G6 follow-up queue. These cards rank above everything else
    # (see _KIND_PRIORITY) because of time-decay. Failure here MUST NOT
    # black out /today — the table may not exist yet on first deploy.
    try:
        actions.extend(_build_follow_up_actions(user_id))
    except Exception:
        logger.exception("follow_up builder failed")

    try:
        actions.extend(_build_job_actions(
            user_id, include_suspicious=include_suspicious,
        ))
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
