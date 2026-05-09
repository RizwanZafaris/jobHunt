"""
resume_agents/g2_run.py — Top-level entry point for invoking the G2 graph.

Used by:
  pipeline._process_single_job()  (when USE_G2_GRAPH=true feature flag is set)
  api/server.py /jobs/{id}/generate-resume   (the manual-trigger endpoint)
  scripts/run.py                  (CLI runs)

Wraps:
  1. Canonicalize company name (mirrors agents/company_agent.CompanyAgent._canonicalize logic)
  2. Build/get the compiled graph (cached process-wide)
  3. Invoke graph with a thread_id so checkpointer state is keyed properly
  4. Catch fatal errors and finalize the resume_builds row to status='failed'

Phase 1.12 also exports `check_persona_quality_gate` — used by the API to
refuse builds against low-quality personas without an explicit force flag.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Persona quality gate (Phase 1.12) ──────────────────────────────────
QUALITY_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass
class GateResult:
    """Outcome of check_persona_quality_gate."""
    verdict: str          # 'pass' | 'cold_start' | 'blocked'
    quality: Optional[str]  # 'high' | 'medium' | 'low' | None (cold start)
    persona_version: Optional[int]
    unknown_sections: Optional[int]
    message: str          # human-readable rationale


def check_persona_quality_gate(
    company_name: str,
    *,
    force: bool = False,
    min_quality: Optional[str] = None,
) -> GateResult:
    """
    Decide whether a G2 build for `company_name` should proceed.

    Logic:
      - force=True               → always pass (verdict='pass', message notes override)
      - no persona row           → pass with verdict='cold_start' (Insider Expert
                                   uses fallback prompt; quality is unknown but
                                   we don't block since cold-start is a real path)
      - quality < min_quality    → blocked (caller should surface to user with
                                   option to retry with force=true)
      - quality >= min_quality   → pass

    The min_quality parameter defaults to settings.g2_min_persona_quality
    (default 'medium', which blocks 'low').
    """
    from config.settings import get_settings
    from db.client import get_supabase

    settings = get_settings()
    min_q = (min_quality or settings.g2_min_persona_quality or "medium").lower()
    if min_q not in QUALITY_RANK:
        logger.warning(f"Invalid min_quality {min_q!r} — defaulting to 'medium'")
        min_q = "medium"

    if force:
        return GateResult(
            verdict="pass",
            quality=None,
            persona_version=None,
            unknown_sections=None,
            message=f"force=true override (min_quality={min_q!r} bypassed)",
        )

    # Look up the persona for this company
    try:
        rows = (
            get_supabase()
            .table("company_personas")
            .select("persona_version, metadata")
            .eq("company_name", company_name)
            .limit(1)
            .execute()
            .data
        ) or []
    except Exception as e:
        # If we can't reach the table, don't block — treat as cold start.
        logger.warning(f"persona quality gate query failed: {e} — treating as cold start")
        return GateResult(
            verdict="cold_start",
            quality=None,
            persona_version=None,
            unknown_sections=None,
            message=f"could not query company_personas ({e}) — cold start",
        )

    if not rows:
        return GateResult(
            verdict="cold_start",
            quality=None,
            persona_version=None,
            unknown_sections=None,
            message=(
                f"No persona row for {company_name!r}. G2 will use the "
                f"INSIDER_EXPERT_FALLBACK_SYSTEM prompt. Quality unknown "
                f"but cold-start is a supported path."
            ),
        )

    row = rows[0]
    metadata = row.get("metadata") or {}
    quality = (metadata.get("persona_quality") or "unknown").lower()
    unknown_sections = metadata.get("unknown_sections")
    version = row.get("persona_version")

    if quality not in QUALITY_RANK:
        # Unknown quality tier — don't block, but flag it
        return GateResult(
            verdict="pass",
            quality=quality,
            persona_version=version,
            unknown_sections=unknown_sections,
            message=(
                f"Persona v{version} has metadata.persona_quality={quality!r} "
                f"(unrecognised tier). Allowing build — review manually."
            ),
        )

    if QUALITY_RANK[quality] < QUALITY_RANK[min_q]:
        return GateResult(
            verdict="blocked",
            quality=quality,
            persona_version=version,
            unknown_sections=unknown_sections,
            message=(
                f"Persona quality for {company_name!r} is {quality!r} "
                f"(v{version}, {unknown_sections or 0}/5 sections were "
                f"'Unknown — insufficient data' at synthesis). Minimum "
                f"required is {min_q!r}. Pass force=true to override "
                f"and accept the risk of a sub-par build."
            ),
        )

    return GateResult(
        verdict="pass",
        quality=quality,
        persona_version=version,
        unknown_sections=unknown_sections,
        message=f"Persona v{version} quality={quality!r} meets min={min_q!r}",
    )

# Process-wide compiled graph (with checkpointer if available)
_GRAPH = None


def _get_graph():
    """Lazy graph compile — only happens on first invocation."""
    global _GRAPH
    if _GRAPH is None:
        from resume_agents.g2_graph import build_g2_graph, get_postgres_checkpointer
        checkpointer = get_postgres_checkpointer()
        _GRAPH = build_g2_graph(checkpointer=checkpointer)
        logger.info(
            f"G2 graph compiled "
            f"(checkpointer={'on' if checkpointer else 'off'})"
        )
    return _GRAPH


def is_enabled() -> bool:
    """Feature flag: USE_G2_GRAPH=true (or 1 or yes) enables G2."""
    val = os.environ.get("USE_G2_GRAPH", "").lower().strip()
    return val in ("1", "true", "yes", "on")


def _canonicalize_company(name: str) -> str:
    """Mirror CompanyAgent._canonicalize so persona lookup hits the right row."""
    if not name:
        return name
    n = name.strip()
    for suffix in (" Careers", " careers", " Jobs", " jobs",
                   " hiring", " Hiring",
                   " (Uber subsidiary)", " (Alibaba Group)",
                   " — Careers"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
            break
    # Match against known target companies (best effort)
    try:
        from db.client import get_supabase
        rows = (
            get_supabase()
            .table("companies")
            .select("name")
            .eq("is_target", True)
            .execute()
            .data
        ) or []
        target_names = {r["name"]: r["name"] for r in rows}
        target_lower = {r["name"].lower(): r["name"] for r in rows}
        if n in target_names:
            return n
        if n.lower() in target_lower:
            return target_lower[n.lower()]
        for t in target_names:
            if t.lower() in n.lower() and len(t) >= 4:
                return t
    except Exception:
        pass
    return n


async def run_g2_graph(
    job_id: int,
    company_name: Optional[str] = None,
    max_cost_usd: Optional[float] = None,
) -> dict:
    """
    Run the G2 graph for one job. Returns the final ResumeState as a dict.

    If company_name isn't passed, it's read from the jobs row.

    Phase 1.11: max_cost_usd defaults to settings.g2_max_cost_usd (5.0).
    Pass a different value (e.g. 10.0 for top-tier targets) to relax the
    cap. The orchestrator forces converge with status='cost_capped' if
    cumulative cost exceeds the cap mid-build.
    """
    from resume_agents.g2_io import load_job, finalize_resume_build
    from config.settings import get_settings

    # Resolve company name from the job if not provided
    if not company_name:
        job = load_job(job_id)
        company_name = job.get("company") or ""

    canonical = _canonicalize_company(company_name)
    cap = (
        max_cost_usd if max_cost_usd is not None
        else get_settings().g2_max_cost_usd
    )
    logger.info(
        f"G2 run start: job_id={job_id} company={company_name!r} → canonical={canonical!r}"
        f" cost_cap=${cap:.2f}"
    )

    initial_state = {
        "job_id": job_id,
        "company_name": canonical,
        "cost_cap_usd": cap,
        "transcript": [],
        "iteration": 0,
        "converged": False,
        "cost_capped": False,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
    }
    thread_id = f"g2-job-{job_id}"

    graph = _get_graph()
    try:
        # ainvoke supports both checkpointed (with config) and stateless modes
        config = {"configurable": {"thread_id": thread_id}}
        final_state = await graph.ainvoke(initial_state, config=config)
        logger.info(
            f"G2 run done: job_id={job_id} score={final_state.get('final_score')} "
            f"iters={final_state.get('iteration')} cost=${final_state.get('cost_usd_total', 0):.2f}"
        )
        return dict(final_state)
    except Exception as e:
        logger.exception(f"G2 run failed for job_id={job_id}")
        # Best-effort: tag the most recent running resume_builds row for this
        # job with the exception type + message + truncated traceback. Lets
        # us diagnose post-hoc from Supabase without needing Railway logs.
        try:
            import traceback
            from db.client import get_supabase
            err_summary = (
                f"{type(e).__name__}: {str(e)[:300]}\n\n"
                f"Traceback (last 2KB):\n{traceback.format_exc()[-2000:]}"
            )
            get_supabase().table("resume_builds").update({
                "status": "failed",
                "error": err_summary,
                "finalized_at": datetime.now(timezone.utc).isoformat(),
            }).eq("job_id", job_id).eq("status", "running").execute()
        except Exception as record_err:
            logger.warning(f"Failed to record G2 failure to resume_builds: {record_err}")
        raise
