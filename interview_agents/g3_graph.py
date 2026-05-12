"""
interview_agents/g3_graph.py — LangGraph topology for the Interview Prep graph.

Layout (Tier 2 §4.2 — story_bank integration nodes marked NEW):

    entry
      ├── behavioral_predictor ──┐
      ├── technical_predictor ───┤
      └── domain_predictor  ─────┤
                                 ▼
                          merge_questions
                                 │
                                 ▼
                       story_retriever        (NEW — pure code, $0)
                                 │
                                 ▼
                        gap_analyzer          (NEW — Sonnet 4.6, ~$0.05)
                                 │
                                 ▼
                       star_story_matcher
                                 │
                                 ▼
                     mock_interview_loop  (single-node internal loop)
                                 │
                                 ▼
                         compile_prep_pack
                                 │
                                 ▼
                   prep_pack_persona_critic   (NEW — identity-lock, $0)
                                 │
                                 ▼
                              export
                                 │
                                 ▼
                                END

Three-way fan-out at entry → predictors makes this a true graph rather than
a chain. LangGraph waits for all three branches before proceeding to
merge_questions.

story_retriever_node + gap_analyzer_node are the Tier 2 G3 wedge: they
auto-pull the best STAR from G9's story_bank for each likely behavioural
question and Sonnet-coach reframing on low-similarity matches.

prep_pack_persona_critic_node enforces the identity-lock (engineering
principle 3) on whatever competencies the gap_analyzer surfaced.

The mock_interview_loop is a single node that internally iterates — see
the comment in g3_nodes.py for why we chose single-node over a
writer-critic conditional-edge topology like G2.
"""
from __future__ import annotations
import logging
from typing import Optional

from interview_agents.g3_state import InterviewPrepState
from interview_agents.g3_nodes import (
    entry_node,
    behavioral_predictor_node,
    technical_predictor_node,
    domain_predictor_node,
    merge_questions_node,
    story_retriever_node,
    gap_analyzer_node,
    star_story_matcher_node,
    mock_interview_loop_node,
    compile_prep_pack_node,
    prep_pack_persona_critic_node,
    export_node,
)

logger = logging.getLogger(__name__)


def build_g3_graph(checkpointer: Optional[object] = None):
    """
    Compile the LangGraph state machine.

    checkpointer: an optional langgraph checkpointer (Postgres-backed in
    production). When None, the graph runs in-memory only — useful for
    tests but loses crash-recovery.
    """
    from langgraph.graph import StateGraph, END

    g = StateGraph(InterviewPrepState)

    g.add_node("entry",                       entry_node)
    g.add_node("behavioral_predictor",        behavioral_predictor_node)
    g.add_node("technical_predictor",         technical_predictor_node)
    g.add_node("domain_predictor",            domain_predictor_node)
    g.add_node("merge_questions",             merge_questions_node)
    # Tier 2 G3 §4.2 — story_bank integration
    g.add_node("story_retriever",             story_retriever_node)
    g.add_node("gap_analyzer",                gap_analyzer_node)
    g.add_node("star_story_matcher",          star_story_matcher_node)
    g.add_node("mock_interview_loop",         mock_interview_loop_node)
    g.add_node("compile_prep_pack",           compile_prep_pack_node)
    g.add_node("prep_pack_persona_critic",    prep_pack_persona_critic_node)
    g.add_node("export",                      export_node)

    g.set_entry_point("entry")

    # Three-way parallel fan-out: entry → 3 predictors
    g.add_edge("entry", "behavioral_predictor")
    g.add_edge("entry", "technical_predictor")
    g.add_edge("entry", "domain_predictor")

    # All three feed merge (LangGraph waits for all three)
    g.add_edge("behavioral_predictor", "merge_questions")
    g.add_edge("technical_predictor",  "merge_questions")
    g.add_edge("domain_predictor",     "merge_questions")

    # Tier 2 §4.2: insert story_retriever + gap_analyzer between
    # merge_questions and the existing star_story_matcher node. The
    # retrieved_stories / story_gaps state fields then flow through the
    # mock-loop and into compile_prep_pack, where the template renders
    # them as "Auto-Retrieved STAR Stories" with gap callouts.
    g.add_edge("merge_questions",            "story_retriever")
    g.add_edge("story_retriever",            "gap_analyzer")
    g.add_edge("gap_analyzer",               "star_story_matcher")
    g.add_edge("star_story_matcher",         "mock_interview_loop")
    g.add_edge("mock_interview_loop",        "compile_prep_pack")
    # Tier 2 §4.2: persona_critic gate before export to enforce identity
    # lock on the gap_analyzer's output.
    g.add_edge("compile_prep_pack",          "prep_pack_persona_critic")
    g.add_edge("prep_pack_persona_critic",   "export")
    g.add_edge("export",                     END)

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
            "G3 checkpointer: no SUPABASE_DB_URL / POSTGRES_URL set — "
            "running graph without checkpointing (no crash recovery)"
        )
        return None
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        return AsyncPostgresSaver.from_conn_string(conn_str)
    except ImportError:
        logger.warning(
            "G3 checkpointer: langgraph-checkpoint-postgres not installed; "
            "running without checkpointing"
        )
        return None
