"""Finding + Severity model. Pure stdlib so this stays lightweight."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Optional


class Severity(IntEnum):
    LOW = 1
    MED = 2
    HIGH = 3

    @classmethod
    def parse(cls, raw: str) -> "Severity":
        raw = raw.strip().upper()
        if raw in ("MED", "MEDIUM"):
            return cls.MED
        return cls[raw]

    def label(self) -> str:
        return {1: "low", 2: "med", 3: "high"}[int(self)]


@dataclass
class Finding:
    """A single rule violation in a single location."""
    rule_id: str          # e.g. "SEC001"
    title: str            # human summary
    severity: Severity
    file: str
    line: int             # 1-indexed; 0 means "whole file"
    snippet: str = ""     # the offending line, stripped
    why: str = ""         # short rationale
    fix: str = ""         # suggested fix or doc link

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.label()
        return d

    def cli_line(self) -> str:
        # `gcc`-style for editor jump-to-line
        return (
            f"{self.file}:{self.line}: [{self.severity.label()}] "
            f"{self.rule_id} {self.title}"
        )


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    files_scanned: int = 0
    rules_run: int = 0

    def by_severity(self, sev: Severity) -> list[Finding]:
        return [f for f in self.findings if f.severity == sev]

    def max_severity(self) -> Optional[Severity]:
        if not self.findings:
            return None
        return max(f.severity for f in self.findings)
