"""
agents/g6_io.py — All Supabase reads/writes the G6 graph needs.

Centralised so nodes stay LLM-focused. Every public function here
corresponds to one entry-point or persist step in the graph.

Tables touched (all in public schema):
    READ   applications, profiles, company_personas, follow_up_cadence,
           linkedin_drafts, company_knowledge (via search_company_knowledge RPC)
    WRITE  follow_up_cadence, applications (status flip to 'withdrawn' on COLD)

The high-level orchestrator `run_g6_for_application(application_id)` is the
public entry point for the cron, the API approve/skip/regenerate endpoints,
and tests. It builds the initial FollowUpState, compiles the graph, runs
it, and returns the final state as a plain dict.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def _resolve_user_id(explicit: Optional[str] = None) -> str:
    """Multi-tenant-safe user_id resolution.

    Matches the convention used by g2_io.create_resume_build — env override
    so the cron + worker code paths don't have to plumb user_id through
    every call site.
    """
    if explicit:
        return explicit
    return os.environ.get("RIZWAN_USER_ID", _DEFAULT_USER_ID)


# ═════════════════════════════════════════════════════════════════════════
# Reads
# ═════════════════════════════════════════════════════════════════════════
def load_application(application_id: int) -> dict:
    """Fetch a single applications row by id."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("applications")
        .select("*")
        .eq("id", application_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise RuntimeError(f"Application {application_id} not found")
    return rows[0]


def load_last_cadence_row(application_id: int) -> Optional[dict]:
    """Latest follow_up_cadence row for this application, or None."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("follow_up_cadence")
        .select("*")
        .eq("application_id", application_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def load_active_applications(user_id: Optional[str] = None) -> list[dict]:
    """Applications that are candidates for follow-up.

    Excludes pre-apply rows (status IN new/researched/resume_ready) and
    terminal rows (rejected, withdrawn). Returns the rows the daily cron
    iterates over.
    """
    from db.client import get_supabase
    user_id = _resolve_user_id(user_id)
    rows = (
        get_supabase()
        .table("applications")
        .select("*")
        .eq("user_id", user_id)
        .in_("status", ["applied", "interviewing", "offered"])
        .execute()
        .data
    ) or []
    return rows


def load_company_persona(user_id: str, company_name: str) -> Optional[dict]:
    """Row from company_personas matching (user_id, canonicalised name)."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("company_personas")
        .select("*")
        .eq("user_id", user_id)
        .eq("company_name", company_name)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


async def load_recent_company_knowledge(
    company_name: str,
    query: str,
    *,
    match_count: int = 5,
    max_age_days: int = 7,
) -> list[dict]:
    """pgvector RAG against company_knowledge, optionally filtered to the
    last N days so the follow-up signal is genuinely fresh news.

    Falls back to "no time filter" if the recent-only query returns nothing
    — we'd rather cite a 30-day-old TechCrunch piece than nothing at all.
    """
    from db.client import search_company_knowledge

    try:
        chunks = await search_company_knowledge(
            company_name=company_name,
            query=query,
            match_count=match_count,
        )
    except Exception as e:
        logger.warning(f"G6 RAG fetch failed for {company_name}: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    fresh: list[dict] = []
    for c in chunks:
        scraped_at = c.get("scraped_at")
        if not scraped_at:
            continue
        try:
            ts = datetime.fromisoformat(str(scraped_at).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts >= cutoff:
            fresh.append(c)

    return fresh or chunks


def load_recent_linkedin_personal_wins(
    user_id: str,
    *,
    max_age_days: int = 14,
    limit: int = 3,
) -> list[dict]:
    """Personal-win signals from linkedin_drafts (status='posted' or 'approved').

    The career-ops follow-up template says signal sentences should cite
    EITHER fresh company news OR a personal win posted in the last 14 days.
    These rows let the draft_generator reference "I just shipped X" as the
    second sentence of the follow-up.
    """
    from db.client import get_supabase
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).isoformat()
    rows = (
        get_supabase()
        .table("linkedin_drafts")
        .select("id, hook, angle, status, posted_at, scheduled_for")
        .eq("user_id", user_id)
        .in_("status", ["posted", "approved", "scheduled"])
        .gte("scheduled_for", cutoff)
        .order("scheduled_for", desc=True)
        .limit(limit)
        .execute()
        .data
    ) or []
    return rows


# ═════════════════════════════════════════════════════════════════════════
# Writes
# ═════════════════════════════════════════════════════════════════════════
def insert_cadence_row(
    *,
    user_id: str,
    application_id: int,
    current_status: str,
    follow_up_count: int,
    urgency: Optional[str],
    next_follow_up_date: Optional[datetime],
    draft_email: Optional[str],
    draft_cites: Optional[list[dict]],
    draft_persona_critique: Optional[dict],
    cost_usd: float = 0.0,
    last_contact_date: Optional[datetime] = None,
) -> dict:
    """Insert a fresh follow_up_cadence row. Returns the inserted row."""
    from db.client import get_supabase
    payload: dict[str, Any] = {
        "user_id": user_id,
        "application_id": application_id,
        "current_status": current_status,
        "follow_up_count": follow_up_count,
        "urgency": urgency,
        "draft_email": draft_email,
        "draft_cites": draft_cites,
        "draft_persona_critique": draft_persona_critique,
        "cost_usd": float(cost_usd or 0.0),
    }
    if next_follow_up_date is not None:
        payload["next_follow_up_date"] = next_follow_up_date.astimezone(
            timezone.utc
        ).isoformat()
    if last_contact_date is not None:
        payload["last_contact_date"] = last_contact_date.astimezone(
            timezone.utc
        ).isoformat()

    result = get_supabase().table("follow_up_cadence").insert(payload).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError("follow_up_cadence insert returned 0 rows")
    return rows[0]


def update_cadence_row(
    cadence_id: str,
    *,
    follow_up_count: Optional[int] = None,
    urgency: Optional[str] = None,
    last_contact_date: Optional[datetime] = None,
    next_follow_up_date: Optional[datetime] = None,
    outcome: Optional[str] = None,
    outcome_attributed_cites: Optional[list[dict]] = None,
) -> dict:
    """Update a single follow_up_cadence row. Used by the /approve, /skip,
    and outcome-credit endpoints."""
    from db.client import get_supabase
    payload: dict[str, Any] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if follow_up_count is not None:
        payload["follow_up_count"] = follow_up_count
    if urgency is not None:
        payload["urgency"] = urgency
    if last_contact_date is not None:
        payload["last_contact_date"] = last_contact_date.astimezone(
            timezone.utc
        ).isoformat()
    if next_follow_up_date is not None:
        payload["next_follow_up_date"] = next_follow_up_date.astimezone(
            timezone.utc
        ).isoformat()
    if outcome is not None:
        payload["outcome"] = outcome
        payload["outcome_recorded_at"] = datetime.now(timezone.utc).isoformat()
    if outcome_attributed_cites is not None:
        payload["outcome_attributed_cites"] = outcome_attributed_cites

    res = (
        get_supabase()
        .table("follow_up_cadence")
        .update(payload)
        .eq("id", cadence_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else {}


def close_application_cold(application_id: int, user_id: str) -> None:
    """Transition an application's status to 'withdrawn' when the cadence
    rules engine declares it COLD (max follow-ups exhausted or auto-close
    window elapsed) AND the user confirms via /skip.

    Note: jobHunt's `application_status` enum (migration 002) does not
    include a literal 'closed_cold' value — the spec uses 'closed_cold'
    semantically but we map to 'withdrawn', which is the terminal
    post-apply state in the enum.
    """
    from db.client import get_supabase
    (
        get_supabase()
        .table("applications")
        .update({"status": "withdrawn"})
        .eq("id", application_id)
        .eq("user_id", user_id)
        .execute()
    )


# ═════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════
async def run_g6_for_application(
    application_id: int,
    *,
    user_id: Optional[str] = None,
    force_news_signal: bool = False,
) -> dict:
    """Run the G6 graph for one application and return the final state.

    The cron iterates over load_active_applications() and calls this. The
    API endpoints (approve/skip/regenerate) call this directly with the
    relevant application_id. Tests can call it after monkey-patching the
    LLM router.

    Returns a plain dict shape:
        {
          "application_id": int,
          "cadence_row_id": uuid|None,
          "urgency": str,
          "draft_email": str|None,
          "draft_cites": list,
          "cost_usd_total": float,
          "latency_ms_total": int,
          "short_circuit": bool,
          "applications_closed_cold": bool,
          "transcript": [...],
          "error": str|None,
        }
    """
    from agents.g6_graph import build_g6_graph

    user_id = _resolve_user_id(user_id)
    application = load_application(application_id)
    if application.get("user_id") and str(application["user_id"]) != user_id:
        raise PermissionError(
            f"application {application_id} does not belong to user {user_id}"
        )

    company_name = application.get("company") or ""
    persona = load_company_persona(user_id, company_name)
    last_cadence = load_last_cadence_row(application_id)

    initial_state = {
        "application_id": application_id,
        "user_id": user_id,
        "application_row": application,
        "company_persona": persona,
        "last_cadence_row": last_cadence,
        "follow_up_count": int((last_cadence or {}).get("follow_up_count") or 0),
        "iteration": 0,
        "draft_attempts": 0,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        "transcript": [],
        "force_news_signal": force_news_signal,
    }

    graph = build_g6_graph()
    final_state = await graph.ainvoke(initial_state)

    return {
        "application_id": application_id,
        "cadence_row_id": final_state.get("cadence_row_id"),
        "urgency": final_state.get("urgency"),
        "next_action": final_state.get("next_action"),
        "draft_email": final_state.get("draft_email"),
        "draft_cites": final_state.get("draft_cites") or [],
        "persona_critique": final_state.get("persona_critique"),
        "voice_critique": final_state.get("voice_critique"),
        "cost_usd_total": float(final_state.get("cost_usd_total") or 0.0),
        "latency_ms_total": int(final_state.get("latency_ms_total") or 0),
        "short_circuit": bool(final_state.get("short_circuit")),
        "applications_closed_cold": bool(final_state.get("applications_closed_cold")),
        "pushed_to_today_queue": bool(final_state.get("pushed_to_today_queue")),
        "transcript": final_state.get("transcript") or [],
        "error": final_state.get("error"),
    }


async def run_g6_batch(*, user_id: Optional[str] = None) -> dict:
    """Iterate every active application for `user_id` and run G6 on each.

    Errors per-application are caught and logged so the cron continues with
    the rest of the queue. Returns a summary suitable for cost telemetry +
    Railway logs.
    """
    user_id = _resolve_user_id(user_id)
    apps = load_active_applications(user_id=user_id)
    logger.info(f"G6 batch: starting for user_id={user_id} ({len(apps)} active apps)")

    summary = {
        "total": len(apps),
        "urgent": 0,
        "overdue": 0,
        "waiting": 0,
        "cold": 0,
        "errors": 0,
        "cost_usd": 0.0,
        "results": [],
    }
    for app in apps:
        try:
            res = await run_g6_for_application(app["id"], user_id=user_id)
        except Exception as e:
            logger.exception(f"G6 batch: failed for application {app.get('id')}")
            summary["errors"] += 1
            summary["results"].append({
                "application_id": app.get("id"),
                "error": f"{type(e).__name__}: {e}"[:300],
            })
            continue
        u = (res.get("urgency") or "").lower()
        if u == "urgent":
            summary["urgent"] += 1
        elif u == "overdue":
            summary["overdue"] += 1
        elif u == "waiting":
            summary["waiting"] += 1
        elif u == "cold":
            summary["cold"] += 1
        summary["cost_usd"] += float(res.get("cost_usd_total") or 0.0)
        summary["results"].append({
            "application_id": app.get("id"),
            "urgency": res.get("urgency"),
            "cost_usd": res.get("cost_usd_total"),
        })

    logger.info(
        f"G6 batch: done — urgent={summary['urgent']} overdue={summary['overdue']} "
        f"waiting={summary['waiting']} cold={summary['cold']} "
        f"errors={summary['errors']} cost_usd=${summary['cost_usd']:.4f}"
    )
    return summary
