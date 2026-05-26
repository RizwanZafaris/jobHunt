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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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

# ─── stale-card fix tunables (2026-05-26) ─────────────────────────────────
# Root cause being addressed: the Adyen evergreen-posting bug — same job
# card persists on /today for weeks because nothing decays it.
#
# Three independent forces now push old cards down/off /today:
#   1. Age penalty       — _rank() reads job created_at, penalizes by day
#   2. Surface penalty   — _rank() reads jobs.surface_count (migration 037)
#   3. User dismissal    — job_card_dismissals filter (migration 036)
#
# Defaults chosen so a 95-score, 14-day-old, surfaced-7-times card sinks
# below a fresh 87-score card. Tune empirically once we have telemetry.
CARD_AGE_PENALTY_PER_DAY = 1       # subtracts 1 from effective score per day
CARD_AGE_PENALTY_CAP_DAYS = 30     # don't penalize beyond 30 days
CARD_SURFACE_PENALTY_PER_VIEW = 2  # subtracts 2 from effective score per surface after the first
CARD_SURFACE_PENALTY_CAP = 30      # don't penalize beyond 15 surfaces
CARD_DORMANT_THRESHOLD = 7         # >=7 surfaces with no action = dormant (hidden unless ?show_all=true)


# ─── helpers ──────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today_local() -> date:
    """Server's local 'today'. /today is per-user but all rows are UTC for now."""
    return _utcnow().date()


def _phantom_company_names(user_id: UUID) -> frozenset[str]:
    """Return lowercase company_name strings flagged as phantoms.

    Used to filter /today + /applications surfaces so the user never
    sees fabricated companies (e.g. "Adyen Careers", "SuperApp",
    "Merchant Acquiring ...") on action cards even before the cleanup
    migration 028 runs.

    Returns frozenset for fast `lower(company) in <frozenset>` membership.
    Defensive — DB error returns empty (no filter applied).
    """
    try:
        rows = (
            get_supabase()
            .table("company_personas")
            .select("company_name")
            .eq("user_id", str(user_id))
            .eq("is_phantom", True)
            .execute()
            .data
        ) or []
        return frozenset((r.get("company_name") or "").strip().lower() for r in rows)
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
    # 2026-05-26 stale-card fix — these flow into meta for _rank() to read
    job_id: Optional[int] = None,         # enables dismiss action
    created_at: Optional[str] = None,     # age penalty input
    surface_count: Optional[int] = None,  # lifecycle penalty input
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
    # 2026-05-26 stale-card fix — meta fields consumed by _rank() and
    # by the dashboard dismiss button.
    if job_id is not None:
        meta["jobId"] = int(job_id)
    if created_at:
        meta["createdAt"] = created_at
    if surface_count is not None:
        meta["surfaceCount"] = int(surface_count)
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


def _active_dismissed_job_ids(user_id: UUID) -> set[int]:
    """Job IDs the user has dismissed (and not yet snooze-expired).

    Reads job_card_dismissals (migration 036). Defensive — DB error
    returns empty set so /today never blackouts because of this filter.

    Filter logic:
      • snoozed_until IS NULL          → permanent dismissal, always filter
      • snoozed_until >  now()         → snooze still active, filter
      • snoozed_until <= now()         → snooze expired, do NOT filter
                                         (re-surface the card to give it
                                         another shot)
    """
    try:
        rows = (
            get_supabase()
            .table("job_card_dismissals")
            .select("job_id, snoozed_until")
            .eq("user_id", str(user_id))
            .execute()
            .data
        ) or []
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "dismissals lookup failed (no filter applied): %s", e
        )
        return set()

    now_iso = _utcnow().isoformat()
    out: set[int] = set()
    for r in rows:
        snooze = r.get("snoozed_until")
        # Active filter: NULL (permanent) OR snooze hasn't elapsed yet.
        if snooze is None or snooze > now_iso:
            jid = r.get("job_id")
            if jid is not None:
                out.add(int(jid))
    return out


def _bump_surface_counters(user_id: UUID, job_ids: list[int]) -> None:
    """Increment surface_count + stamp last_surfaced_at for shown jobs.

    Called AFTER /today returns its card list. Best-effort fire-and-forget:
    if the bump fails, the user still got their cards and the next bump
    will catch up. Tracking is for ranking, not correctness.

    Uses an RPC-style update (one round-trip per call). At top-N=8 this
    is 8 UPDATEs; acceptable. Could be optimized to a single CASE WHEN
    if it becomes a bottleneck.

    Migration 037 added the columns + default 0.
    """
    if not job_ids:
        return
    db = get_supabase()
    now_iso = _utcnow().isoformat()
    try:
        # Postgres-side increment via RPC would be cleaner; PostgREST
        # doesn't expose a stored procedure here, so we read-modify-write.
        # Race-condition tolerance: if two /today calls fire concurrently,
        # we might under-count by 1. That's a non-issue for ranking.
        rows = (
            db.table("jobs")
            .select("id, surface_count, first_surfaced_at")
            .eq("user_id", str(user_id))
            .in_("id", job_ids)
            .execute()
            .data
        ) or []
        for r in rows:
            jid = r.get("id")
            if jid is None:
                continue
            current = int(r.get("surface_count") or 0)
            payload: dict[str, Any] = {
                "surface_count": current + 1,
                "last_surfaced_at": now_iso,
            }
            if not r.get("first_surfaced_at"):
                payload["first_surfaced_at"] = now_iso
            db.table("jobs").update(payload).eq("id", jid).execute()
    except Exception as e:  # pragma: no cover — defensive
        logger.debug("surface counter bump failed (non-fatal): %s", e)


def _build_job_actions(
    user_id: UUID,
    *,
    include_suspicious: bool = False,
    show_dormant: bool = False,
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
            "validation_failed, letter_grade, legitimacy_tier, legitimacy_score, "
            # 2026-05-26 stale-card fix: surface created_at + surface_count
            # so _rank() can apply age + lifecycle penalties (migration 037).
            "created_at, first_surfaced_at, last_surfaced_at, surface_count"
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

    # 2026-05-26 stale-card Fix 2: filter user-dismissed/snoozed jobs.
    # Read once, filter all kinds.
    dismissed_ids = _active_dismissed_job_ids(user_id)
    if dismissed_ids:
        before = len(job_rows)
        job_rows = [r for r in job_rows if int(r["id"]) not in dismissed_ids]
        dropped = before - len(job_rows)
        if dropped:
            logger.info(
                "actions: dropped %d dismissed/snoozed job(s) from /today",
                dropped,
            )

    # 2026-05-26 stale-card Fix 3: filter dormant cards (surface_count >= threshold)
    # unless explicitly requested via ?show_all=true. Dormant = user has
    # been shown the card N times without acting, system stops surfacing.
    if not show_dormant:
        before = len(job_rows)
        job_rows = [
            r for r in job_rows
            if int(r.get("surface_count") or 0) < CARD_DORMANT_THRESHOLD
        ]
        dropped = before - len(job_rows)
        if dropped:
            logger.info(
                "actions: hid %d dormant job card(s) (surface_count >= %d). "
                "Use ?show_all=true to surface.",
                dropped, CARD_DORMANT_THRESHOLD,
            )

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

        # 2026-05-26: capture the stale-card fix inputs once per job
        created_at = j.get("created_at")
        surface_count = int(j.get("surface_count") or 0)

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
                job_id=job_id,
                created_at=created_at,
                surface_count=surface_count,
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
                job_id=job_id,
                created_at=created_at,
                surface_count=surface_count,
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
                job_id=job_id,
                created_at=created_at,
                surface_count=surface_count,
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


def _effective_score(meta: dict[str, Any]) -> int:
    """Compute an effective ranking score with stale-card penalties applied.

    Fix 1 (age penalty): each day old subtracts CARD_AGE_PENALTY_PER_DAY,
    capped at CARD_AGE_PENALTY_CAP_DAYS.

    Fix 3 (surface penalty): each prior surface beyond the first subtracts
    CARD_SURFACE_PENALTY_PER_VIEW, capped at CARD_SURFACE_PENALTY_CAP views.

    Returns the effective score (negative of which is the rank key — higher
    effective = earlier in queue).

    Example for a 95-score Adyen card after 14 days + 10 surfaces:
        base = 95
        age_penalty     = min(14, 30) * 1 = 14
        surface_penalty = min(max(10-1, 0), 30) * 2 = 18
        effective       = 95 - 14 - 18 = 63
    A fresh 88-score job:
        effective = 88 - 0 - 0 = 88   (wins)
    """
    base = int(meta.get("score") or 0)

    age_penalty = 0
    created_at = meta.get("createdAt")
    if created_at:
        try:
            created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            days_old = (_utcnow() - created_dt).days
            age_penalty = min(max(days_old, 0), CARD_AGE_PENALTY_CAP_DAYS) * CARD_AGE_PENALTY_PER_DAY
        except (ValueError, AttributeError):
            pass  # malformed timestamp — no penalty rather than crashing the page

    surface_penalty = 0
    sc = meta.get("surfaceCount")
    if sc is not None:
        # First surface is free; subsequent surfaces accrue penalty.
        extra_surfaces = max(int(sc) - 1, 0)
        surface_penalty = min(extra_surfaces, CARD_SURFACE_PENALTY_CAP) * CARD_SURFACE_PENALTY_PER_VIEW

    return base - age_penalty - surface_penalty


def _rank(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable sort by (kind priority, descending effective score, ascending date).

    2026-05-26 stale-card fix: now uses _effective_score() which applies
    age + surface-count penalties. Old, repeatedly-shown cards naturally
    sink below fresh ones even at higher raw match_score.

    Non-job kinds (linkedin_post_due, persona_stale, follow_up_*) carry
    no createdAt/surfaceCount in meta, so _effective_score returns their
    raw score (0 if absent) — they continue to rank purely by KIND_PRIORITY.
    """
    def key(a: dict[str, Any]) -> tuple:
        kind = a.get("kind", "")
        meta = a.get("meta") or {}
        # Higher effective score → earlier within the same kind.
        score_key = -_effective_score(meta)
        # Older date → earlier within stale_application.
        date_key = meta.get("date") or ""
        return (_KIND_PRIORITY.get(kind, 99), score_key, date_key)
    return sorted(actions, key=key)


# ─── Stream D builders + routes (2026-05-13) ─────────────────────────────


def _build_post_of_the_day(user_id: UUID) -> Optional[dict[str, Any]]:
    """The single most-recent LinkedIn draft for the user.

    User feedback: "Today page shows section is just shows old job
    there is no today's linkedin post that should be there." The
    existing `_build_linkedin_post_due` only fires when a draft is
    BOTH status='approved' AND scheduled_for=today — too strict for
    early-stage users. This new builder is permissive: it surfaces
    the latest draft regardless of status so the user has something
    visible to act on.

    Preference order:
      1. status IN ('approved','scheduled') AND scheduled_for=today
         (the actual "due today" case — same as _build_linkedin_post_due)
      2. status='draft' (latest, pending user review)
      3. status='approved' but not scheduled yet
    """
    db = get_supabase()

    # First try the strict "today" lookup
    strict = _build_linkedin_post_due(user_id)
    if strict:
        return strict

    # Otherwise fall back to the latest draft
    try:
        rows = (
            db.table("linkedin_drafts")
            .select("id, hook, body, angle, source_company_name, status, scheduled_for, created_at")
            .eq("user_id", str(user_id))
            .in_("status", ["draft", "pending_review", "approved"])
            .order("created_at", desc=True)
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception:
        logger.exception("post-of-the-day fallback lookup failed")
        return None

    if not rows:
        return None
    d = rows[0]
    return {
        "id": str(d["id"]),
        "hook": d.get("hook") or "",
        "body_excerpt": (d.get("body") or "")[:280],
        "angle": d.get("angle"),
        "company": d.get("source_company_name"),
        "status": d.get("status") or "draft",
        "scheduled_for": d.get("scheduled_for"),
        "created_at": d.get("created_at"),
    }


def _build_incoming_jobs(user_id: UUID, limit: int = 10) -> list[dict[str, Any]]:
    """Jobs with score >= 85 + no applications row + not closed/phantom.

    Used by /applications "Incoming" lane so the user sees their highest-
    quality discoveries inline on the pipeline board.
    """
    db = get_supabase()

    # Phantom names to exclude. Schema correction (2026-05-13):
    # is_phantom lives on `companies` (added by BUG-013), NOT
    # company_personas. Post migration 028 this returns empty in
    # steady state; kept as defence-in-depth.
    try:
        ph_rows = (
            db.table("companies")
            .select("name")
            .eq("user_id", str(user_id))
            .eq("is_phantom", True)
            .execute()
            .data
        ) or []
        phantoms = frozenset((r.get("name") or "").strip().lower() for r in ph_rows)
    except Exception:
        phantoms = frozenset()

    try:
        rows = (
            db.table("jobs")
            .select(
                "id, title, company, match_score, letter_grade, archetype, "
                "legitimacy_tier, created_at"
            )
            .eq("user_id", str(user_id))
            .is_("posting_closed_at", None)
            .is_("validation_failed", None)
            .gte("match_score", HIGH_SCORE_THRESHOLD)
            .order("match_score", desc=True)
            .limit(limit * 3)  # over-fetch then post-filter
            .execute()
            .data
        ) or []
    except Exception:
        logger.exception("incoming jobs query failed")
        return []

    job_ids = [int(r["id"]) for r in rows if r.get("id") is not None]
    applied_ids: set[int] = set()
    if job_ids:
        try:
            app_rows = (
                db.table("applications")
                .select("job_id")
                .eq("user_id", str(user_id))
                .in_("job_id", job_ids)
                .execute()
                .data
            ) or []
            for ar in app_rows:
                if ar.get("job_id") is not None:
                    applied_ids.add(int(ar["job_id"]))
        except Exception:
            logger.exception("incoming applications lookup failed")

    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("id") in applied_ids or int(r["id"]) in applied_ids:
            continue
        company = (r.get("company") or "").strip()
        if company.lower() in phantoms:
            continue
        if (r.get("legitimacy_tier") or "").lower() == "suspicious":
            continue
        out.append({
            "job_id": int(r["id"]),
            "title": r.get("title") or "",
            "company": company,
            "score": int(r.get("match_score") or 0),
            "letter_grade": r.get("letter_grade"),
            "archetype": r.get("archetype"),
            "created_at": r.get("created_at"),
        })
        if len(out) >= limit:
            break
    return out


@router.get("/today/linkedin-post")
def get_post_of_the_day(
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Latest LinkedIn draft for the user, regardless of status.
    Surfaces on /today as the post-of-the-day card."""
    post = _build_post_of_the_day(user.id)
    return {"post": post}


@router.get("/incoming")
def get_incoming_jobs(
    limit: int = Query(default=10, ge=1, le=50),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Jobs with score >= 85 that the user hasn't applied to yet.
    Renders as the leftmost lane on /applications."""
    return {"jobs": _build_incoming_jobs(user.id, limit=limit)}


# ─── existing /today route ─────────────────────────────────────────────────
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
    show_all: bool = Query(
        default=False,
        description=(
            "2026-05-26 stale-card fix: bypass dormant filter "
            "(jobs.surface_count >= CARD_DORMANT_THRESHOLD). Default false. "
            "Used by 'Show all' toggle in the dashboard."
        ),
    ),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the ranked action queue for /today.

    2026-05-26: three stale-card forces now decay old cards:
      1. _rank() applies age + surface penalties (Fix 1 + Fix 3)
      2. _build_job_actions() filters dismissed/snoozed (Fix 2)
      3. _build_job_actions() filters dormant unless show_all=true (Fix 3)
    After the response is built, surface counters bump (fire-and-forget).
    """
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
            user_id,
            include_suspicious=include_suspicious,
            show_dormant=show_all,
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

    # 2026-05-26: bump surface_count for every job card the user is about
    # to see. Best-effort; non-fatal failure. This is what powers Fix 3's
    # lifecycle penalty on subsequent /today calls.
    surfaced_job_ids: list[int] = []
    for a in top:
        meta = a.get("meta") or {}
        jid = meta.get("jobId")
        if jid is not None and a.get("kind", "").startswith(("score_", "resume_")):
            try:
                surfaced_job_ids.append(int(jid))
            except (TypeError, ValueError):
                continue
    if surfaced_job_ids:
        _bump_surface_counters(user_id, surfaced_job_ids)

    return {
        "actions": top,
        "total": len(ranked),
        "counts": counts,
        "generated_at": _utcnow().isoformat(),
    }


# ─── Dismiss / snooze (Fix 2 — 2026-05-26) ────────────────────────────────


class DismissBody(BaseModel):
    """Payload for POST /actions/today/dismiss/{job_id}.

    snooze_days: None = permanent dismissal (the card is hidden forever)
                 >0   = re-surface after N days
    reason: optional categorical reason for analytics
    notes:  optional free-form text
    """
    snooze_days: Optional[int] = Field(
        default=None, ge=1, le=365,
        description="1-365 to snooze, None/omit for permanent dismissal"
    )
    reason: Optional[str] = Field(
        default=None,
        description="Categorical: not_interested | maybe_later | wrong_seniority | "
                    "wrong_location | wrong_comp | closed_already | other"
    )
    notes: Optional[str] = Field(default=None, max_length=1000)


_VALID_REASONS = {
    "not_interested", "maybe_later", "wrong_seniority",
    "wrong_location", "wrong_comp", "closed_already", "other",
}


@router.post("/today/dismiss/{job_id}")
def dismiss_today_card(
    job_id: int,
    body: DismissBody,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Hide a job card from /today.

    Idempotent — calling twice on the same job_id updates the existing
    dismissal row rather than creating a duplicate (uq_user_job unique).

    Permanent dismissal: body = { } or { reason: "not_interested" }
    Snooze 7 days:       body = { snooze_days: 7, reason: "maybe_later" }
    """
    if body.reason and body.reason not in _VALID_REASONS:
        raise HTTPException(
            status_code=422,
            detail=f"reason must be one of: {sorted(_VALID_REASONS)}",
        )

    # Verify the job exists and belongs to the user (defense in depth — RLS
    # would also block, but a 404 is clearer than a silent insert failure).
    job_check = (
        get_supabase()
        .table("jobs")
        .select("id")
        .eq("id", job_id)
        .eq("user_id", str(user.id))
        .limit(1)
        .execute()
        .data
    ) or []
    if not job_check:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")

    snoozed_until = None
    if body.snooze_days:
        snoozed_until = (_utcnow() + timedelta(days=body.snooze_days)).isoformat()

    payload = {
        "user_id": str(user.id),
        "job_id": job_id,
        "dismissed_at": _utcnow().isoformat(),
        "snoozed_until": snoozed_until,
        "reason": body.reason,
        "notes": body.notes,
    }

    # Upsert on (user_id, job_id) — see uq_user_job in migration 036.
    try:
        get_supabase().table("job_card_dismissals").upsert(
            payload, on_conflict="user_id,job_id"
        ).execute()
    except Exception as e:
        logger.exception("dismiss upsert failed for job %s", job_id)
        raise HTTPException(status_code=500, detail=f"dismissal write failed: {e}")

    return {
        "ok": True,
        "job_id": job_id,
        "dismissed": True,
        "snoozed_until": snoozed_until,
    }


@router.delete("/today/dismiss/{job_id}")
def undo_dismiss_today_card(
    job_id: int,
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Undo dismissal — the job card will re-surface on next /today.

    Useful for the "Show dismissed" tab + per-card "Undo" button.
    """
    try:
        get_supabase().table("job_card_dismissals").delete().eq(
            "user_id", str(user.id)
        ).eq("job_id", job_id).execute()
    except Exception as e:
        logger.exception("dismiss delete failed for job %s", job_id)
        raise HTTPException(status_code=500, detail=f"undo failed: {e}")
    return {"ok": True, "job_id": job_id, "restored": True}


@router.get("/today/dismissed")
def list_dismissed_cards(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """List the user's active dismissals + snoozed-but-not-expired rows.

    Powers the "Manage dismissed" tab in the dashboard so the user can
    undo individual dismissals without remembering job IDs.
    """
    try:
        rows = (
            get_supabase()
            .table("job_card_dismissals")
            .select("job_id, dismissed_at, snoozed_until, reason, notes")
            .eq("user_id", str(user.id))
            .order("dismissed_at", desc=True)
            .limit(limit)
            .execute()
            .data
        ) or []
    except Exception as e:
        logger.exception("list dismissed failed")
        raise HTTPException(status_code=500, detail=f"list failed: {e}")

    # Enrich each row with job title/company so the UI doesn't need a join.
    job_ids = [int(r["job_id"]) for r in rows if r.get("job_id") is not None]
    job_meta: dict[int, dict] = {}
    if job_ids:
        try:
            job_rows = (
                get_supabase()
                .table("jobs")
                .select("id, title, company, posting_closed_at")
                .eq("user_id", str(user.id))
                .in_("id", job_ids)
                .execute()
                .data
            ) or []
            job_meta = {int(j["id"]): j for j in job_rows}
        except Exception:
            logger.exception("dismissed jobs metadata lookup failed (continuing)")

    now_iso = _utcnow().isoformat()
    out = []
    for r in rows:
        jid = int(r["job_id"])
        j = job_meta.get(jid) or {}
        snooze = r.get("snoozed_until")
        if snooze is None:
            status = "dismissed_permanently"
        elif snooze > now_iso:
            status = "snoozed_active"
        else:
            status = "snooze_expired"
        out.append({
            "job_id": jid,
            "title": j.get("title"),
            "company": j.get("company"),
            "job_closed": j.get("posting_closed_at") is not None,
            "dismissed_at": r.get("dismissed_at"),
            "snoozed_until": snooze,
            "reason": r.get("reason"),
            "notes": r.get("notes"),
            "status": status,
        })
    return {"dismissals": out, "total": len(out)}
