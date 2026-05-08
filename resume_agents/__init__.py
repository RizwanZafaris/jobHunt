"""
resume_agents — G2 Resume Builder LangGraph (Phase 1).

This package contains the multi-LLM, multi-node graph that builds a
95+ ATS-passing tailored resume + cover email per job. Triggered manually
from the dashboard at score ≥ 85.

See docs/G2_RESUME_BUILDER_GRAPH.md for the full design.

Module layout:
  g2_state.py      ResumeState TypedDict + helpers
  g2_io.py         Load profile + persona + past transcripts (with cold-start)
  g2_nodes.py      The 12 graph nodes (one function each)
  g2_graph.py      Topology + compile() with Postgres checkpointer
  g2_run.py        Top-level run_g2_graph(job_id) entry point used by pipeline.py

Lazy imports — importing this package shouldn't pull in langgraph or any
LLM clients until something actually invokes the graph.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from resume_agents.g2_state import ResumeState
    from resume_agents.g2_graph import build_g2_graph
    from resume_agents.g2_run import run_g2_graph

_LAZY_ATTRS = {
    "ResumeState":     ("resume_agents.g2_state", "ResumeState"),
    "build_g2_graph":  ("resume_agents.g2_graph", "build_g2_graph"),
    "run_g2_graph":    ("resume_agents.g2_run",   "run_g2_graph"),
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
