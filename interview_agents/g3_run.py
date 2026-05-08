"""
interview_agents/g3_run.py — Top-level entry point for invoking the G3 graph.

Used by:
  api/server.py /jobs/{id}/prep-interview   (the manual-trigger endpoint)
  scripts/run.py                            (CLI runs)

Wraps:
  1. Canonicalize company name (mirrors agents/company_agent.CompanyAgent._canonicalize logic
     — kept locally to avoid a heavy import)
  2. Build/get the compiled graph (cached process-wide)
  3. Invoke graph with a thread_id so checkpointer state is keyed properly
  4. Catch fatal errors and finalize the interview_prep row to status='failed'

We REUSE `check_persona_quality_gate` from `resume_agents.g2_run` rather
than duplicating the function. Pass `min_quality=settings.g3_min_persona_quality`
when calling — defaults differ per graph but the gate logic is identical.
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
        from interview_agents.g3_graph import build_g3_graph, get_postgres_checkpointer
        checkpointer = get_postgres_checkpointer()
        _GRAPH = build_g3_graph(checkpointer=checkpointer)
        logger.info(
            f"G3 graph compiled "
            f"(checkpointer={'on' if checkpointer else 'off'})"
        )
    return _GRAPH


def is_enabled() -> bool:
    """Feature flag: USE_G3_GRAPH=true (or 1 or yes) enables G3."""
    val = os.environ.get("USE_G3_GRAPH", "").lower().strip()
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


async def run_g3_graph(
    application_id: str,
    *,
    round_type: str = "hm",
    round_number: int = 1,
    company_name: Optional[str] = None,
    max_cost_usd: Optional[float] = None,
) -> dict:
    """
    Run the G3 graph for one application/round. Returns the final
    InterviewPrepState as a dict.

    If company_name isn't passed, it's read from the application row (or
    the linked job row).

    Phase 2: max_cost_usd defaults to settings.g3_max_cost_usd ($3.0).
    Pass a different value (e.g. $5.0) to relax the cap. The mock loop
    forces termination with status='cost_capped' if cumulative cost
    exceeds the cap.
    """
    from interview_agents.g3_io import load_application, load_job
    from config.settings import get_settings

    settings = get_settings()

    # Resolve company name from the application/job if not provided
    if not company_name:
        try:
            application = load_application(application_id)
            company_name = application.get("company") or ""
            if not company_name and application.get("job_id"):
                job = load_job(application["job_id"])
                company_name = job.get("company") or ""
        except Exception as e:
            logger.warning(f"G3 run: company name resolution failed: {e}")
            company_name = ""

    canonical = _canonicalize_company(company_name) if company_name else ""
    cap = (
        max_cost_usd if max_cost_usd is not None
        else settings.g3_max_cost_usd
    )
    logger.info(
        f"G3 run start: application_id={application_id} round={round_type}#{round_number}"
        f" company={company_name!r} → canonical={canonical!r} cost_cap=${cap:.2f}"
    )

    initial_state = {
        "application_id": application_id,
        "company_name": canonical,
        "round_type": round_type,
        "round_number": round_number,
        "cost_cap_usd": cap,
        "transcript": [],
        "iteration": 0,
        "mock_iteration": 0,
        "converged": False,
        "cost_capped": False,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
    }
    thread_id = f"g3-app-{application_id}-r{round_number}"

    graph = _get_graph()
    try:
        config = {"configurable": {"thread_id": thread_id}}
        final_state = await graph.ainvoke(initial_state, config=config)
        logger.info(
            f"G3 run done: application_id={application_id} "
            f"score={final_state.get('mock_critic_score')} "
            f"iters={final_state.get('mock_iteration')} "
            f"cost=${final_state.get('cost_usd_total', 0):.2f}"
        )
        return dict(final_state)
    except Exception as e:
        logger.exception(f"G3 run failed for application_id={application_id}")
        raise
