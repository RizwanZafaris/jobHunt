"""
agents/g9_run.py — Top-level entry point for invoking the G9 graph.

Used by:
  api/worker.py worker_run_g9   (RQ-queue-driven re-runs on CV updates)
  api/workspace.py /workspace/stories/build  (manual UI trigger)

Wraps:
  1. Resolve user_id (env override → seed UUID in single-user mode).
  2. Pre-render the master CV markdown via g9_io.render_master_cv_md.
  3. Build/get the compiled graph (cached process-wide).
  4. Invoke with a thread_id keyed on user_id+cv_hash so re-runs against
     the SAME CV resume from checkpoint, but a CV edit gives a new thread.
  5. Return the final state as a dict.

Cost note (per docs/GAP_CLOSURE_ROADMAP_2026_05_12.md §3.2): ~$0.20 per
run. There's no per-run cost cap today — the graph is fixed-shape with
no loops, so cost is bounded by structure.
"""
from __future__ import annotations
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Process-wide compiled graph (with checkpointer if available)
_GRAPH = None

# ─── LangGraph runaway guard (Phase 1, Finding 4) ───────────────────────
# G9 is a fixed-shape graph with no loops (cost is bounded by structure),
# so it never approaches LangGraph's default of 25 super-steps. We set an
# EXPLICIT limit anyway so a future edge that introduces a cycle fails
# with a controlled GraphRecursionError instead of silently looping.
# (The thread_id is already tenant-namespaced with user_id below.)
G9_RECURSION_LIMIT = 25


def _default_user_id() -> str:
    return os.environ.get(
        "RIZWAN_USER_ID",
        "00000000-0000-0000-0000-000000000001",
    )


def _get_graph():
    global _GRAPH
    if _GRAPH is None:
        from agents.g9_graph import build_g9_graph, get_postgres_checkpointer
        checkpointer = get_postgres_checkpointer()
        _GRAPH = build_g9_graph(checkpointer=checkpointer)
        logger.info(
            f"G9 graph compiled "
            f"(checkpointer={'on' if checkpointer else 'off'})"
        )
    return _GRAPH


async def run_g9(
    user_id: Optional[str] = None,
    master_cv_md: Optional[str] = None,
) -> dict:
    """Run G9 STAR+R extraction for one user. Returns the final state dict.

    Args:
        user_id: the candidate's UUID. Defaults to the env override.
        master_cv_md: pre-rendered CV markdown. If omitted, g9_io renders
            from profile_master + profile_experience.

    The graph emits 10-15 STAR+R stories, applies the persona-critic +
    identity-lock filters, and upserts surviving rows into story_bank.
    Returns a dict with `persisted_count`, `dropped_count`,
    `stories_persisted`, `stories_dropped`, `total_cost_usd`, and the
    transcript.
    """
    from agents.g9_io import hash_cv, render_master_cv_md

    uid = user_id or _default_user_id()
    md = master_cv_md if master_cv_md is not None else render_master_cv_md()
    cv_hash = hash_cv(md)
    logger.info(
        f"G9 run start: user_id={uid} cv_chars={len(md)} hash={cv_hash[:12]}"
    )

    initial_state: dict = {
        "user_id": uid,
        "master_cv_md": md,
        "source_cv_hash": cv_hash,
        "transcript": [],
        "total_cost_usd": 0.0,
        "total_latency_ms": 0,
        "persona_warnings": [],
    }
    # thread_id is already tenant-namespaced with user_id (uid prefix).
    thread_id = f"g9-{uid[:8]}-{cv_hash[:8]}"
    graph = _get_graph()

    try:
        # recursion_limit set EXPLICITLY (Phase 1, Finding 4).
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": G9_RECURSION_LIMIT,
        }
        final_state = await graph.ainvoke(initial_state, config=config)
        logger.info(
            f"G9 run done: user_id={uid} "
            f"persisted={final_state.get('persisted_count', 0)} "
            f"dropped={final_state.get('dropped_count', 0)} "
            f"cost=${final_state.get('total_cost_usd', 0):.3f}"
        )
        return dict(final_state)
    except Exception as e:
        logger.exception(f"G9 run failed for user_id={uid}")
        raise
