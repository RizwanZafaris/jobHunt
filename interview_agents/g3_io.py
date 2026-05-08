"""
interview_agents/g3_io.py — All Supabase reads/writes the G3 graph needs.

Centralised so nodes stay LLM-focused and we have a single place to fix
schema-drift issues in the future. Every public function here corresponds
to one entry-point or persist step in the graph.

Tables touched (all in public schema):
  READ   applications, jobs, company_personas, story_bank, resume_builds,
         interview_outcomes
  WRITE  interview_prep (insert + update)
  WRITE  Supabase Storage 'interview-packs/' bucket
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# Application + job loaders
# ═════════════════════════════════════════════════════════════════════════
def load_application(application_id: str) -> dict:
    """Fetch one row from public.applications."""
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
        raise RuntimeError(f"Application {application_id!r} not found")
    return rows[0]


def load_job(job_id: int) -> dict:
    """Fetch one row from public.jobs."""
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("jobs")
        .select("*")
        .eq("id", job_id)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        raise RuntimeError(f"Job {job_id} not found")
    return rows[0]


# ═════════════════════════════════════════════════════════════════════════
# Persona + story bank + history loaders
# ═════════════════════════════════════════════════════════════════════════
def load_company_persona(company_name: str) -> Optional[dict]:
    """
    Returns the row from public.company_personas matching the canonicalised
    name, or None on cold start. The behavioral/domain predictor nodes treat
    None as "fall back to a generic recruitment-best-practices prompt".
    """
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("company_personas")
        .select("*")
        .eq("company_name", company_name)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def load_story_bank(limit: int = 200) -> list[dict]:
    """
    Fetch all STAR+R stories the candidate has accumulated. Empty on cold
    start (live DB has 0 rows as of 2026-05-09 — `star_story_matcher_node`
    must handle this gracefully).

    The `companies_used` text[] column lets us boost stories already used
    for this company — done in g3_nodes, not here.
    """
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("story_bank")
        .select("id, title, situation, task, action, result, reflection, "
                "themes, companies_used, created_at")
        .limit(limit)
        .execute()
        .data
    ) or []
    logger.info(f"G3 io.load_story_bank: {len(rows)} stories loaded")
    return rows


def load_last_resume_build(job_id: int) -> Optional[dict]:
    """
    Most recent resume_builds row for this job (so the predictors know what
    keywords / framing the resume emphasised — likely interview probe areas).
    Returns None if no build exists yet.
    """
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("resume_builds")
        .select("id, company_name, resume_md, polisher_score, "
                "ats_score_a, ats_score_b, status, finalized_at, agent_transcript")
        .eq("job_id", job_id)
        .order("finalized_at", desc=True)
        .limit(1)
        .execute()
        .data
    ) or []
    return rows[0] if rows else None


def load_interview_history(application_id: str) -> list[dict]:
    """
    Prior interview_outcomes rows for this application. On round 2+ the
    predictor uses the round-1 questions/feedback as a strong signal for
    what's coming next.
    """
    from db.client import get_supabase
    rows = (
        get_supabase()
        .table("interview_outcomes")
        .select("round_number, round_type, passed, questions_asked, "
                "feedback_text, conducted_at")
        .eq("application_id", application_id)
        .order("round_number", desc=False)
        .execute()
        .data
    ) or []
    logger.info(
        f"G3 io.load_interview_history: {len(rows)} prior rounds for "
        f"application {application_id}"
    )
    return rows


# ═════════════════════════════════════════════════════════════════════════
# interview_prep row lifecycle
# ═════════════════════════════════════════════════════════════════════════
def create_interview_prep(
    application_id: str,
    job_id: Optional[int],
    company_name: str,
    round_type: str,
    round_number: int = 1,
) -> dict:
    """
    INSERT a new interview_prep row in 'running' state. Returns the row
    (so the graph can stash the uuid in state.interview_prep_id).
    """
    from db.client import get_supabase
    payload = {
        "application_id": application_id,
        "job_id": job_id,
        "company_name": company_name,
        "round_type": round_type,
        "round_number": round_number,
        "status": "running",
        "iterations": 0,
    }
    result = get_supabase().table("interview_prep").insert(payload).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError(
            f"Failed to create interview_prep row for application {application_id}"
        )
    return rows[0]


def finalize_interview_prep(
    interview_prep_id: str,
    *,
    status: str,                    # 'converged' | 'exhausted' | 'cost_capped' | 'failed'
    iterations: int,
    prep_pack_md: Optional[str] = None,
    prep_pack_url: Optional[str] = None,
    likely_questions: Optional[list] = None,
    star_stories: Optional[list] = None,
    company_hooks: Optional[list] = None,
    red_flag_questions: Optional[list] = None,
    salary_negotiation_notes: Optional[str] = None,
    agent_transcript: Optional[list] = None,
    cost_usd_total: Optional[float] = None,
    latency_ms_total: Optional[int] = None,
    graph_thread_id: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    """UPDATE the interview_prep row at end-of-run (success or failure)."""
    from db.client import get_supabase
    payload: dict[str, Any] = {
        "status": status,
        "iterations": iterations,
        "finalized_at": datetime.now(timezone.utc).isoformat(),
    }
    if prep_pack_md is not None:           payload["prep_pack_md"] = prep_pack_md
    if prep_pack_url is not None:          payload["prep_pack_url"] = prep_pack_url
    if likely_questions is not None:       payload["likely_questions"] = likely_questions
    if star_stories is not None:           payload["star_stories"] = star_stories
    if company_hooks is not None:          payload["company_hooks"] = company_hooks
    if red_flag_questions is not None:     payload["red_flag_questions"] = red_flag_questions
    if salary_negotiation_notes is not None: payload["salary_negotiation_notes"] = salary_negotiation_notes
    if agent_transcript is not None:       payload["agent_transcript"] = agent_transcript
    if cost_usd_total is not None:         payload["cost_usd_total"] = cost_usd_total
    if latency_ms_total is not None:       payload["latency_ms_total"] = latency_ms_total
    if graph_thread_id is not None:        payload["graph_thread_id"] = graph_thread_id
    if error is not None:                  payload["error"] = error[:5000]

    result = (
        get_supabase()
        .table("interview_prep")
        .update(payload)
        .eq("id", interview_prep_id)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else {}


# ═════════════════════════════════════════════════════════════════════════
# Storage upload (interview-packs bucket)
# ═════════════════════════════════════════════════════════════════════════
def upload_prep_pack(
    company_name: str,
    interview_prep_id: str,
    prep_pack_md: str,
) -> Optional[str]:
    """
    Upload prep pack markdown to Supabase Storage 'interview-packs/' bucket.
    Returns the public/signed URL or None on failure (graceful degradation —
    the prep pack is still saved in interview_prep.prep_pack_md).
    """
    try:
        from db.client import upload_artifact
        import os, tempfile
        with tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(prep_pack_md)
            md_path = f.name
        try:
            url = upload_artifact(
                local_path=md_path,
                remote_path=(
                    f"interview-packs/{company_name.lower().replace(' ', '-')}/"
                    f"{interview_prep_id}.md"
                ),
                content_type="text/markdown",
            )
            return url
        finally:
            if os.path.exists(md_path):
                os.unlink(md_path)
    except Exception as e:
        logger.warning(f"G3 prep pack upload failed: {e}")
        return None
