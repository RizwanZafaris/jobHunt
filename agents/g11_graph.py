"""
agents/g11_graph.py — LangGraph topology for the G11 Voice Calibration Graph.

Layout (linear, no fan-out, no critic loop — voice extraction is one-shot):

    style_extractor   (Sonnet 4.6, prompt-cached system)
          ↓
    voice_profiler    (Sonnet 4.6, prompt-cached system)
          ↓
    persist           (pure code)
          ↓
        END

Mirrors agents.g9_graph (extract → format → critic → persist) but with
ONE LLM gate replaced by code — voice attributes are tightly constrained
by their source samples (every preferred-word / opening MUST appear in
the corpus), and the persist node enforces source_sample_ids is
non-empty. No iterative LLM gate is needed.
"""
from __future__ import annotations

import logging
from typing import Optional

from agents.g11_nodes import (
    persist_node,
    style_extractor_node,
    voice_profiler_node,
)
from agents.g11_state import VoiceCalibrationState

logger = logging.getLogger(__name__)


def build_g11_graph(checkpointer: Optional[object] = None):
    """Compile the G11 LangGraph state machine.

    checkpointer is optional. We don't currently checkpoint G11 because
    each run is short (<10s) and idempotent at the user_id level — re-
    running just overwrites voice_calibration with a fresher snapshot.
    """
    from langgraph.graph import END, StateGraph

    g = StateGraph(VoiceCalibrationState)
    g.add_node("style_extractor", style_extractor_node)
    g.add_node("voice_profiler",  voice_profiler_node)
    g.add_node("persist",         persist_node)

    g.set_entry_point("style_extractor")
    g.add_edge("style_extractor", "voice_profiler")
    g.add_edge("voice_profiler",  "persist")
    g.add_edge("persist",         END)

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()
