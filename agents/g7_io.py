"""
agents/g7_io.py — All Supabase reads/writes for the G7 Application Graph
+ public orchestrator entry points.

Centralised so the LangGraph nodes stay LLM-focused.

Tables touched:
    READ   applications, jobs, profiles, company_personas, resume_builds
    WRITE  application_answers, follow_up_cadence (auto-enrol on G6)

Public entry points:
  - start_g7_run(application_id, form_url, ...)  → runs until interrupt;
        returns the application_answer_id + state snapshot.
  - resume_g7_run(application_answer_id, approvals)  → resumes the paused
        graph past the HITL interrupt and runs to END.
  - fetch_application_answers(application_answer_id) → row dict.
  - update_question(application_answer_id, idx, ...) → in-place edit for
        the /questions/{idx} PATCH endpoint.
  - regenerate_one(application_answer_id, idx) → re-run the generator for
        a single question (cheap; not a full graph run).

HITL persistence model:
  - One application_answers row created when start_g7_run begins.
  - status moves: drafting → awaiting_review → approved → filled → submitted
  - Postgres checkpointer (when present) saves the in-flight state per
    thread_id=application_answer_id. The checkpointer is what makes the
    HITL pause durable across server restarts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def _resolve_user_id(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    return os.environ.get("RIZWAN_USER_ID", _DEFAULT_USER_ID)


# ══════════════════════════════════════════════════════════════════════════
# DB reads
# ══════════════════════════════════════════════════════════════════════════
def load_application(application_id: int) -> dict:
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


def load_job(job_id: int) -> Optional[dict]:
    if job_id is None:
        return None
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("jobs")
        .select("*")
        .eq("id", job_id)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def load_company_persona(user_id: str, company_name: str) -> Optional[dict]:
    if not company_name:
        return None
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
    ) or []
    return rows[0] if rows else None


def load_latest_resume_build(user_id: str, job_id: Optional[int]) -> Optional[dict]:
    """Used to pre-load resume_builds.cover_email_md into state so the
    answer_retriever can populate the cover_letter retrieved_context."""
    if not job_id:
        return None
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("resume_builds")
        .select("id, cover_email_md, cv_md")
        .eq("user_id", user_id)
        .eq("job_id", job_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def _load_cv_md() -> str:
    """Read cv.md from repo root — same pattern as agents/g4_linkedin_graph.
    Future: pull from profile_master via profile_build module."""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "cv.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def _load_profile_yaml() -> dict:
    """Parse profile.yml when present; falls back to a minimal dict
    derived from cv.md's frontmatter if profile.yml is missing."""
    from pathlib import Path
    import yaml
    p = Path(__file__).resolve().parent.parent / "profile.yml"
    if p.exists():
        try:
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as e:
            logger.warning("G7 profile.yml parse failed: %s", e)
    return {}


# ══════════════════════════════════════════════════════════════════════════
# DB writes
# ══════════════════════════════════════════════════════════════════════════
def create_application_answers_row(
    *,
    user_id: str,
    application_id: int,
    form_url: str,
    ats_type: str = "greenhouse",
) -> dict:
    """INSERT the initial application_answers row at status='drafting'.

    Returns the inserted row (which includes the UUID we'll use as the
    LangGraph thread_id for checkpointing).
    """
    from db.client import get_supabase
    payload = {
        "user_id": user_id,
        "application_id": application_id,
        "form_url": form_url,
        "ats_type": ats_type,
        "questions": [],
        "status": "drafting",
        "total_cost_usd": 0.0,
    }
    result = get_supabase().table("application_answers").insert(payload).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError("application_answers insert returned 0 rows")
    return rows[0]


def update_status_and_questions(
    *,
    application_answer_id: str,
    status: str,
    questions: list[dict],
    user_id: Optional[str] = None,
) -> dict:
    from db.client import get_supabase
    payload: dict[str, Any] = {
        "status": status,
        "questions": questions,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    q = get_supabase().table("application_answers").update(payload).eq("id", application_answer_id)
    if user_id:
        q = q.eq("user_id", user_id)
    res = q.execute()
    rows = res.data or []
    return rows[0] if rows else {}


def finalize_application_answers(
    *,
    application_answer_id: str,
    user_id: str,
    questions: list[dict],
    total_cost_usd: float,
    status: str = "filled",
) -> dict:
    from db.client import get_supabase
    payload = {
        "status": status,
        "questions": questions,
        "total_cost_usd": float(total_cost_usd or 0.0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    res = (
        get_supabase()
        .table("application_answers")
        .update(payload)
        .eq("id", application_answer_id)
        .eq("user_id", user_id)
        .execute()
    )
    rows = res.data or []
    return rows[0] if rows else {}


def fetch_application_answers(application_answer_id: str, user_id: Optional[str] = None) -> Optional[dict]:
    from db.client import get_supabase
    q = (
        get_supabase()
        .table("application_answers")
        .select("*")
        .eq("id", application_answer_id)
    )
    if user_id:
        q = q.eq("user_id", user_id)
    rows = q.limit(1).execute().data or []
    return rows[0] if rows else None


def update_question(
    *,
    application_answer_id: str,
    user_id: str,
    idx: int,
    user_edited: Optional[str] = None,
    approved: Optional[bool] = None,
) -> dict:
    """Edit ONE question in place (the dashboard's PATCH route).

    We read-modify-write the whole questions JSONB because Postgres
    JSONB updates are easier to reason about that way at our scale (~25
    questions per row, single-digit RPS).
    """
    row = fetch_application_answers(application_answer_id, user_id=user_id)
    if not row:
        raise RuntimeError(f"application_answer {application_answer_id} not found")
    questions = list(row.get("questions") or [])
    for q in questions:
        if int(q.get("idx", -1)) == int(idx):
            if user_edited is not None:
                q["user_edited"] = user_edited
            if approved is not None:
                q["approved"] = bool(approved)
            break
    else:
        raise RuntimeError(f"question idx={idx} not found")
    return update_status_and_questions(
        application_answer_id=application_answer_id,
        status=row.get("status") or "awaiting_review",
        questions=questions,
        user_id=user_id,
    )


def ensure_g6_enrolment(*, user_id: str, application_id: int) -> Optional[str]:
    """Auto-enrol the application in the G6 follow-up cadence.

    Idempotent: if a cadence row already exists for this application,
    we leave it alone. Otherwise insert one with current_status='applied',
    follow_up_count=0, next_follow_up_date=NOW()+7d.
    """
    from db.client import get_supabase

    existing = (
        get_supabase()
        .table("follow_up_cadence")
        .select("id")
        .eq("application_id", application_id)
        .limit(1)
        .execute()
        .data
    ) or []
    if existing:
        return str(existing[0]["id"])

    next_fu = datetime.now(timezone.utc) + timedelta(days=7)
    payload = {
        "user_id": user_id,
        "application_id": application_id,
        "current_status": "applied",
        "follow_up_count": 0,
        "urgency": "waiting",
        "next_follow_up_date": next_fu.isoformat(),
    }
    res = get_supabase().table("follow_up_cadence").insert(payload).execute()
    rows = res.data or []
    return str(rows[0]["id"]) if rows else None


# ══════════════════════════════════════════════════════════════════════════
# Checkpointer
# ══════════════════════════════════════════════════════════════════════════
async def get_g7_checkpointer():
    """Return an AsyncPostgresSaver pointed at Supabase, or None when no
    DB URL is configured (tests).

    Matches the pattern in resume_agents.g2_graph.get_postgres_checkpointer
    so ops only has to wire SUPABASE_DB_URL once. The first call from a
    fresh deploy lazily creates the langgraph schema tables.
    """
    conn_str = os.environ.get("SUPABASE_DB_URL") or os.environ.get("POSTGRES_URL")
    if not conn_str:
        logger.info("G7 checkpointer: no SUPABASE_DB_URL/POSTGRES_URL — running without HITL durability")
        return None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(conn_str)
    except ImportError:
        logger.warning("G7 checkpointer: langgraph-checkpoint-postgres not installed")
        return None


# ══════════════════════════════════════════════════════════════════════════
# Public orchestrator entry points
# ══════════════════════════════════════════════════════════════════════════
async def start_g7_run(
    *,
    application_id: int,
    form_url: str,
    user_id: Optional[str] = None,
    ats_type: str = "greenhouse",
    force: bool = False,
    _test_html: Optional[str] = None,
) -> dict:
    """Kick off a G7 run for the given application.

    Flow:
      1. Wallet guard via api._job_guards.load_open_job (closed jobs → raise
         unless force=True).
      2. Create the application_answers row at status='drafting'.
      3. Build initial ApplicationFormState (load app, persona, cv.md,
         profile.yml).
      4. Compile the graph with a Postgres checkpointer; thread_id =
         application_answer_id.
      5. await graph.ainvoke(initial_state, config) — runs until human_review
         hits interrupt(), at which point control returns here with the
         partial state.
      6. Return {application_answer_id, status, questions, cost_usd_total}.

    Returns a dict suitable for the POST /apply response. The dashboard
    polls fetch_application_answers until status='awaiting_review'.

    Test mode: pass _test_html to skip Playwright entirely.
    """
    user_id = _resolve_user_id(user_id)
    application = load_application(application_id)
    if application.get("user_id") and str(application["user_id"]) != user_id:
        raise PermissionError(f"application {application_id} not yours")

    # Wallet guard (the spec calls it out explicitly).
    job_id = application.get("job_id")
    if job_id and not _test_html:
        try:
            from uuid import UUID as _UUID
            from api._job_guards import load_open_job_or_none
            from db.client import get_supabase as _gs
            job = load_open_job_or_none(
                _gs(), job_id=int(job_id), force=force, user_id=_UUID(user_id),
            )
            if job is None and not force:
                raise PermissionError("job_closed_or_stale")
        except (ImportError, ValueError):
            # ImportError: older deploys. ValueError: user_id not a UUID
            # (test fixture). Either way, skip silently.
            pass

    # 1. Create the row.
    row = create_application_answers_row(
        user_id=user_id,
        application_id=application_id,
        form_url=form_url,
        ats_type=ats_type,
    )
    application_answer_id = str(row["id"])

    # 2. Build the initial state.
    persona = load_company_persona(user_id, application.get("company") or "")
    job_row = load_job(job_id) if job_id else None
    cv_md = _load_cv_md()
    profile = _load_profile_yaml()
    # Lazily fold in the most-recent resume_build.cover_email_md so the
    # cover-letter retrieval has something to copy from.
    rb = load_latest_resume_build(user_id, job_id)
    if rb and rb.get("cover_email_md"):
        application = {**application, "cover_email_md": rb["cover_email_md"]}

    initial_state: dict[str, Any] = {
        "application_answer_id": application_answer_id,
        "application_id": application_id,
        "user_id": user_id,
        "form_url": form_url,
        "ats_type": ats_type,
        "application_row": application,
        "job_row": job_row,
        "company_persona": persona,
        "cv_md": cv_md,
        "profile": profile,
        "questions": [],
        "generation_iteration": 0,
        "generation_attempts": 0,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        "transcript": [],
    }
    if _test_html is not None:
        initial_state["_test_html"] = _test_html

    # 3. Compile graph with checkpointer.
    from agents.g7_graph import build_g7_graph
    checkpointer = await get_g7_checkpointer()
    if checkpointer is None:
        # Tests / no-DB mode — try the in-memory saver so the interrupt
        # path is still exercise-able.
        try:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
        except ImportError:
            checkpointer = None

    if checkpointer is not None and hasattr(checkpointer, "__aenter__"):
        async with checkpointer as cp:
            graph = build_g7_graph(checkpointer=cp)
            return await _invoke_until_interrupt(
                graph=graph,
                initial_state=initial_state,
                application_answer_id=application_answer_id,
                user_id=user_id,
            )
    else:
        graph = build_g7_graph(checkpointer=checkpointer)
        return await _invoke_until_interrupt(
            graph=graph,
            initial_state=initial_state,
            application_answer_id=application_answer_id,
            user_id=user_id,
        )


async def _invoke_until_interrupt(
    *,
    graph,
    initial_state: dict,
    application_answer_id: str,
    user_id: str,
) -> dict:
    config = {"configurable": {"thread_id": application_answer_id}}
    try:
        final = await graph.ainvoke(initial_state, config=config)
    except Exception as e:
        logger.exception("G7 start_g7_run failed")
        # Mark the row as cancelled so the dashboard can surface a useful
        # error. We don't bubble out a 500 silently.
        try:
            update_status_and_questions(
                application_answer_id=application_answer_id,
                status="cancelled",
                questions=initial_state.get("questions") or [],
                user_id=user_id,
            )
        except Exception:
            pass
        raise
    # When the graph hits interrupt(), `final` is the state snapshot up
    # to that point. The status was already flipped to 'awaiting_review'
    # by human_review_node BEFORE the pause, so the dashboard can poll.
    return {
        "application_answer_id": application_answer_id,
        "status": (final or {}).get("status") or "awaiting_review",
        "questions": (final or {}).get("questions") or [],
        "scanner_meta": (final or {}).get("scanner_meta") or {},
        "classification_summary": (final or {}).get("classification_summary") or {},
        "persona_critique_summary": (final or {}).get("persona_critique_summary") or {},
        "merge_outcome": (final or {}).get("merge_outcome") or {},
        "cost_usd_total": float((final or {}).get("cost_usd_total") or 0.0),
        "latency_ms_total": int((final or {}).get("latency_ms_total") or 0),
        "transcript": (final or {}).get("transcript") or [],
    }


async def resume_g7_run(
    *,
    application_answer_id: str,
    approvals: list[dict],
    user_id: Optional[str] = None,
) -> dict:
    """Resume a paused G7 run past the HITL interrupt.

    Called by POST /workspace/applications/{id}/approve. The `approvals`
    payload is a list of {idx, approved, user_edited?} dicts — the
    dashboard's per-field approve UI sends them all at once.

    Implementation:
      - Mark status='approved' so the dashboard knows it's resuming.
      - graph.ainvoke(Command(resume={"approvals": approvals}), config)
        with thread_id = application_answer_id resumes from the saved
        state, hands `approvals` to interrupt()'s return value inside
        human_review_node, then runs form_filler + persist to completion.
    """
    user_id = _resolve_user_id(user_id)

    row = fetch_application_answers(application_answer_id, user_id=user_id)
    if not row:
        raise RuntimeError(f"application_answer {application_answer_id} not found")
    if row.get("status") not in ("awaiting_review", "approved"):
        # Permissive: re-approve is allowed; the form_filler is idempotent
        # on already-filled fields and the persist node will overwrite.
        logger.info(
            "G7 resume: row %s is in status=%s (proceeding anyway)",
            application_answer_id, row.get("status"),
        )

    update_status_and_questions(
        application_answer_id=application_answer_id,
        status="approved",
        questions=row.get("questions") or [],
        user_id=user_id,
    )

    from agents.g7_graph import build_g7_graph
    from langgraph.types import Command

    checkpointer = await get_g7_checkpointer()
    if checkpointer is None:
        # MemorySaver fallback for tests.
        try:
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()
        except ImportError:
            checkpointer = None

    config = {"configurable": {"thread_id": application_answer_id}}

    if checkpointer is not None and hasattr(checkpointer, "__aenter__"):
        async with checkpointer as cp:
            graph = build_g7_graph(checkpointer=cp)
            final = await graph.ainvoke(
                Command(resume={"approvals": approvals}),
                config=config,
            )
    else:
        graph = build_g7_graph(checkpointer=checkpointer)
        final = await graph.ainvoke(
            Command(resume={"approvals": approvals}),
            config=config,
        )

    return {
        "application_answer_id": application_answer_id,
        "status": (final or {}).get("status") or "filled",
        "questions": (final or {}).get("questions") or [],
        "filler_summary": (final or {}).get("filler_summary") or {},
        "cadence_row_id": (final or {}).get("cadence_row_id"),
        "identity_lock_redactions": (final or {}).get("identity_lock_redactions") or [],
        "cost_usd_total": float((final or {}).get("cost_usd_total") or 0.0),
        "transcript": (final or {}).get("transcript") or [],
    }


async def cancel_g7_run(*, application_answer_id: str, user_id: Optional[str] = None) -> dict:
    user_id = _resolve_user_id(user_id)
    row = fetch_application_answers(application_answer_id, user_id=user_id)
    if not row:
        raise RuntimeError(f"application_answer {application_answer_id} not found")
    return update_status_and_questions(
        application_answer_id=application_answer_id,
        status="cancelled",
        questions=row.get("questions") or [],
        user_id=user_id,
    )


async def regenerate_one(
    *,
    application_answer_id: str,
    user_id: Optional[str] = None,
    idx: int,
) -> dict:
    """Re-run answer_generator + persona_critic for a SINGLE question.

    Cheap path that doesn't reset the whole graph — used by the
    "Regenerate" button in the dashboard. Costs ~$0.05 since we run
    the generator over a 1-question batch.
    """
    user_id = _resolve_user_id(user_id)
    row = fetch_application_answers(application_answer_id, user_id=user_id)
    if not row:
        raise RuntimeError(f"application_answer {application_answer_id} not found")
    questions = list(row.get("questions") or [])
    target_idx = int(idx)
    target = next((q for q in questions if int(q.get("idx", -1)) == target_idx), None)
    if target is None:
        raise RuntimeError(f"question idx={target_idx} not found")

    # Build a single-question state slice and run JUST the
    # answer_generator + persona_critic nodes via direct function calls
    # (avoids re-doing the form scan / classification).
    from agents.g7_nodes import (
        answer_generator_node,
        persona_critic_node,
    )

    application = load_application(row["application_id"])
    persona = load_company_persona(user_id, application.get("company") or "")
    cv_md = _load_cv_md()

    slice_state: dict = {
        "application_answer_id": application_answer_id,
        "application_id": row["application_id"],
        "user_id": user_id,
        "questions": [target],
        "application_row": application,
        "company_persona": persona,
        "cv_md": cv_md,
        "generation_iteration": 0,
        "generation_attempts": 0,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        "transcript": [],
    }

    gen_patch = await answer_generator_node(slice_state)
    slice_state.update(gen_patch)
    crit_patch = await persona_critic_node(slice_state)
    slice_state.update(crit_patch)

    # Splice the regenerated question back into the row's questions list.
    new_q = slice_state["questions"][0]
    for i, q in enumerate(questions):
        if int(q.get("idx", -1)) == target_idx:
            questions[i] = new_q
            break

    new_cost = float(row.get("total_cost_usd") or 0.0) + float(slice_state.get("cost_usd_total") or 0.0)
    from db.client import get_supabase
    get_supabase().table("application_answers").update({
        "questions": questions,
        "total_cost_usd": new_cost,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", application_answer_id).eq("user_id", user_id).execute()

    return {
        "application_answer_id": application_answer_id,
        "idx": target_idx,
        "question": new_q,
        "cost_usd_delta": float(slice_state.get("cost_usd_total") or 0.0),
        "total_cost_usd": new_cost,
    }
