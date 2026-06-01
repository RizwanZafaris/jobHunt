"""Diff sources — feed the rule engine from a diff file, repo, or GitHub PR."""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from .diff import AddedLine, DiffFile, parse_diff
from .finding import Finding, ScanResult
from .rules import ALL_RULES, run_all


def scan_diff_text(diff_text: str, select: list[str] | None = None) -> ScanResult:
    files = parse_diff(diff_text)
    findings = run_all(files, select=select)
    return ScanResult(
        findings=findings,
        files_scanned=len(files),
        rules_run=len(ALL_RULES) if not select else len(select),
    )


def scan_diff_file(path: str | Path, select: list[str] | None = None) -> ScanResult:
    p = Path(path)
    return scan_diff_text(p.read_text(encoding="utf-8", errors="replace"), select=select)


def scan_repo(repo: str | Path, base: str = "main", select: list[str] | None = None) -> ScanResult:
    """Run `git diff <base>...HEAD` inside `repo` and scan the result."""
    repo_path = Path(repo).resolve()
    if not (repo_path / ".git").exists():
        raise RuntimeError(f"{repo_path} is not a git repo")
    out = subprocess.run(
        ["git", "diff", f"{base}...HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return scan_diff_text(out.stdout, select=select)


def scan_pr(pr_number: int, repo: str | None = None, select: list[str] | None = None) -> ScanResult:
    """Use the `gh` CLI to fetch a PR diff and scan it.

    Requires `gh auth login`. The repo argument is in `owner/name` form;
    if omitted, `gh` infers it from the current directory.
    """
    cmd = ["gh", "pr", "diff", str(pr_number)]
    if repo:
        cmd.extend(["--repo", repo])
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return scan_diff_text(out.stdout, select=select)


# ───────────────────── scan-tree (whole-repo audit) ──────────────────────
#
# Unlike scan-diff/scan-repo (which scan *changes*), scan-tree pretends
# every line of every tracked source file is a new addition. This is what
# you want for a one-shot audit of an existing codebase.

_DEFAULT_INCLUDES = ("**/*.py", "**/*.ts", "**/*.tsx")
_DEFAULT_EXCLUDES = (
    "**/node_modules/**",
    "**/.next/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
    "**/dist/**",
    "**/build/**",
    "**/.git/**",
    "**/migrations/**",          # SQL migrations, not Python
    "tools/pr_job_scan/**",      # the scanner stores its own rule patterns
)


def _gather_files(
    root: Path,
    includes: tuple[str, ...] = _DEFAULT_INCLUDES,
    excludes: tuple[str, ...] = _DEFAULT_EXCLUDES,
) -> list[Path]:
    out: list[Path] = []
    for pat in includes:
        for p in root.rglob(pat[3:] if pat.startswith("**/") else pat):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, ex) for ex in excludes):
                continue
            out.append(p)
    return sorted(set(out))


def _file_to_difffile(root: Path, file_path: Path) -> DiffFile:
    """Wrap a live file as if every line were freshly added."""
    rel = file_path.relative_to(root).as_posix()
    text = file_path.read_text(encoding="utf-8", errors="replace")
    added = [
        AddedLine(file=rel, line=i + 1, text=line)
        for i, line in enumerate(text.splitlines())
    ]
    return DiffFile(path=rel, added=added, is_new=False)


def scan_tree(
    root: str | Path,
    select: list[str] | None = None,
    includes: tuple[str, ...] = _DEFAULT_INCLUDES,
    excludes: tuple[str, ...] = _DEFAULT_EXCLUDES,
) -> ScanResult:
    """Audit every source file under `root` as if it were a fresh diff."""
    root_path = Path(root).resolve()
    files = _gather_files(root_path, includes, excludes)
    diff_files = [_file_to_difffile(root_path, f) for f in files]
    findings = run_all(diff_files, select=select)
    return ScanResult(
        findings=findings,
        files_scanned=len(diff_files),
        rules_run=len(ALL_RULES) if not select else len(select),
    )
