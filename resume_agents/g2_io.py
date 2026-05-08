"""
resume_agents/g2_io.py — All Supabase reads/writes the G2 graph needs.

Centralised so nodes stay LLM-focused and we have a single place to fix
schema-drift issues in the future. Every public function here corresponds
to one entry-point or persist step in the graph.

Tables touched (all in public schema):
  READ   profile_master, profile_experience, profile_certification, profile_education
  READ   companies, company_personas
  READ   jobs, resume_builds, agent_conversations
  WRITE  resume_builds (insert + update)
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════
# Master profile assembly — the canonical source of truth for the candidate
# ═════════════════════════════════════════════════════════════════════════
def render_master_resume_md() -> str:
    """
    Render the candidate's full master profile as markdown by joining
    profile_master + profile_experience + profile_certification +
    profile_education.

    NOT public.rizwan_profile — that's a legacy embedding cache with
    stale section names. See docs/LIVE_DB_AUDIT.md §2.

    Returns a markdown string that becomes ResumeState.master_resume_md.
    Cached implicitly by callers; no caching here.
    """
    from db.client import get_supabase
    db = get_supabase()

    master_rows = db.table("profile_master").select("*").eq("id", 1).limit(1).execute().data
    if not master_rows:
        raise RuntimeError(
            "profile_master row not found. The G2 graph requires the "
            "structured profile to exist. Run the profile_build pipeline first."
        )
    master = master_rows[0]

    experience = (
        db.table("profile_experience").select("*").order("sort_order").execute().data
    ) or []
    certs = (
        db.table("profile_certification").select("*").order("sort_order").execute().data
    ) or []
    edu = (
        db.table("profile_education").select("*").order("sort_order").execute().data
    ) or []

    return _render_markdown(master, experience, certs, edu)


def _render_markdown(
    master: dict,
    experience: list[dict],
    certs: list[dict],
    edu: list[dict],
) -> str:
    """Pure render — testable without Supabase."""
    lines: list[str] = []

    # ─── Header ──────────────────────────────────────────────────────
    lines.append(f"# {master.get('name', '')}")
    if master.get("headline"):
        lines.append(f"### {master['headline']}")

    contact_parts = [
        master.get("location"),
        master.get("email"),
        master.get("linkedin_url"),
    ]
    phones = master.get("phones") or []
    if phones:
        contact_parts.extend(phones if isinstance(phones, list) else [str(phones)])
    contact = " · ".join(p for p in contact_parts if p)
    if contact:
        lines.append(contact)

    # ─── Summary ─────────────────────────────────────────────────────
    if master.get("summary"):
        lines.append("\n## Summary\n")
        lines.append(master["summary"])

    # ─── Core Competencies ───────────────────────────────────────────
    competencies = master.get("core_competencies") or []
    if competencies:
        lines.append("\n## Core Competencies\n")
        lines.append(", ".join(competencies))

    # ─── Technical Knowledge ─────────────────────────────────────────
    tech = master.get("technical_knowledge") or []
    if tech:
        lines.append("\n## Technical Knowledge\n")
        lines.append(", ".join(tech))

    # ─── Experience ──────────────────────────────────────────────────
    if experience:
        lines.append("\n## Experience\n")
        for exp in experience:
            title = exp.get("title", "")
            company = exp.get("company", "")
            dates = exp.get("dates", "")
            location = exp.get("location", "")
            header = f"### {title} — {company}".strip(" —")
            sub = " · ".join(p for p in [location, dates] if p)
            lines.append(header)
            if sub:
                lines.append(f"_{sub}_")
            if exp.get("summary"):
                lines.append(f"\n{exp['summary']}")
            highlights = exp.get("highlights") or []
            if highlights:
                lines.append("")
                for h in highlights:
                    lines.append(f"- {h}")
            # Nested groups (jsonb) — array of {heading, bullets}
            groups = exp.get("groups") or []
            for grp in groups:
                if not isinstance(grp, dict):
                    continue
                heading = grp.get("heading") or grp.get("title")
                if heading:
                    lines.append(f"\n**{heading}**")
                for b in grp.get("bullets") or []:
                    lines.append(f"- {b}")
            lines.append("")

    # ─── Certifications ──────────────────────────────────────────────
    if certs:
        lines.append("## Certifications\n")
        for c in certs:
            full = c.get("full_name") or c.get("name", "")
            issuer = c.get("issuer", "")
            year = c.get("year")
            line = full
            if issuer:
                line += f" — {issuer}"
            if year:
                line += f" ({year})"
            lines.append(f"- {line}")
        lines.append("")

    # ─── Education ───────────────────────────────────────────────────
    if edu:
        lines.append("## Education\n")
        for e in edu:
            title = e.get("title", "")
            details = e.get("details", "")
            year = e.get("year", "")
            line = title
            if details:
                line += f" — {details}"
            if year:
                line += f" ({year})"
            lines.append(f"- {line}")

    return "\n".join(lines).strip()


# ═════════════════════════════════════════════════════════════════════════
# Persona + history loaders
# ═════════════════════════════════════════════════════════════════════════
def load_company_persona(company_name: str) -> Optional[dict]:
    """
    Returns the row from public.company_personas matching the canonicalised
    name, or None on cold start. The Insider Expert node treats None as
    "fall back to a generic INSIDER_EXPERT_TEMPLATE prompt".
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


def load_past_transcripts(
    company_name: str,
    n: int = 5,
) -> list[dict]:
    """
    Primary source: last N resume_builds.agent_transcript rows for this
    company where status='converged', ordered by finalized_at DESC.

    Cold-start fallback: when primary returns 0, pull last N rows from
    agent_conversations for this company. This way the meta-critic still
    has *something* on first builds — gap-dialogue history pre-dates G2
    but is still useful signal (152 rows live as of 2026-05-09).

    Returns a list of dicts. Shape varies by source — the meta-critic
    node receives them as opaque text, so the shape difference is fine.
    """
    from db.client import get_supabase
    db = get_supabase()

    # Primary: G2 builds
    primary = (
        db.table("resume_builds")
        .select("agent_transcript, finalized_at, polisher_score")
        .eq("company_name", company_name)
        .eq("status", "converged")
        .order("finalized_at", desc=True)
        .limit(n)
        .execute()
        .data
    ) or []

    if primary:
        logger.info(
            f"G2 io.load_past_transcripts: {len(primary)} resume_builds for "
            f"{company_name}"
        )
        return [
            {"source": "resume_builds", **row} for row in primary
        ]

    # Cold-start fallback: agent_conversations
    fallback = (
        db.table("agent_conversations")
        .select("turn, speaker, message, gap_identified, gap_filled, created_at")
        .eq("company", company_name)
        .order("created_at", desc=True)
        .limit(n * 4)   # 4x because conversations come in turn pairs (rizwan + company_agent)
        .execute()
        .data
    ) or []

    logger.info(
        f"G2 io.load_past_transcripts: cold start — 0 resume_builds, "
        f"{len(fallback)} agent_conversations for {company_name}"
    )
    return [{"source": "agent_conversations", **row} for row in fallback]


# ═════════════════════════════════════════════════════════════════════════
# resume_builds row lifecycle
# ═════════════════════════════════════════════════════════════════════════
def create_resume_build(
    job_id: int,
    company_name: str,
    persona_version: Optional[int] = None,
) -> dict:
    """
    INSERT a new resume_builds row in 'running' state. Returns the row
    (so the graph can stash the uuid in state.resume_build_id).
    """
    from db.client import get_supabase
    payload = {
        "job_id": job_id,
        "company_name": company_name,
        "persona_version": persona_version,
        "status": "running",
        "iterations": 0,
    }
    result = get_supabase().table("resume_builds").insert(payload).execute()
    rows = result.data or []
    if not rows:
        raise RuntimeError(f"Failed to create resume_builds row for job {job_id}")
    return rows[0]


def finalize_resume_build(
    resume_build_id: str,
    *,
    status: str,                    # 'converged' | 'exhausted' | 'failed'
    iterations: int,
    ats_score_a: Optional[int] = None,
    ats_score_b: Optional[int] = None,
    polisher_score: Optional[int] = None,
    resume_md: Optional[str] = None,
    resume_docx_url: Optional[str] = None,
    resume_pdf_url: Optional[str] = None,
    cover_email_md: Optional[str] = None,
    agent_transcript: Optional[list] = None,
    cost_usd_total: Optional[float] = None,
    latency_ms_total: Optional[int] = None,
    error: Optional[str] = None,
) -> dict:
    """UPDATE the resume_builds row at end-of-run (success or failure)."""
    from db.client import get_supabase
    payload: dict[str, Any] = {
        "status": status,
        "iterations": iterations,
        "finalized_at": datetime.now(timezone.utc).isoformat(),
    }
    if ats_score_a is not None:        payload["ats_score_a"] = ats_score_a
    if ats_score_b is not None:        payload["ats_score_b"] = ats_score_b
    if polisher_score is not None:     payload["polisher_score"] = polisher_score
    if resume_md is not None:          payload["resume_md"] = resume_md
    if resume_docx_url is not None:    payload["resume_docx_url"] = resume_docx_url
    if resume_pdf_url is not None:     payload["resume_pdf_url"] = resume_pdf_url
    if cover_email_md is not None:     payload["cover_email_md"] = cover_email_md
    if agent_transcript is not None:   payload["agent_transcript"] = agent_transcript
    if cost_usd_total is not None:     payload["cost_usd_total"] = cost_usd_total
    if latency_ms_total is not None:   payload["latency_ms_total"] = latency_ms_total
    if error is not None:              payload["error"] = error[:5000]

    result = (
        get_supabase()
        .table("resume_builds")
        .update(payload)
        .eq("id", resume_build_id)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else {}


# ═════════════════════════════════════════════════════════════════════════
# Job loader
# ═════════════════════════════════════════════════════════════════════════
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
