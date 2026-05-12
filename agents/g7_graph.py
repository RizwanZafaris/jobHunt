"""
agents/g7_graph.py — LangGraph topology for the G7 Application Graph
(Tier 3 / crown jewel).

Layout:

    form_scanner            (code, Playwright)
            ↓
    question_classifier     (Sonnet 4.6)
            ↓
    answer_retriever        (code + RAG)
            ↓
    answer_generator        (Sonnet 4.6) ◄────────────────────────┐
            ↓                                                     │ retry
            FAN-OUT (parallel sibling joins via merge):           │ (max 1)
            persona_critic   (Sonnet 4.6)                         │
            ↓ (join)                                              │
    merge_critique         (code; sets needs_generator_retry on   │
                            fabricated metrics / banned keywords) │
            ↓                                                     │
        needs_generator_retry? ─── true ─────────────────────────►│
            │  false
            ▼
    human_review            (interrupt — graph PAUSES)
            ↓ (resume)
    form_filler             (code, Playwright; stops BEFORE submit)
            ↓
    persist                 (code; identity-lock + G6 auto-enrol)
            ↓
        END

HITL implementation:
  - We compile with a Postgres checkpointer (passed in) so the state is
    durable across the interrupt() pause. The thread_id used by the
    checkpointer is the application_answer_id UUID — that's how the
    /approve endpoint resumes.
  - The interrupt itself is INSIDE human_review_node (see g7_nodes.py).
  - When the user POSTs /approve, api/g7.py calls
      graph.invoke(Command(resume={"approvals": [...]}),
                  config={"configurable": {"thread_id": application_answer_id}})
    which threads the user_approvals payload into the resume value of
    interrupt() and continues to form_filler.

Pattern mirrors resume_agents.g2_graph (writer → ats_critic_a +
ats_critic_b + persona_critic → merge → orchestrator → writer loop) and
agents/g6_graph (draft_generator + persona_critic → merge → urgency).
G7 has ONE parallel critic axis (persona_critic) + ONE HITL interrupt.
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.g7_nodes import (
    answer_generator_node,
    answer_retriever_node,
    form_filler_node,
    form_scanner_node,
    human_review_node,
    merge_critique_node,
    persist_node,
    persona_critic_node,
    question_classifier_node,
)
from agents.g7_state import ApplicationFormState

logger = logging.getLogger(__name__)


def _route_after_merge(state: ApplicationFormState) -> str:
    """Loop back to answer_generator when persona flagged issues AND we
    have retries left, otherwise proceed to human_review."""
    if state.get("needs_generator_retry"):
        return "answer_generator"
    return "human_review"


def build_g7_graph(checkpointer: Optional[object] = None):
    """Compile the G7 LangGraph state machine.

    The HITL interrupt requires a checkpointer. We pass None in tests
    (which short-circuit BEFORE the interrupt) but production callers
    (g7_io.start_g7_run) must supply one.

    Pattern note: when checkpointer is None, langgraph.compile() raises
    when an `interrupt()` actually fires; tests use a stub MemorySaver
    so the interrupt path is exercise-able without Postgres.
    """
    from langgraph.graph import END, StateGraph

    g = StateGraph(ApplicationFormState)
    g.add_node("form_scanner",        form_scanner_node)
    g.add_node("question_classifier", question_classifier_node)
    g.add_node("answer_retriever",    answer_retriever_node)
    g.add_node("answer_generator",    answer_generator_node)
    g.add_node("persona_critic",      persona_critic_node)
    g.add_node("merge_critique",      merge_critique_node)
    g.add_node("human_review",        human_review_node)
    g.add_node("form_filler",         form_filler_node)
    g.add_node("persist",             persist_node)

    g.set_entry_point("form_scanner")
    g.add_edge("form_scanner",        "question_classifier")
    g.add_edge("question_classifier", "answer_retriever")
    g.add_edge("answer_retriever",    "answer_generator")

    # Parallel fan-out: answer_generator → persona_critic + merge wait point.
    # LangGraph joins on merge_critique automatically because BOTH edges
    # land there.
    g.add_edge("answer_generator", "persona_critic")
    g.add_edge("persona_critic",   "merge_critique")

    g.add_conditional_edges(
        "merge_critique",
        _route_after_merge,
        {
            "answer_generator": "answer_generator",
            "human_review":     "human_review",
        },
    )

    g.add_edge("human_review", "form_filler")
    g.add_edge("form_filler",  "persist")
    g.add_edge("persist",      END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()
