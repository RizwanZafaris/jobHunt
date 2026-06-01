"""
Phase 1, finding 5 — STATIC unscoped-tenant-query guardrail.

This is a *regression fence*, not a runtime test. It AST-scans api/server.py and
every router under api/ and, for every Supabase ``db.table("<TENANT_TABLE>")``
call, fails if the enclosing function neither (a) scopes the query to the
caller's tenant nor (b) is on an explicit ALLOWLIST of deliberate unscoped
queries (admin / cron / worker / global-observability / transitively-scoped).

Why a static guard
------------------
finding 2 (feat/endpoint-user-scoping) mechanically added ``.eq("user_id", ...)``
to ~70 endpoints. The load-bearing tenant guarantee is that explicit predicate —
we never rely on RLS alone, because api/server.py talks to Supabase with the
service-role key, which BYPASSES RLS. A future PR that adds a new tenant-table
read without the predicate would silently re-open cross-tenant reads. Code review
is the only thing standing between that and prod. This test makes the machine
enforce it: add an unscoped tenant query that isn't a justified exception and the
suite goes red.

What counts as "scoped"
-----------------------
A function that touches a tenant table passes if its body contains ANY of:
  * ``.eq("user_id", ...)``                 — the canonical read filter
  * a dict literal with a ``"user_id"`` key — a write that stamps the tenant
  * a call to ``load_open_job(`` / ``load_open_job_or_none(`` — the shared
    single-job loader in api/_job_guards.py, which 404s a row that isn't the
    caller's (it applies the ``.eq("user_id", ...)`` internally).

Everything else MUST be on ALLOWLIST with a one-line reason, OR the test fails
and prints the offending (file, function, table, line). See ALLOWLIST below for
the catalogue of deliberate exceptions finding 2 left in place.

TENANT_TABLES is cross-checked against migration
db/migrations/2026_05_10_001_multi_tenancy.sql's ``target_tables`` loop plus the
later migrations that introduced new user_id-bearing tables (004 referral graph,
005 linkedin, 008 outcome credits, 013 comp_cache, 015 story_bank, 016
follow_up_cadence, 022 application_answers, 023 writing_samples, 024 proof_points,
026 offer_evaluations, 003 jobs_runs, 036 job_card_dismissals, 038
buffer_oauth_tokens). NON-tenant tables (boss_audit_log, the v_* views) are
NOT in the set, so unscoped reads of them never trip the guard.
"""
from __future__ import annotations

import ast
import os

import pytest


# ── repo paths ───────────────────────────────────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_THIS_DIR)
_API_DIR = os.path.join(_REPO_ROOT, "api")


# ── TENANT_TABLES — the user_id-bearing tables ───────────────────────────────
# Sourced from migration 001's tenant loop + every later migration that adds a
# table carrying `user_id NOT NULL REFERENCES users(id)`. If you add a new
# tenant table, add it here so the guard starts protecting its queries.
TENANT_TABLES = frozenset({
    # ── migration 001 target_tables loop ──
    "jobs",
    "applications",
    "company_personas",       # canonical name for "personas"
    "personas",               # defensive: 001 also loops a bare `personas`
    "company_knowledge",
    "companies",
    "target_companies",
    "costs",
    "agent_call_log",
    "agent_conversations",
    "rizwan_profile",
    "story_bank",
    "resume_builds",
    "resume_outcomes",
    "ats_test_results",
    "interview_outcomes",
    "interview_prep",
    "profile_master",
    "profile_experience",
    "profile_certification",
    "profile_education",
    "profile_keyword",
    "profile_keyword_category",
    "profile_source_document",
    "profile_recommendation",
    "outreach",
    # ── 004 referral graph ──
    "people",
    "edges",
    # NOTE: `employments` has NO direct user_id column — it routes tenancy
    # through people.user_id (see migration 004). It is intentionally NOT a
    # TENANT_TABLE here: a `.table("employments")` query can't carry
    # `.eq("user_id")` and is guarded at the people layer / by RLS join.
    # ── 003 queue ──
    "jobs_runs",
    # ── 005 linkedin engine ──
    "linkedin_drafts",
    "linkedin_posting_schedule",
    "linkedin_voice_profile",
    # ── 008 outcome credits ──
    "knowledge_outcome_credits",
    "persona_versions",
    "interview_tutor_messages",
    # ── 013 comp cache ──
    "comp_cache",
    # ── 016 / 022 / 023 / 024 / 026 ──
    "follow_up_cadence",
    "application_answers",
    "writing_samples",
    "proof_points",
    "offer_evaluations",
    # ── 036 / 038 (FK to auth.users, still per-user) ──
    "job_card_dismissals",
    "buffer_oauth_tokens",
})


# ── ALLOWLIST — deliberate unscoped tenant-table queries ─────────────────────
# Keyed by (basename, function_name). Each entry is a tenant-table query that is
# *correctly* not filtered by the request caller's user_id, with the reason.
# Categories:
#   [cron/system batch]  — service-secret gated; the agent resolves user_id
#                          internally / operates across all targets.
#   [admin]              — require_admin gated; ops visibility across tenants.
#   [global观测]          — un-tenanted observability surface (agent_call_log
#                          rollups, resume_builds cost joins). A separate
#                          per-user analytics surface exists where needed.
#   [worker/queue]       — RQ worker / queue-runner internals keyed by run_id
#                          (UUID). Not request-scoped.
#   [transitive]         — operates on a row already proven to be the caller's
#                          (id taken from a previously user-scoped fetch).
#   [helper]             — module-level helper whose only callers are gated
#                          endpoints in one of the above categories.
#
# IMPORTANT: this list must contain ONLY genuine exceptions. If a NEW (file,
# func) shows up failing the guard and it is a normal user-facing read/write,
# the fix is to add `.eq("user_id")` — NOT to add it here.
ALLOWLIST = frozenset({
    # ── api/server.py — system / readiness probe (no request user) ──
    # /ready (P3-3 observability) is an infra liveness/readiness endpoint
    # (@limiter.exempt, hit by the load balancer). Its nested `_db_probe`
    # closure reads ONE arbitrary row (`select("id").limit(1)`) purely to
    # prove DB reachability — it returns a bool, never exposes the row, and
    # runs with no authenticated request user. There is deliberately no
    # `.eq("user_id")`: the probe must work before/without any auth context.
    ("server.py", "ready.<locals>._db_probe"),     # [system/probe] readiness DB reachability check; not user data
    ("server.py", "admin_selftest.<locals>._db"),  # [system/probe] admin /admin/selftest DB reachability check; not user data (require_admin)
    # ── api/server.py — cron / system batch (verify_service_secret) ──
    # NOTE: trigger_company_research / run_pipeline_targets /
    # reclassify_existing_jobs do their tenant queries inside a background `_run`
    # closure, so they're allowlisted by their parent-qualified `_run` qualname
    # below — NOT by the bare endpoint name.
    ("server.py", "_build_boss_context"),          # [helper] boss digest, /boss/chat only (service-secret); global pipeline snapshot
    ("server.py", "trigger_persona_synthesis"),    # [cron/system batch] PersonaSynthesizer across companies
    ("server.py", "trigger_refresh_news"),         # [cron/system batch] daily news refresh across targets
    ("server.py", "trigger_deep_research_batch"),  # [cron/system batch] deep-research roster across targets
    ("server.py", "admin_rebuild_personas"),       # [admin] system-wide persona rebuild across all target companies (require_admin)
    # ── api/server.py — global cost observability (verify_service_secret) ──
    ("server.py", "_cost_window_query"),           # [helper] agent_call_log window builder for /costs/*
    ("server.py", "costs_summary"),                # [global obs] agent_call_log rollup
    ("server.py", "costs_by_resume_build"),        # [global obs] resume_builds cost join
    ("server.py", "costs_recent_calls"),           # [global obs] agent_call_log tail
    # ── api/server.py — admin ops (require_admin) ──
    ("server.py", "admin_worker_status"),          # [admin] jobs_runs queue diagnostics
    ("server.py", "admin_requeue_stuck"),          # [admin] re-enqueue stale jobs_runs rows
    # ── api/server.py — background-task closures spawned by the cron endpoints
    #    above. They run the same system batch work inside `background_tasks`.
    #    Keyed by parent-qualified name (NOT the bare `_run`) so a future
    #    unscoped query in a `_run` under a *user-facing* endpoint is NOT masked.
    ("server.py", "trigger_company_research.<locals>._run"),   # [cron/system batch] CompanyAgent across targets
    ("server.py", "run_pipeline_targets.<locals>._run"),       # [cron/system batch] scout-only across all targets
    ("server.py", "reclassify_existing_jobs.<locals>._run"),   # [cron/system batch] re-classify all jobs missing archetype
    # ── api/interview_studio.py ──
    ("interview_studio.py", "_load_job_for_application"),  # [transitive] job_id comes from an already user-scoped application row
    # ── api/jobs_runs.py — queue run-state CRUD keyed by run_id (UUID) ──
    ("jobs_runs.py", "get_run"),                   # [worker/queue] fetch run by id
    ("jobs_runs.py", "find_by_idempotency_key"),   # [worker/queue] dedup lookup by idem key
    ("jobs_runs.py", "mark_running"),              # [worker/queue] state transition by id
    ("jobs_runs.py", "mark_succeeded"),            # [worker/queue] state transition by id
    ("jobs_runs.py", "mark_failed"),               # [worker/queue] state transition by id
    ("jobs_runs.py", "mark_cancelled"),            # [worker/queue] state transition by id
    ("jobs_runs.py", "find_orphans"),              # [worker/queue] reaper scan across tenants
    # ── api/orphan_reaper.py — cron orphan sweeper (cross-tenant by design) ──
    ("orphan_reaper.py", "_sweep_stuck_resume_builds"),  # [cron] reap stuck resume_builds across tenants
    ("orphan_reaper.py", "_sweep_stuck_queued"),         # [cron] reap stuck jobs_runs across tenants
    # ── api/worker.py — RQ worker internals (run_id keyed / shutdown sweep) ──
    ("worker.py", "worker_run_g1"),                # [worker/queue] G1 runner; company build by run payload
    ("worker.py", "_sweep_in_flight_on_shutdown"), # [worker/queue] mark in-flight runs on shutdown
})


# ── scan ─────────────────────────────────────────────────────────────────────


def _python_files():
    """Every real .py under api/ — skips macOS exFAT resource-fork files
    (``._foo.py`` — the T7 Shield drive is exFAT and littered with them)."""
    out = []
    for name in sorted(os.listdir(_API_DIR)):
        if not name.endswith(".py"):
            continue
        if name.startswith("._"):  # AppleDouble sidecar — not real source
            continue
        out.append(os.path.join(_API_DIR, name))
    return out


def _tenant_table_of(call: ast.Call) -> str | None:
    """Return the table name if `call` is `<...>.table("<TENANT_TABLE>")`."""
    if isinstance(call.func, ast.Attribute) and call.func.attr == "table":
        if call.args and isinstance(call.args[0], ast.Constant):
            name = call.args[0].value
            if isinstance(name, str) and name in TENANT_TABLES:
                return name
    return None


def _iter_functions(tree: ast.AST):
    """Yield (qualname, func_node) for every function/closure, where qualname
    is parent-qualified (``parent.<locals>.child``) — so a tenant query in a
    nested closure is attributed to that closure, not its parent, and can be
    allowlisted (or caught) precisely."""

    def _walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = (
                    f"{prefix}.<locals>.{child.name}" if prefix else child.name
                )
                yield qual, child
                yield from _walk(child, qual)
            else:
                # descend through non-function nodes so nested funcs are found
                yield from _walk(child, prefix)

    yield from _walk(tree, "")


def _direct_nodes(func: ast.AST):
    """Walk `func`'s body but DO NOT descend into nested function definitions —
    so scope evidence / tenant calls are attributed to the function that
    literally contains them."""
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue  # belongs to the nested function, not this one
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _has_user_id_eq(func: ast.AST) -> bool:
    """True if the function body (excluding nested funcs) contains
    `.eq("user_id", ...)`."""
    for sub in _direct_nodes(func):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "eq"
            and sub.args
            and isinstance(sub.args[0], ast.Constant)
            and sub.args[0].value == "user_id"
        ):
            return True
    return False


def _has_user_id_write(func: ast.AST) -> bool:
    """True if the function body (excluding nested funcs) has a dict literal with
    a "user_id" key — i.e. a write payload that stamps the tenant. Covers both
    ``{"user_id": ...}`` and ``row["user_id"] = ...`` subscript assignment."""
    for sub in _direct_nodes(func):
        if isinstance(sub, ast.Dict):
            for k in sub.keys:
                if isinstance(k, ast.Constant) and k.value == "user_id":
                    return True
        # row["user_id"] = uid   (subscript-assignment style writers)
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if (
                    isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "user_id"
                ):
                    return True
    return False


_SCOPING_HELPERS = {"load_open_job", "load_open_job_or_none"}


def _calls_scoping_helper(func: ast.AST) -> bool:
    """True if the function (excluding nested funcs) calls one of the shared
    user-scoped single-row loaders (which apply `.eq("user_id")` internally and
    404 a row that isn't the caller's)."""
    for sub in _direct_nodes(func):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = (
                fn.id if isinstance(fn, ast.Name)
                else fn.attr if isinstance(fn, ast.Attribute)
                else None
            )
            if name in _SCOPING_HELPERS:
                return True
    return False


def _is_scoped(func: ast.AST) -> bool:
    return (
        _has_user_id_eq(func)
        or _has_user_id_write(func)
        or _calls_scoping_helper(func)
    )


def _collect_unscoped_tenant_funcs():
    """Walk api/*.py and return:
      offenders: list of (basename, func, table, lineno) lacking scope AND
                 not on the ALLOWLIST  → these FAIL the test.
      allowlisted_hits: set of (basename, func) ALLOWLIST entries actually
                 matched (used to detect stale allowlist entries).
    """
    offenders = []
    allowlisted_hits = set()
    for path in _python_files():
        basename = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src, filename=path)
        for qualname, func in _iter_functions(tree):
            # DIRECT tenant-table calls only — a query inside a nested closure is
            # attributed to that closure (its own qualname), not this function.
            tenant_hits = [
                (_tenant_table_of(sub), sub.lineno)
                for sub in _direct_nodes(func)
                if isinstance(sub, ast.Call) and _tenant_table_of(sub) is not None
            ]
            if not tenant_hits:
                continue
            if _is_scoped(func):
                continue
            key = (basename, qualname)
            if key in ALLOWLIST:
                allowlisted_hits.add(key)
                continue
            # not scoped, not allowlisted → a real finding
            for table, lineno in sorted(set(tenant_hits)):
                offenders.append((basename, qualname, table, lineno))
    return offenders, allowlisted_hits


# ── tests ────────────────────────────────────────────────────────────────────


def test_no_unscoped_tenant_queries_outside_allowlist():
    """The core fence: every tenant-table query in api/ is either tenant-scoped
    or an explicitly-justified ALLOWLIST exception. A new unscoped query fails
    here with a precise pointer to the (file, function, table, line)."""
    offenders, _ = _collect_unscoped_tenant_funcs()
    if offenders:
        lines = "\n".join(
            f"  - {f}::{fn} queries tenant table {t!r} (line {ln}) "
            f"without .eq('user_id'), a user_id write, or a scoping helper, "
            f"and is not on ALLOWLIST"
            for (f, fn, t, ln) in offenders
        )
        pytest.fail(
            "Unscoped tenant-table query(ies) found — either add "
            ".eq('user_id', str(user.id)) (reads) / a user_id write, or, if "
            "this is a genuine admin/cron/worker/global exception, add it to "
            "ALLOWLIST in this file WITH a reason:\n" + lines
        )


def test_allowlist_has_no_stale_entries():
    """Keep the allowlist honest: every ALLOWLIST entry must still correspond to
    a real unscoped tenant-table function. If a function got scoped (or removed),
    its allowlist entry is dead weight and should be deleted — otherwise it would
    silently mask a future unscoped query of the same name."""
    _, hits = _collect_unscoped_tenant_funcs()
    stale = sorted(set(ALLOWLIST) - hits)
    assert not stale, (
        "ALLOWLIST entries no longer match an unscoped tenant query (remove "
        f"them — they're stale and could mask a real regression): {stale}"
    )


def test_tenant_tables_cover_core_audit_tables():
    """Sanity-pin the four P0.1 core tenant tables from the audit are in the
    set, so a careless edit to TENANT_TABLES can't quietly stop protecting
    jobs/applications/companies/company_knowledge."""
    for core in ("jobs", "applications", "companies", "company_knowledge"):
        assert core in TENANT_TABLES, f"{core} must be a guarded tenant table"


def test_guardrail_catches_a_synthetic_unscoped_query(tmp_path):
    """Meta-test: prove the scanner actually FAILS on an unscoped tenant query
    (not vacuously green). We build a throwaway module with a tenant-table read
    that has no user_id filter and assert the scan flags it; then confirm that
    adding `.eq('user_id', ...)` clears it. This guarantees the failing-then-
    passing property without touching real api/ source."""
    unscoped = (
        "def handler(db):\n"
        "    return db.table('jobs').select('*').execute().data\n"
    )
    scoped = (
        "def handler(db, user):\n"
        "    return (db.table('jobs').select('*')\n"
        "            .eq('user_id', str(user.id)).execute().data)\n"
    )

    def _scan_source(src: str):
        tree = ast.parse(src)
        flagged = []
        for qualname, func in _iter_functions(tree):
            hits = [
                _tenant_table_of(c)
                for c in _direct_nodes(func)
                if isinstance(c, ast.Call) and _tenant_table_of(c)
            ]
            if hits and not _is_scoped(func):
                flagged.append(func.name)
        return flagged

    assert _scan_source(unscoped) == ["handler"], (
        "scanner should flag an unscoped tenant query"
    )
    assert _scan_source(scoped) == [], (
        "scanner should pass once .eq('user_id') is added"
    )
