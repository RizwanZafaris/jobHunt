"""Renderers — text, JSON, SARIF."""
from __future__ import annotations

import json
from typing import Iterable

from .finding import Finding, ScanResult, Severity


def render_text(result: ScanResult, color: bool = True) -> str:
    if not result.findings:
        return (
            f"PR Job Scan: clean ({result.files_scanned} files, "
            f"{result.rules_run} rules)\n"
        )

    def sev_tag(sev: Severity) -> str:
        tag = f"[{sev.label().upper():>4}]"
        if not color:
            return tag
        c = {Severity.HIGH: "\033[31m", Severity.MED: "\033[33m", Severity.LOW: "\033[36m"}[sev]
        return f"{c}{tag}\033[0m"

    lines: list[str] = []
    by_sev: dict[Severity, int] = {}
    for f in result.findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        lines.append(f"{sev_tag(f.severity)} {f.rule_id}  {f.file}:{f.line}")
        lines.append(f"        {f.title}")
        if f.snippet:
            lines.append(f"        |  {f.snippet}")
        if f.why:
            lines.append(f"        why: {f.why}")
        if f.fix:
            lines.append(f"        fix: {f.fix}")
        lines.append("")

    summary = (
        f"Summary: {by_sev.get(Severity.HIGH, 0)} HIGH, "
        f"{by_sev.get(Severity.MED, 0)} MED, "
        f"{by_sev.get(Severity.LOW, 0)} LOW "
        f"(across {result.files_scanned} files)"
    )
    lines.append(summary)
    return "\n".join(lines) + "\n"


def render_json(result: ScanResult) -> str:
    return json.dumps(
        {
            "files_scanned": result.files_scanned,
            "rules_run": result.rules_run,
            "findings": [f.to_dict() for f in result.findings],
        },
        indent=2,
    )


def render_sarif(result: ScanResult) -> str:
    """SARIF 2.1.0 — drops into GitHub code-scanning."""
    rules_seen: dict[str, dict] = {}
    runs_results = []
    for f in result.findings:
        rules_seen.setdefault(
            f.rule_id,
            {
                "id": f.rule_id,
                "name": f.rule_id,
                "shortDescription": {"text": f.title},
                "fullDescription": {"text": f.why or f.title},
                "defaultConfiguration": {
                    "level": {Severity.HIGH: "error", Severity.MED: "warning", Severity.LOW: "note"}[f.severity]
                },
            },
        )
        runs_results.append({
            "ruleId": f.rule_id,
            "level": {Severity.HIGH: "error", Severity.MED: "warning", Severity.LOW: "note"}[f.severity],
            "message": {"text": f"{f.title} — {f.why}".strip(" —")},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": f.file},
                    "region": {"startLine": max(1, f.line)},
                }
            }],
        })
    return json.dumps({
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {
                "driver": {
                    "name": "pr_job_scan",
                    "informationUri": "https://github.com/RizwanZafaris/jobHunt",
                    "rules": list(rules_seen.values()),
                }
            },
            "results": runs_results,
        }],
    }, indent=2)
