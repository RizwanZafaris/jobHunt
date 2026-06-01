"""
agents/g6_graph.py — LangGraph topology for the G6 Follow-up Graph.

Layout:

    cadence_checker   (code, short-circuits when not URGENT/OVERDUE)
          ↓
    context_builder   (code + pgvector RAG)
          ↓
    draft_generator   (Sonnet 4.6, prompt-cached system) ◄────────┐
          ↓                                                       │ loop
        FAN-OUT (parallel siblings, LangGraph waits for both):    │ (max 2)
          ├── tone_calibrator                                     │
          └── persona_critic                                      │
          ↓ (join)                                                │
    merge_critique   (code; sets needs_retry if any critic said   │
                     "revise" AND attempts left)                  │
          ↓                                                       │
       needs_retry? ─── true ───────────────────────────────────► │
          │  false
          ▼
    urgency_scorer   (code; may downgrade URGENT→waiting)
          ↓
    persist_and_notify   (code; writes follow_up_cadence row)
          ↓
        END

Pattern mirrors resume_agents.g2_graph (writer → ats_a + ats_b +
persona_critic → merge → orchestrator → writer loop). G6 has ONE
critic-loop axis instead of G2's writer-critic loop, and only TWO
parallel critics instead of three.
"""
from __future__ import annotations
import logging
from typing import Optional

from agents.g6_nodes import (
    cadence_checker_node,
    context_builder_node,
    draft_generator_node,
    merge_critique_node,
    persist_and_notify_node,
    persona_critic_node,
    tone_calibrator_node,
    urgency_scorer_node,
)
from agents.g6_state import FollowUpState

logger = logging.getLogger(__name__)


def _route_after_cadence(state: FollowUpState) -> str:
    """Short-circuit: when cadence_checker says no draft today, jump straight
    to persist (which records the skip row) and skip the LLM nodes entirely.

    This is what makes the cron cost-bounded: of N active applications,
    only those past their cadence window incur the ~$0.12 LLM spend. The
    rest cost $0 because they short-circuit here.
    """
    if state.get("short_circuit"):
        return "persist_and_notify"
    return "context_builder"


def _route_after_merge(state: FollowUpState) -> str:
    """Loop back to draft_generator when either critic asked for revisions
    AND we have attempts left, otherwise proceed to urgency_scorer."""
    if state.get("needs_retry"):
        return "draft_generator"
    return "urgency_scorer"


def build_g6_graph(checkpointer: Optional[object] = None):
    """Compile the G6 LangGraph state machine.

    checkpointer is optional; we don't currently checkpoint G6 because each
    run is short (<5s) and idempotent at the application_id level. Adding
    a checkpointer is straightforward — see resume_agents.g2_graph for the
    Postgres pattern.
    """
    from langgraph.graph import END, StateGraph

    g = StateGraph(FollowUpState)
    g.add_node("cadence_checker",     cadence_checker_node)
    g.add_node("context_builder",     context_builder_node)
    g.add_node("draft_generator",     draft_generator_node)
    g.add_node("tone_calibrator",     tone_calibrator_node)
    g.add_node("persona_critic",      persona_critic_node)
    g.add_node("merge_critique",      merge_critique_node)
    g.add_node("urgency_scorer",      urgency_scorer_node)
    g.add_node("persist_and_notify",  persist_and_notify_node)

    g.set_entry_point("cadence_checker")

    g.add_conditional_edges(
        "cadence_checker",
        _route_after_cadence,
        {
            "context_builder":    "context_builder",
            "persist_and_notify": "persist_and_notify",
        },
    )

    g.add_edge("context_builder", "draft_generator")

    # Parallel fan-out: draft_generator → tone_calibrator + persona_critic.
    # LangGraph waits for both before running merge_critique.
    g.add_edge("draft_generator", "tone_calibrator")
    g.add_edge("draft_generator", "persona_critic")
    g.add_edge("tone_calibrator", "merge_critique")
    g.add_edge("persona_critic",  "merge_critique")

    g.add_conditional_edges(
        "merge_critique",
        _route_after_merge,
        {
            "draft_generator":  "draft_generator",
            "urgency_scorer":   "urgency_scorer",
        },
    )

    g.add_edge("urgency_scorer", "persist_and_notify")
    g.add_edge("persist_and_notify", END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()
