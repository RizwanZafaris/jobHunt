"""Agents package for the Job Hunt AI System v2.

Lazy module attribute resolution (PEP 562). This means:

  from agents import CompanyAgent          # works, lazy-loads on first access
  from agents.company_agent import CompanyAgent   # also works (preferred)
  from agents.llm_router import get_router        # router-only — no transitive
                                                  # supabase / anthropic loads

This matters because importing one agent used to chain-import every other
agent + db/client → supabase, which made testing the router or experimenting
in scripts unnecessarily heavy.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # only for IDEs / type checkers
    from agents.base_agent import BaseAgent
    from agents.job_scout_agent import JobScoutAgent
    from agents.company_agent import CompanyAgent
    from agents.rizwan_agent import RizwanAgent
    from agents.resume_builder_agent import ResumeBuilderAgent
    from agents.interview_agent import InterviewAgent
    from agents.boss_agent import BossAgent
    from agents.networking_agent import NetworkingAgent
    from agents.salary_research_agent import SalaryResearchAgent
    from agents.application_tracker_agent import ApplicationTrackerAgent
    from agents.persona_synthesizer import PersonaSynthesizer
    from agents.cost_alerter import CostAlerter

# Map exported name → (module path, attribute name)
_LAZY_ATTRS = {
    "BaseAgent":              ("agents.base_agent",              "BaseAgent"),
    "JobScoutAgent":          ("agents.job_scout_agent",         "JobScoutAgent"),
    "CompanyAgent":           ("agents.company_agent",           "CompanyAgent"),
    "RizwanAgent":            ("agents.rizwan_agent",            "RizwanAgent"),
    "ResumeBuilderAgent":     ("agents.resume_builder_agent",    "ResumeBuilderAgent"),
    "InterviewAgent":         ("agents.interview_agent",         "InterviewAgent"),
    "BossAgent":              ("agents.boss_agent",              "BossAgent"),
    "NetworkingAgent":        ("agents.networking_agent",        "NetworkingAgent"),
    "SalaryResearchAgent":    ("agents.salary_research_agent",   "SalaryResearchAgent"),
    "ApplicationTrackerAgent":("agents.application_tracker_agent","ApplicationTrackerAgent"),
    "PersonaSynthesizer":     ("agents.persona_synthesizer",     "PersonaSynthesizer"),
    "CostAlerter":            ("agents.cost_alerter",            "CostAlerter"),
}

__all__ = sorted(_LAZY_ATTRS.keys())


def __getattr__(name: str):
    """PEP 562 lazy attribute resolution."""
    if name in _LAZY_ATTRS:
        import importlib
        module_path, attr = _LAZY_ATTRS[name]
        module = importlib.import_module(module_path)
        value = getattr(module, attr)
        globals()[name] = value  # cache for subsequent access
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
