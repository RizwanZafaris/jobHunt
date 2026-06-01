"""PR Job Scan — a focused static-analysis scanner for the jobHunt bug classes.

Public surface:
    from pr_job_scan import scan_diff_text, scan_repo, Finding, Severity
"""
from __future__ import annotations

from .finding import Finding, Severity
from .sources import scan_diff_text, scan_repo, scan_pr

__all__ = ["Finding", "Severity", "scan_diff_text", "scan_repo", "scan_pr"]
__version__ = "0.1.0"
