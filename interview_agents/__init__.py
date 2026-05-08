"""
interview_agents — G3 Interview Prep LangGraph (Phase 2).

This package contains the multi-LLM, multi-node graph that builds a
persona-aware interview prep pack per application/round. Triggered from
the dashboard once the user moves the application to interview status.

See docs/G3_INTERVIEW_PREP_GRAPH.md for the full design.

Module layout:
  g3_state.py      InterviewPrepState TypedDict + helpers
  g3_io.py         Load application + job + persona + story_bank + outcomes
  g3_nodes.py      The 9 graph nodes (one function each, 7 logical nodes)
  g3_graph.py      Topology + compile() with Postgres checkpointer
  g3_run.py        Top-level run_g3_graph(application_id) entry point used by api/server.py

Lazy imports — importing this package shouldn't pull in langgraph or any
LLM clients until something actually invokes the graph.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from interview_agents.g3_state import InterviewPrepState
    from interview_agents.g3_graph import build_g3_graph
    from interview_agents.g3_run import run_g3_graph

_LAZY_ATTRS = {
    "InterviewPrepState": ("interview_agents.g3_state", "InterviewPrepState"),
    "build_g3_graph":     ("interview_agents.g3_graph", "build_g3_graph"),
    "run_g3_graph":       ("interview_agents.g3_run",   "run_g3_graph"),
}

__all__ = sorted(_LAZY_ATTRS.keys())


def __getattr__(name: str):
    if name in _LAZY_ATTRS:
        import importlib
        module_path, attr = _LAZY_ATTRS[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
