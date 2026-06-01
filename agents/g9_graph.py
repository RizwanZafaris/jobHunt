"""
agents/g9_graph.py — LangGraph topology for the G9 STAR+R Story Extractor.

Layout:

    entry
      │
      ▼
    experience_parser   (Sonnet 4.6 — 10-15 candidate passages)
      │
      ▼
    star_formatter      (Sonnet 4.6 — batched STAR+R JSON, prompt-cached)
      │
      ▼
    persona_critic      (Sonnet 4.6 — fabrication flags)
      │
      ▼
    persist             (pure code — identity-lock + embed + upsert)
      │
      ▼
    END

This is intentionally simpler than G2/G3 — no fan-out, no writer-critic
loop. G9 is an extract-and-persist pipeline with one critic gate; the
roadmap's principle "persona-as-critic" is satisfied by persona_critic
without paying the cost of multi-LLM fan-out for this kind of bulk
structured extraction.
"""
from __future__ import annotations
import logging
from typing import Optional

from agents.g9_state import StoryExtractionState
from agents.g9_nodes import (
    entry_node,
    experience_parser_node,
    star_formatter_node,
    persona_critic_node,
    persist_node,
)

logger = logging.getLogger(__name__)


def build_g9_graph(checkpointer: Optional[object] = None):
    """Compile the G9 LangGraph state machine.

    checkpointer: an optional langgraph checkpointer (Postgres-backed in
    production). When None, the graph runs in-memory only — useful for
    tests but loses crash-recovery.
    """
    from langgraph.graph import StateGraph, END

    g = StateGraph(StoryExtractionState)
    g.add_node("entry",              entry_node)
    g.add_node("experience_parser",  experience_parser_node)
    g.add_node("star_formatter",     star_formatter_node)
    g.add_node("persona_critic",     persona_critic_node)
    g.add_node("persist",            persist_node)

    g.set_entry_point("entry")
    g.add_edge("entry",              "experience_parser")
    g.add_edge("experience_parser",  "star_formatter")
    g.add_edge("star_formatter",     "persona_critic")
    g.add_edge("persona_critic",     "persist")
    g.add_edge("persist",            END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


def get_postgres_checkpointer():
    """Build a langgraph-checkpoint-postgres saver. None on graceful fallback."""
    import os
    conn_str = os.environ.get("SUPABASE_DB_URL") or os.environ.get("POSTGRES_URL")
    if not conn_str:
        logger.info(
            "G9 checkpointer: no SUPABASE_DB_URL / POSTGRES_URL set — "
            "running graph without checkpointing"
        )
        return None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(conn_str)
    except ImportError:
        logger.warning(
            "G9 checkpointer: langgraph-checkpoint-postgres not installed; "
            "running without checkpointing"
        )
        return None
