"""
CI guard (P2-a): no blocking supabase `.execute()` inside an async handler.

WHY THIS EXISTS
---------------
FastAPI runs ``async def`` path operations ON the event loop, so a *synchronous*
supabase-py ``.execute()`` there blocks the whole loop for the duration of the
DB round-trip — every other in-flight request on that worker stalls behind it
(docs/SCALABILITY.md P0 #3). Sync ``def`` handlers are NOT affected: FastAPI
already runs them in a worker thread, so their ``.execute()`` never touches the
loop. That's why this guard only flags async-enclosed sites.

The async seam in ``db/client.py`` (``aexecute`` + the ``a*`` twins) offloads
that blocking call to the threadpool. P2-a converts async handlers
``.execute()`` -> ``await aexecute(...)`` router by router.

HOW THE GUARD WORKS
-------------------
It scans ``api/*.py`` for ``.execute()`` whose *nearest enclosing function* is
async, and fails if a site appears that is NOT in :data:`ALLOWLIST`. The
allowlist is the set of *not-yet-converted* sites, keyed by
``"<file>::<qualname>"`` (stable across line-number churn). Two ways it bites:

  * a NEW blocking ``.execute()`` is added to an async handler  -> fails
  * a previously-converted site regresses back to raw ``.execute()`` -> fails

As P2-a converts handlers we delete their keys from :data:`ALLOWLIST`. When the
allowlist is empty, every async handler is on the seam and the door is shut.

Run the full report::

    python tests/test_no_blocking_execute_in_async.py
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent / "api"


class _Scanner(ast.NodeVisitor):
    """Collect ``.execute()`` calls whose nearest enclosing function is async."""

    def __init__(self) -> None:
        self._func_async: list[bool] = []   # is-async flag per enclosing function
        self._names: list[str] = []         # qualname components (classes + funcs)
        self.sites: list[tuple[str, int]] = []  # (qualname, lineno)

    def _visit_func(self, node: ast.AST, is_async: bool) -> None:
        self._func_async.append(is_async)
        self._names.append(node.name)  # type: ignore[attr-defined]
        self.generic_visit(node)
        self._names.pop()
        self._func_async.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node, False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node, True)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._names.append(node.name)
        self.generic_visit(node)
        self._names.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "execute"
            and self._func_async
            and self._func_async[-1]  # nearest enclosing function is async
        ):
            self.sites.append((".".join(self._names), node.lineno))
        self.generic_visit(node)


def find_blocking_sites() -> dict[str, list[tuple[str, int]]]:
    """Map ``api`` filename -> list of (qualname, lineno) blocking sites."""
    out: dict[str, list[tuple[str, int]]] = {}
    for path in sorted(API_DIR.glob("*.py")):
        # Skip macOS AppleDouble sidecars (`._foo.py`) that exFAT drives spawn —
        # they're binary resource forks, not source (see env note T7/exFAT).
        if path.name.startswith("._"):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        scanner = _Scanner()
        scanner.visit(ast.parse(source, filename=str(path)))
        if scanner.sites:
            out[path.name] = scanner.sites
    return out


def _keys(sites: dict[str, list[tuple[str, int]]]) -> set[str]:
    return {f"{fname}::{qual}" for fname, lst in sites.items() for qual, _ in lst}


# ---------------------------------------------------------------------------
# ALLOWLIST — the not-yet-converted async `.execute()` sites, "<file>::<qual>".
#
# Seeded from the pre-P2-a scan. SHRINK this as handlers move onto the seam;
# do NOT add to it without a deliberate reason (a genuinely loop-safe async
# `.execute()` is essentially never correct — convert it instead).
# ---------------------------------------------------------------------------
ALLOWLIST: set[str] = {
    # ── server.py (not yet converted) ──────────────────────────────────────
    # PR #1 (feat/p2a-async-reads-1) converted the read backbone:
    #   list_jobs, list_my_jobs, get_job, list_companies, get_job_detail,
    #   list_applications, get_application_by_job — so they are NOT listed here.
}


def test_no_new_blocking_execute_in_async_handlers() -> None:
    current = _keys(find_blocking_sites())
    new = current - ALLOWLIST
    assert not new, (
        "New blocking supabase `.execute()` found inside async function(s) — "
        "these block the FastAPI event loop. Convert to `await aexecute(...)` "
        "(db/client.py async seam), or if truly loop-safe add the key to "
        "ALLOWLIST with justification:\n  " + "\n  ".join(sorted(new))
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Keep the allowlist honest: every entry must still exist in the tree."""
    current = _keys(find_blocking_sites())
    stale = ALLOWLIST - current
    assert not stale, (
        "ALLOWLIST entries no longer present (converted or moved) — delete "
        "them so the guard stays tight:\n  " + "\n  ".join(sorted(stale))
    )


if __name__ == "__main__":
    sites = find_blocking_sites()
    total = sum(len(v) for v in sites.values())
    print(f"Blocking `.execute()` in async functions: {total} across {len(sites)} files\n")
    for fname in sorted(sites):
        lst = sites[fname]
        print(f"{fname}  ({len(lst)})")
        by_func: dict[str, list[int]] = defaultdict(list)
        for qual, lineno in lst:
            by_func[qual].append(lineno)
        for qual in sorted(by_func):
            lines = ", ".join(str(n) for n in sorted(by_func[qual]))
            print(f"    {qual:55s} L{lines}")
        print()
    print("# ALLOWLIST seed (paste into ALLOWLIST):")
    for key in sorted(_keys(sites)):
        print(f'    "{key}",')
