"""Minimal unified-diff parser.

We deliberately avoid `unidiff` to keep this dependency-free. The parser
only extracts what the rule engine needs: per-file added lines with their
final-file line numbers.

Supports both `git diff` style (with `diff --git` and `a/` `b/` prefixes)
and bare unified diff (`--- a` / `+++ b`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator

_DIFF_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_FILE_OLD = re.compile(r"^--- (?:a/)?(?P<path>.+?)(?:\s+\S+)?$")
_FILE_NEW = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+?)(?:\s+\S+)?$")
_HUNK = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


@dataclass
class AddedLine:
    """A line that was added in a diff."""
    file: str           # post-diff path (b/)
    line: int           # 1-indexed line number in the *new* file
    text: str           # raw text without the leading "+"


@dataclass
class DiffFile:
    """All added lines for one file in a diff.

    `added` is the canonical input for rules. `context` is the unchanged
    lines around the hunks — rules that need a few neighboring lines of
    context (e.g. SEC002 looking for allow_credentials=True near a newly
    added allow_origins=["*"]) can grep both.
    """
    path: str
    added: list[AddedLine] = field(default_factory=list)
    context: list[AddedLine] = field(default_factory=list)
    is_new: bool = False
    is_deleted: bool = False

    def hunk_text(self) -> str:
        """Joined text of added + context, in roughly diff order.

        Good enough for substring searches that need to span both.
        """
        merged = sorted(self.added + self.context, key=lambda a: a.line)
        return "\n".join(a.text for a in merged)


def parse_diff(diff_text: str) -> list[DiffFile]:
    """Parse a unified diff into a list of DiffFile.

    Robust to:
      - missing `diff --git` header (bare patches)
      - `/dev/null` for new/deleted files
      - hunks without explicit counts (`@@ -1 +1 @@`)
    """
    files: list[DiffFile] = []
    current: DiffFile | None = None
    new_lineno = 0
    in_hunk = False

    for raw_line in diff_text.splitlines():
        line = raw_line

        # --- diff --git header: opens a new file context
        m = _DIFF_HEADER.match(line)
        if m:
            current = DiffFile(path=m.group("b"))
            files.append(current)
            in_hunk = False
            continue

        # --- "--- a/path" or "--- /dev/null"
        m = _FILE_OLD.match(line)
        if m:
            if m.group("path") == "/dev/null" and current is not None:
                current.is_new = True
            in_hunk = False
            continue

        # --- "+++ b/path"  →  if there's no `diff --git`, this opens a file
        m = _FILE_NEW.match(line)
        if m:
            path = m.group("path")
            if path == "/dev/null":
                if current is not None:
                    current.is_deleted = True
            else:
                if current is None or current.path != path:
                    current = DiffFile(path=path)
                    files.append(current)
                else:
                    current.path = path
            in_hunk = False
            continue

        # --- "@@ -X,Y +A,B @@" hunk header
        m = _HUNK.match(line)
        if m:
            new_lineno = int(m.group("new_start"))
            in_hunk = True
            continue

        if not in_hunk or current is None:
            continue

        # --- body lines inside a hunk
        if line.startswith("+") and not line.startswith("+++"):
            current.added.append(
                AddedLine(file=current.path, line=new_lineno, text=line[1:])
            )
            new_lineno += 1
        elif line.startswith("-") and not line.startswith("---"):
            # removed-only line: doesn't advance new_lineno
            pass
        else:
            # context line (starts with " " or empty)
            ctx_text = line[1:] if line.startswith(" ") else line
            current.context.append(
                AddedLine(file=current.path, line=new_lineno, text=ctx_text)
            )
            new_lineno += 1

    return files


def all_added_lines(files: Iterable[DiffFile]) -> Iterator[AddedLine]:
    for f in files:
        if f.is_deleted:
            continue
        yield from f.added
