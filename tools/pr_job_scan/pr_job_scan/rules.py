"""Rule implementations.

Each rule is a function `rule_<ID>(diff_files) -> list[Finding]`. They consume
the parsed diff so they only look at *added* lines (the diff is the unit of
review). Whole-file rules use the `whole_file_*` helpers, which load the file
from disk when run via `scan-repo`.

Rules deliberately use plain regex / substring checks — fast, easy to read,
and good enough for the patterns we care about. AST parsing would over-engineer
this; CodeQL exists if you need that.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

from .diff import AddedLine, DiffFile, all_added_lines
from .finding import Finding, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_python(path: str) -> bool:
    return path.endswith(".py")


def _is_ts(path: str) -> bool:
    return path.endswith((".ts", ".tsx"))


def _is_test(path: str) -> bool:
    return (
        "/tests/" in path
        or path.startswith("tests/")
        or "test_" in Path(path).name
        or path.endswith(".test.ts")
        or path.endswith(".test.tsx")
    )


def _strip_comment(line: str) -> str:
    """Remove trailing `# …` / `// …` for matchers that should ignore comments."""
    # naive but sufficient — we don't care about strings containing `#`
    for marker in ("#", "//"):
        idx = line.find(marker)
        if idx != -1:
            return line[:idx]
    return line


# ---------------------------------------------------------------------------
# SEC001 — logger.exception in HTTP / worker entry points
# ---------------------------------------------------------------------------
#
# `logger.exception` writes the full traceback to logs. In production these
# logs are often forwarded to a SaaS aggregator and a 500 response is returned
# to the client; the traceback can leak file paths, secrets in repr(), and
# library versions. The B17 batch swept 11 of these from api/server.py and
# api/worker.py — we now block new ones at PR time.
#
# Allow-listed:
#   - background helpers under `agents/` and `db/`
#   - lines whose preceding comment says `# SEC001-ok:` (explicit override)

_LOGGER_EXC = re.compile(r"\blogger\.exception\s*\(")


def rule_SEC001(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        if not _is_python(line.file):
            continue
        # Only block in HTTP and worker entry points
        if not (line.file.startswith("api/") or "/api/" in line.file):
            continue
        text = _strip_comment(line.text)
        if _LOGGER_EXC.search(text):
            if "SEC001-ok" in line.text:
                continue
            findings.append(Finding(
                rule_id="SEC001",
                title="logger.exception leaks traceback in HTTP/worker path",
                severity=Severity.HIGH,
                file=line.file,
                line=line.line,
                snippet=line.text.strip(),
                why=(
                    "Tracebacks include file paths and reprs of locals — they "
                    "leak into log aggregators and crash dumps. Use logger.error "
                    "with %s-formatted fields instead."
                ),
                fix=(
                    'logger.error("foo failed: ctx=%s error=%s", ctx, '
                    'type(e).__name__)'
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# SEC002 — CORS wildcard with credentials
# ---------------------------------------------------------------------------
#
# `allow_origins=["*"]` + `allow_credentials=True` is rejected by browsers and
# is a known credential-exfiltration vector. The B3 fix moved origins into
# settings.cors_allowed_origins. We catch single-line and multi-line forms.

def rule_SEC002(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if not _is_python(f.path):
            continue
        # We check the *added* lines for the wildcard, but allow context
        # lines (unchanged neighbors) to satisfy the credentials half —
        # the bug is real even when only the origins line is the new one.
        added_text = "\n".join(a.text for a in f.added)
        full_text = f.hunk_text()
        if not added_text:
            continue
        if 'allow_origins=["*"]' in added_text or "allow_origins=['*']" in added_text:
            if "allow_credentials=True" in full_text or "allow_credentials = True" in full_text:
                for a in f.added:
                    if 'allow_origins=["*"]' in a.text or "allow_origins=['*']" in a.text:
                        findings.append(Finding(
                            rule_id="SEC002",
                            title="CORS wildcard combined with allow_credentials=True",
                            severity=Severity.HIGH,
                            file=f.path,
                            line=a.line,
                            snippet=a.text.strip(),
                            why=(
                                "Browsers reject credentialed requests when "
                                "ACAO is '*'. Even ignoring that, the pair is a "
                                "credential-exfil vector and the request will "
                                "fail in production."
                            ),
                            fix=(
                                "Read origins from config (cors_allowed_origins "
                                "in settings) — comma-separated list."
                            ),
                        ))
                        break
    return findings


# ---------------------------------------------------------------------------
# SEC003 — hard-coded placeholder secrets
# ---------------------------------------------------------------------------

_SECRET_PLACEHOLDERS = (
    "change-me-in-production",
    "changeme",
    "your-secret-key",
    "todo-set-key",
    "REPLACE_ME",
)


def rule_SEC003(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        # Scripts that intentionally check for the placeholder (e.g.
        # scripts/check-prod-config.py) are not vulnerabilities.
        if "check-prod-config" in line.file or "check_prod_config" in line.file:
            continue
        for marker in _SECRET_PLACEHOLDERS:
            if marker in line.text and "test" not in line.file:
                findings.append(Finding(
                    rule_id="SEC003",
                    title=f"Placeholder secret {marker!r} added to source",
                    severity=Severity.MED,
                    file=line.file,
                    line=line.line,
                    snippet=line.text.strip(),
                    why=(
                        "Placeholder values get deployed to production when "
                        "env vars aren't set. Default to None and raise at "
                        "startup if the value is missing."
                    ),
                    fix="Use `os.environ['NAME']` or settings field without default.",
                ))
                break
    return findings


# ---------------------------------------------------------------------------
# SEC004 — TLS verification disabled
# ---------------------------------------------------------------------------

_VERIFY_FALSE = re.compile(r"\bverify\s*=\s*False\b")


def rule_SEC004(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        if not _is_python(line.file):
            continue
        if _is_test(line.file):
            continue
        if _VERIFY_FALSE.search(line.text):
            findings.append(Finding(
                rule_id="SEC004",
                title="TLS verification disabled (verify=False)",
                severity=Severity.HIGH,
                file=line.file,
                line=line.line,
                snippet=line.text.strip(),
                why="Disabling TLS verification opens MITM attacks on outbound calls.",
                fix="Remove verify=False or pin a CA bundle via verify=PATH.",
            ))
    return findings


# ---------------------------------------------------------------------------
# REL001 — deprecated @app.on_event hooks
# ---------------------------------------------------------------------------

_ON_EVENT = re.compile(r"@app\.on_event\(\s*['\"](startup|shutdown)['\"]")


def rule_REL001(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        if not _is_python(line.file):
            continue
        if line.text.lstrip().startswith("#"):
            continue  # mentioned in a comment — not a real use
        m = _ON_EVENT.search(line.text)
        if m:
            findings.append(Finding(
                rule_id="REL001",
                title=f"@app.on_event({m.group(1)!r}) is deprecated in FastAPI",
                severity=Severity.MED,
                file=line.file,
                line=line.line,
                snippet=line.text.strip(),
                why=(
                    "FastAPI deprecated on_event in favor of the lifespan "
                    "context manager (Starlette ASGI lifespan). Mixing the "
                    "two leads to double-init or skipped shutdowns."
                ),
                fix=(
                    "Use @asynccontextmanager + FastAPI(lifespan=_app_lifespan) "
                    "(see B26 in jobHunt bug log)."
                ),
            ))
    return findings


# ---------------------------------------------------------------------------
# REL002 — writing the wrong column on resume_builds
# ---------------------------------------------------------------------------
#
# The resume_builds table has `error` (no `_message`). PostgREST silently
# drops unknown columns, which is exactly why B6/HARDEN-BUG-402 marked 15
# rows as 'failed' with no reason. Catch this at PR time.

_RESUME_BUILDS_UPDATE = re.compile(
    r'table\(\s*["\']resume_builds["\']\s*\)\.update'
)


def rule_REL002(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if not _is_python(f.path):
            continue
        block_text = "\n".join(a.text for a in f.added)
        if not block_text:
            continue
        if not _RESUME_BUILDS_UPDATE.search(block_text):
            continue
        # In the updated block, look for `error_message` *as a JSON key* —
        # not just a mention in a comment.
        for a in f.added:
            code = _strip_comment(a.text)
            if (
                '"error_message"' in code
                or "'error_message'" in code
                or "error_message=" in code
            ):
                findings.append(Finding(
                    rule_id="REL002",
                    title="resume_builds.update writes non-existent column 'error_message'",
                    severity=Severity.HIGH,
                    file=f.path,
                    line=a.line,
                    snippet=a.text.strip(),
                    why=(
                        "PostgREST silently drops unknown JSON keys. The "
                        "column is `error` (no `_message`) — see B6. Writing "
                        "to the wrong key leaves error=NULL on failed rows."
                    ),
                    fix='Use {"status": "failed", "error": "<reason>"}',
                ))
    return findings


# ---------------------------------------------------------------------------
# REL003 — raw LLM router instead of hardened router
# ---------------------------------------------------------------------------
#
# After B-OR3 every G4 path uses get_hardened_router().ask_with_fallback().
# G5/G6/G8/G9/G10/G11 still use get_router().ask() — see B27 tracker.
# Flag any *new* raw-router call site in an `agents/g{n}_*.py` file.

_RAW_ROUTER_ASK = re.compile(r"get_router\(\)\.\s*ask(?:_json)?\(")


def rule_REL003(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        if not _is_python(line.file):
            continue
        if not ("/agents/" in line.file or line.file.startswith("agents/")):
            continue
        # The hardening module itself wraps get_router → allow
        if Path(line.file).name == "llm_hardening.py":
            continue
        if _RAW_ROUTER_ASK.search(line.text):
            findings.append(Finding(
                rule_id="REL003",
                title="Raw get_router().ask(...) — no provider fallback",
                severity=Severity.MED,
                file=line.file,
                line=line.line,
                snippet=line.text.strip(),
                why=(
                    "Single-provider calls take the whole graph down when the "
                    "primary is rate-limited or 500ing. The hardened router "
                    "wraps the same call with retry, circuit breaker, and "
                    "OpenRouter fallback (B-OR3)."
                ),
                fix="get_hardened_router().ask_with_fallback(primary_provider=..., fallback_chain=[...])",
            ))
    return findings


# ---------------------------------------------------------------------------
# REL004 — route prefix collides with `/workspace/{job_id}` int route
# ---------------------------------------------------------------------------
#
# /workspace/stories and /workspace/follow-ups both matched against the
# int `/workspace/{job_id}` route because workspace_router was included
# first in server.py. FastAPI parsed "stories" as job_id → 422. Catch any
# new APIRouter(prefix="/workspace/<word>") and require it be top-level.

_BAD_PREFIX = re.compile(r'prefix\s*=\s*["\']/workspace/([a-z][a-z0-9_-]+)["\']')


def rule_REL004(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        if not _is_python(line.file):
            continue
        m = _BAD_PREFIX.search(line.text)
        if m:
            sub = m.group(1)
            findings.append(Finding(
                rule_id="REL004",
                title=f"Prefix '/workspace/{sub}' collides with /workspace/{{job_id}}",
                severity=Severity.HIGH,
                file=line.file,
                line=line.line,
                snippet=line.text.strip(),
                why=(
                    "workspace_router defines /workspace/{job_id: int}. Any "
                    "sibling /workspace/<word> route is matched as job_id="
                    f"'{sub}' first and 422s on int_parsing. See B2 fix."
                ),
                fix=f'Move to top-level prefix: prefix="/{sub}"',
            ))
    return findings


# ---------------------------------------------------------------------------
# REL005 — long-running BackgroundTask without SIGTERM-aware sweep
# ---------------------------------------------------------------------------
#
# Heuristic: a new file adds `background_tasks.add_task(_run)` where `_run`
# itself runs an `async for ... in ... :` loop. Flag with a note pointing at
# the B7 incident.

def rule_REL005(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if not _is_python(f.path):
            continue
        text = "\n".join(a.text for a in f.added)
        if (
            "background_tasks.add_task" in text
            and ("for " in text and " in " in text)
            and "SIGTERM" not in text
            and "_sweep_in_flight" not in text
        ):
            for a in f.added:
                code = _strip_comment(a.text)
                if "background_tasks.add_task" not in code:
                    continue
                # Skip docstrings — heuristic: any '"""' nearby in the file's added text
                # (cheap; good enough to silence the obvious cases)
                ctx_window = "\n".join(
                    other.text for other in f.added
                    if abs(other.line - a.line) <= 5
                )
                if '"""' in ctx_window and "→" in ctx_window:
                    continue
                findings.append(Finding(
                    rule_id="REL005",
                    title="BackgroundTask with loop and no SIGTERM sweep",
                    severity=Severity.MED,
                    file=f.path,
                    line=a.line,
                    snippet=a.text.strip(),
                    why=(
                        "Railway sends SIGTERM during deploy. Without a "
                        "sweep, in-flight builds stay `running` forever "
                        "(see B7 mass-fail incident, 2026-05-11)."
                    ),
                    fix=(
                        "Register an on_signal handler that flips running "
                        "rows to failed (see api/worker._sweep_in_flight_on_shutdown)."
                    ),
                ))
                break
    return findings


# ---------------------------------------------------------------------------
# QUA001 — mock data fallback in dashboard catch block
# ---------------------------------------------------------------------------

_CATCH_MOCK = re.compile(r"catch\s*\([^)]*\)\s*\{[^}]*MOCK_", re.DOTALL)
_DOT_CATCH_MOCK = re.compile(r"\.catch\s*\(\s*\(\)\s*=>\s*MOCK_")


def rule_QUA001(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if not _is_ts(f.path):
            continue
        if not (f.path.startswith("dashboard/") or "/dashboard/" in f.path):
            continue
        # The mock files themselves are allowed to use MOCK_*. We only flag
        # production pages/components that reach into them.
        if "/lib/mock/" in f.path or "/mock/" in f.path:
            continue
        hunk = f.hunk_text()
        added_text = "\n".join(a.text for a in f.added)
        if "MOCK_" not in added_text:
            continue
        if _CATCH_MOCK.search(hunk) or _DOT_CATCH_MOCK.search(hunk) or "catch" in hunk:
            for a in f.added:
                # Skip lines that only mention MOCK_ in a comment
                code = _strip_comment(a.text)
                if "MOCK_" not in code:
                    continue
                if "MOCK_" in a.text:
                    findings.append(Finding(
                        rule_id="QUA001",
                        title="Dashboard page falls back to MOCK_* data on fetch error",
                        severity=Severity.HIGH,
                        file=f.path,
                        line=a.line,
                        snippet=a.text.strip(),
                        why=(
                            "Users see stale fake data and can't tell. B15/B10 "
                            "and GAP-004 all originated here. Show an empty "
                            "state + 'Couldn't reach the backend' banner."
                        ),
                        fix=(
                            "Return { drafts: [], usedMock: false } and "
                            "render an error banner when err is set."
                        ),
                    ))
                    break
    return findings


# ---------------------------------------------------------------------------
# QUA002 — TODO/FIXME added with no owner or date
# ---------------------------------------------------------------------------

_LOOSE_TODO = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b(?!\([^)]+\)|:\s*\d{4}-\d{2}-\d{2})")


def rule_QUA002(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        text = _strip_comment(line.text)
        # Only flag the TODO if the marker is inside a comment, not in code
        if text == line.text:
            continue  # no comment marker on the line
        if _LOOSE_TODO.search(line.text):
            findings.append(Finding(
                rule_id="QUA002",
                title="TODO/FIXME without owner or date",
                severity=Severity.LOW,
                file=line.file,
                line=line.line,
                snippet=line.text.strip(),
                why=(
                    "B25 swept 4 stale TODOs after the audit. Untraceable "
                    "TODOs accumulate. Require `TODO(owner): YYYY-MM-DD message`."
                ),
                fix="TODO(rizwan): 2026-06-01 — explain what & why",
            ))
    return findings


# ---------------------------------------------------------------------------
# QUA003 — Finder/Cloud-duplicate filenames
# ---------------------------------------------------------------------------

_FINDER_DUP = re.compile(r"\s[2-9](\.[A-Za-z0-9]+)?$")


def rule_QUA003(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for f in files:
        name = Path(f.path).name
        if _FINDER_DUP.search(name) and f.path not in seen:
            seen.add(f.path)
            findings.append(Finding(
                rule_id="QUA003",
                title="Finder-style duplicate filename committed",
                severity=Severity.LOW,
                file=f.path,
                line=0,
                snippet=name,
                why=(
                    "macOS Finder and iCloud create `* 2.ext` copies. They "
                    "ship into bundles and confuse imports (see B28 fix)."
                ),
                fix=".gitignore patterns: '* 2.*', '* 3.*', '* 4.*'",
            ))
    return findings


# ---------------------------------------------------------------------------
# QUA004 — new public endpoint with no test
# ---------------------------------------------------------------------------
#
# Heuristic: a non-test diff adds `@app.<verb>(` or `@router.<verb>(` and no
# test file in the same diff references the new path.

_ROUTE_DEF = re.compile(
    r'@(?:app|router)\.(?:get|post|put|patch|delete)\(\s*["\'](?P<path>[^"\']+)'
)


def rule_QUA004(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    new_paths: list[tuple[str, int, str]] = []  # (route_path, file_line, file_path)
    test_text_blobs: list[str] = []

    for f in files:
        if _is_test(f.path):
            test_text_blobs.append("\n".join(a.text for a in f.added))
            continue
        if not _is_python(f.path):
            continue
        for a in f.added:
            m = _ROUTE_DEF.search(a.text)
            if m:
                new_paths.append((m.group("path"), a.line, f.path))

    test_blob = "\n".join(test_text_blobs)

    def _path_matches(route: str, blob: str) -> bool:
        """Match the route in test blob, ignoring path-param values.

        `/applications/{id}/retire` should match the test call
        `/applications/1/retire`. We turn `{id}` (or `{anything}`) into
        a permissive `[^/"' ]+` regex.
        """
        import re as _re
        pattern = _re.sub(r"\{[^/}]+\}", r"[^/\"' ]+", route)
        return _re.search(pattern, blob) is not None

    for route_path, line_no, fpath in new_paths:
        if _path_matches(route_path, test_blob):
            continue
        findings.append(Finding(
            rule_id="QUA004",
            title=f"New endpoint {route_path} has no test in this PR",
            severity=Severity.MED,
            file=fpath,
            line=line_no,
            snippet=route_path,
            why=(
                "Test coverage gap audit (GAP-005) found 6% endpoint coverage. "
                "Adding endpoints without tests compounds the gap."
            ),
            fix=(
                "Add a tests/test_<area>.py covering happy path + auth + 4xx. "
                "Use FastAPI TestClient with mocked Supabase."
            ),
        ))
    return findings


# ---------------------------------------------------------------------------
# QUA005 — stale Claude model id (claude-opus-4-7, etc.)
# ---------------------------------------------------------------------------

_STALE_MODELS = (
    "claude-opus-4-7",  # was hallucinated; correct id is claude-opus-4-5-20251101
    "claude-3-5-sonnet-20240620",  # superseded by 4.x
    "gpt-4-turbo-preview",
)


def rule_QUA005(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for line in all_added_lines(files):
        code = _strip_comment(line.text)
        # Skip mock fixtures — they're test data, not live model selections
        if "/lib/mock/" in line.file or "/mock/" in line.file:
            continue
        for mdl in _STALE_MODELS:
            if mdl in code:
                findings.append(Finding(
                    rule_id="QUA005",
                    title=f"Stale or invalid model id {mdl!r}",
                    severity=Severity.MED,
                    file=line.file,
                    line=line.line,
                    snippet=line.text.strip(),
                    why=(
                        "PR #121 fixed this exact id (claude-opus-4-7 → "
                        "claude-opus-4-5-20251101). Drift here breaks the "
                        "graph at runtime."
                    ),
                    fix="Use the model id from agents/llm_router.PRICING_PER_1M.",
                ))
                break
    return findings


# ---------------------------------------------------------------------------
# QUA006 — Pydantic body model without extra="forbid"
# ---------------------------------------------------------------------------
#
# B9 introduced `extra="forbid"` on every endpoint body for the /network
# routes. Without it, typos in client payloads silently no-op (the
# offending field is dropped). Heuristic: a new BaseModel that's used as
# an endpoint body and has no `model_config` / `Config` declaration.

_BASEMODEL = re.compile(r"^class\s+(\w+)\s*\(\s*BaseModel\s*\)\s*:")


def rule_QUA006(files: list[DiffFile]) -> list[Finding]:
    findings: list[Finding] = []
    for f in files:
        if not _is_python(f.path):
            continue
        text = "\n".join(a.text for a in f.added)
        if not text:
            continue
        for a in f.added:
            m = _BASEMODEL.match(a.text)
            if not m:
                continue
            name = m.group(1)
            # Accept any of: extra="forbid", extra='forbid',
            # "extra": "forbid", 'extra': 'forbid'
            if ('"forbid"' in text or "'forbid'" in text) and "extra" in text:
                continue
            findings.append(Finding(
                rule_id="QUA006",
                title=f"Pydantic model {name} added without extra='forbid'",
                severity=Severity.LOW,
                file=f.path,
                line=a.line,
                snippet=a.text.strip(),
                why=(
                    "FastAPI silently drops unknown JSON keys without "
                    "extra='forbid'. Bad client typos pass tests and "
                    "break in prod (see /network B9 batch)."
                ),
                fix='model_config = {"extra": "forbid"}',
            ))
    return findings


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

Rule = Callable[[list[DiffFile]], list[Finding]]

ALL_RULES: list[tuple[str, Rule]] = [
    ("SEC001", rule_SEC001),
    ("SEC002", rule_SEC002),
    ("SEC003", rule_SEC003),
    ("SEC004", rule_SEC004),
    ("REL001", rule_REL001),
    ("REL002", rule_REL002),
    ("REL003", rule_REL003),
    ("REL004", rule_REL004),
    ("REL005", rule_REL005),
    ("QUA001", rule_QUA001),
    ("QUA002", rule_QUA002),
    ("QUA003", rule_QUA003),
    ("QUA004", rule_QUA004),
    ("QUA005", rule_QUA005),
    ("QUA006", rule_QUA006),
]


def run_all(files: list[DiffFile], select: Iterable[str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    selected = set(select) if select else None
    for rid, fn in ALL_RULES:
        if selected is not None and rid not in selected:
            continue
        out.extend(fn(files))
    # Stable sort: severity desc, then file, then line
    out.sort(key=lambda f: (-int(f.severity), f.file, f.line, f.rule_id))
    return out
