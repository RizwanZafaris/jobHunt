"""Agents package for the Job Hunt AI System v2."""

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

__all__ = [
    "BaseAgent",
    "JobScoutAgent",
    "CompanyAgent",
    "RizwanAgent",
    "ResumeBuilderAgent",
    "InterviewAgent",
    "BossAgent",
    "NetworkingAgent",
    "SalaryResearchAgent",
    "ApplicationTrackerAgent",
]
