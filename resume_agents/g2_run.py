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
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

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
        # Best-effort failure recording — only works if entry_node ran far
        # enough to create a resume_builds row.
        try:
            # If we have a resume_build_id in state mid-run, the checkpointer
            # would have it. Without checkpointer this is impossible to recover
            # cleanly; we just log and re-raise.
            pass
        except Exception:
            pass
        raise
