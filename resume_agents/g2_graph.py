"""
resume_agents/g2_graph.py — LangGraph topology for the Resume Builder.

Layout:
    entry
      ├── insider_expert ──┐
      └── advocate ────────┤
                           ▼
                      meta_critic
                           │
                           ▼
                        writer  ◄──────┐  loop (max iter)
                           │           │
              ┌────────────┴───────────┐
              │                        │
        ats_critic_a            ats_critic_b
              │                        │
              └─────────┬──────────────┘
                        ▼
                  merge_critique
                        │
                        ▼
                  orchestrator ──┐ converged → polisher → cover_email → export → END
                        │        │
                        └────────┘ not converged → writer

Two parallel fan-outs (entry → expert+advocate, writer → critic_a+critic_b)
make this a true graph rather than a chain. LangGraph waits for both branches
before proceeding to the join node.
"""
from __future__ import annotations
import logging
from typing import Optional

from resume_agents.g2_state import ResumeState
from resume_agents.g2_nodes import (
    entry_node,
    insider_expert_node,
    advocate_node,
    meta_critic_node,
    writer_node,
    ats_critic_a_node,
    ats_critic_b_node,
    merge_critique_node,
    orchestrator_node,
    polisher_node,
    cover_email_node,
    export_node,
)

logger = logging.getLogger(__name__)


def _route_after_orchestrator(state: ResumeState) -> str:
    """Conditional edge: loop back to writer or proceed to polisher."""
    return "polisher" if state.get("converged") else "writer"


def build_g2_graph(checkpointer: Optional[object] = None):
    """
    Compile the LangGraph state machine.

    checkpointer: an optional langgraph checkpointer (Postgres-backed in
    production). When None, the graph runs in-memory only — useful for
    tests but loses crash-recovery.
    """
    from langgraph.graph import StateGraph, END

    g = StateGraph(ResumeState)

    g.add_node("entry",          entry_node)
    g.add_node("insider_expert", insider_expert_node)
    g.add_node("advocate",       advocate_node)
    g.add_node("meta_critic",    meta_critic_node)
    g.add_node("writer",         writer_node)
    g.add_node("ats_critic_a",   ats_critic_a_node)
    g.add_node("ats_critic_b",   ats_critic_b_node)
    g.add_node("merge_critique", merge_critique_node)
    g.add_node("orchestrator",   orchestrator_node)
    g.add_node("polisher",       polisher_node)
    g.add_node("cover_email",    cover_email_node)
    g.add_node("export",         export_node)

    g.set_entry_point("entry")

    # Parallel branch 1: entry fans out to expert + advocate
    g.add_edge("entry", "insider_expert")
    g.add_edge("entry", "advocate")

    # Both feed meta_critic (LangGraph waits for both)
    g.add_edge("insider_expert", "meta_critic")
    g.add_edge("advocate",       "meta_critic")

    g.add_edge("meta_critic", "writer")

    # Parallel branch 2: writer fans out to critic_a + critic_b
    g.add_edge("writer", "ats_critic_a")
    g.add_edge("writer", "ats_critic_b")
    g.add_edge("ats_critic_a", "merge_critique")
    g.add_edge("ats_critic_b", "merge_critique")

    g.add_edge("merge_critique", "orchestrator")

    # Conditional: loop or proceed
    g.add_conditional_edges(
        "orchestrator",
        _route_after_orchestrator,
        {"writer": "writer", "polisher": "polisher"},
    )

    g.add_edge("polisher",    "cover_email")
    g.add_edge("cover_email", "export")
    g.add_edge("export",      END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


def get_postgres_checkpointer():
    """
    Build a langgraph-checkpoint-postgres saver against the Supabase
    Postgres connection string. Returns None if the env var isn't set or
    the package isn't installed (graceful degradation for local dev).
    """
    import os
    conn_str = os.environ.get("SUPABASE_DB_URL") or os.environ.get("POSTGRES_URL")
    if not conn_str:
        logger.info(
            "G2 checkpointer: no SUPABASE_DB_URL / POSTGRES_URL set — "
            "running graph without checkpointing (no crash recovery)"
        )
        return None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(conn_str)
    except ImportError:
        logger.warning(
            "G2 checkpointer: langgraph-checkpoint-postgres not installed; "
            "running without checkpointing"
        )
        return None
