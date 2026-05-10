#!/usr/bin/env python3
"""
scripts/check-prod-config.py — Pre-deploy / CI config-drift detector.

Reads .env.example as the canonical list of vars and verifies that the
current process environment (or a supplied .env file) defines each one
with a non-placeholder, format-valid value.

Usage:
    python scripts/check-prod-config.py
    python scripts/check-prod-config.py --env-file .env.production
    python scripts/check-prod-config.py --ignore-optional

Exit codes:
    0 — all required vars OK (and recommended/optional vars satisfy
        their tier-specific rules; see --ignore-optional below).
    1 — at least one required var is missing, placeholder, or malformed.

Design notes:
    * Stdlib only — no `python-dotenv`, no `pydantic`. The project uses
      pydantic-settings at runtime, but the *check* must run in CI before
      any deps install (so `! python check-prod-config.py` is a clean
      assertion). Inline parser handles `KEY=value`, quoted values, and
      `# tier:` comment markers above each var.
    * Never echoes actual values. Output is var name + tier + status +
      reason only. Reason strings are static (e.g. "missing", "placeholder",
      "not https://") and never include the value.
    * Source of truth for vars is .env.example. To add a new var, edit
      that file. The .github/workflows/config-check.yml CI gate
      enforces this contract.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────
# Repo-relative paths. Script lives at scripts/check-prod-config.py, so
# repo root is its parent's parent.
# ─────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# ─────────────────────────────────────────────────────────────────────
# Tiers — ordered most-to-least critical. The CLI flag --ignore-optional
# downgrades anything below `required` to a warning.
# ─────────────────────────────────────────────────────────────────────
TIER_REQUIRED = "required"
TIER_RECOMMENDED = "recommended"
TIER_OPTIONAL = "optional"
VALID_TIERS = {TIER_REQUIRED, TIER_RECOMMENDED, TIER_OPTIONAL}

# Common placeholder patterns. Case-insensitive substring or literal match.
# IMPORTANT: any value that contains one of these substrings is flagged —
# we never compare against the real value externally.
PLACEHOLDER_TOKENS = (
    "CHANGE_ME",
    "YOUR_KEY_HERE",
    "REPLACE",
    "TODO",
    "your-key-here",
    "your-openai-key-here",
    "your-service-role-key-here",
    "your-anon-key-here",
    "your-serper-key-here",
    "your-sendgrid-key-here",
    "your-random-secret-key-change-this",
    "your-project.supabase.co",
    "your-domain.com",
    "change-me-in-production",
)
# These match only as the *full* value (after strip), not as substrings —
# avoids false-positives where "..." appears mid-URL.
PLACEHOLDER_EXACT = ("...", "<...>")

# ASCII status glyphs. The user spec asks for ✓/✗/⚠ but we emit ASCII
# fallbacks alongside for terminals that can't render them.
STATUS_OK = "OK"     # ✓
STATUS_FAIL = "FAIL"  # ✗
STATUS_WARN = "WARN"  # ⚠

# Status glyph map for the table (utf-8). Falls back to ASCII via env.
GLYPH = {
    STATUS_OK: "✓",
    STATUS_FAIL: "✗",
    STATUS_WARN: "⚠",
}


# ─────────────────────────────────────────────────────────────────────
# Var spec — derived from .env.example.
# ─────────────────────────────────────────────────────────────────────
@dataclass
class VarSpec:
    name: str
    section: str
    tier: str  # one of VALID_TIERS


@dataclass
class CheckResult:
    spec: VarSpec
    status: str   # STATUS_OK | STATUS_FAIL | STATUS_WARN
    reason: str   # never contains the value


# ─────────────────────────────────────────────────────────────────────
# .env file parser — minimal, stdlib-only.
# ─────────────────────────────────────────────────────────────────────
def parse_env_file(path: Path) -> Dict[str, str]:
    """Parse a .env-style file. Returns {KEY: VALUE} for non-comment lines.

    Supports:
        KEY=value
        KEY="quoted value"
        KEY='single quoted'
        KEY=value  # trailing comment
        # full-line comments (skipped)

    Returns the *raw value as a string* — empty string for `KEY=`.
    """
    out: Dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")
    for line_num, line in enumerate(path.read_text().splitlines(), start=1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, raw = s.partition("=")
        key = key.strip()
        val = raw.strip()
        # Strip trailing inline comment (only when value isn't a quoted
        # string that legitimately contains '#').
        if val and not (val.startswith('"') or val.startswith("'")):
            # Allow # in values like 'pass#word' only if no whitespace
            # before it; standard .env tools split on ' #' (space then #).
            split_idx = val.find(" #")
            if split_idx >= 0:
                val = val[:split_idx].strip()
        # Strip matched outer quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        out[key] = val
    return out


def parse_env_example(path: Path) -> List[VarSpec]:
    """Parse .env.example into ordered VarSpec list.

    Section headers are lines like `# === Database ===`.
    Tier markers are lines like `# tier: required` immediately above
    a var. Vars without a tier marker default to `optional`.
    """
    if not path.exists():
        raise FileNotFoundError(f".env.example not found: {path}")
    specs: List[VarSpec] = []
    current_section = "Uncategorized"
    pending_tier: Optional[str] = None
    section_re = re.compile(r"^#\s*=+\s*(.*?)\s*=+\s*$")
    tier_re = re.compile(r"^#\s*tier:\s*(\w+)\s*$", re.IGNORECASE)
    var_re = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=")

    for line in path.read_text().splitlines():
        s = line.strip()
        if not s:
            pending_tier = None  # blank line resets tier hint
            continue
        # Section header (must come before var-line check, since headers
        # also start with #).
        m_sec = section_re.match(s)
        if m_sec:
            current_section = m_sec.group(1).strip()
            pending_tier = None
            continue
        m_tier = tier_re.match(s)
        if m_tier:
            t = m_tier.group(1).lower()
            if t in VALID_TIERS:
                pending_tier = t
            continue
        if s.startswith("#"):
            continue  # other comments are documentation
        m_var = var_re.match(s)
        if m_var:
            name = m_var.group(1)
            tier = pending_tier or TIER_OPTIONAL
            specs.append(VarSpec(name=name, section=current_section, tier=tier))
            pending_tier = None
    return specs


# ─────────────────────────────────────────────────────────────────────
# Validation rules.
# ─────────────────────────────────────────────────────────────────────
def _is_url_ok(val: str) -> Optional[str]:
    if val.startswith("https://"):
        return None
    if val.startswith("http://localhost") or val.startswith("http://127.0.0.1"):
        return None
    return "must start with https:// (or http://localhost for dev)"


def _is_db_or_redis_url_ok(val: str) -> Optional[str]:
    """Looser URL validator for DB/Redis connection strings."""
    if "://" not in val:
        return "must be a connection URL with scheme://"
    scheme = val.split("://", 1)[0].lower()
    allowed = {"postgres", "postgresql", "redis", "rediss", "https", "http"}
    if scheme not in allowed:
        return "scheme must be one of postgres/postgresql/redis/rediss/https/http"
    return None


def _is_alphanum_long(val: str) -> Optional[str]:
    if len(val) <= 20:
        return "expected length > 20"
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", val):
        return "expected alphanumeric (a-z, A-Z, 0-9, _, -)"
    return None


def _is_port(val: str) -> Optional[str]:
    try:
        n = int(val)
    except ValueError:
        return "not an integer"
    if not (1 <= n <= 65535):
        return "out of range 1-65535"
    return None


def _is_bool(val: str) -> Optional[str]:
    if val.lower() in ("true", "false", "1", "0"):
        return None
    return "expected true|false|1|0"


# Validators dict — keys are var names; the matching function is
# called with the value (already known non-empty, non-placeholder)
# and returns None on success or a static reason string on failure.
EXACT_VALIDATORS: Dict[str, Callable[[str], Optional[str]]] = {
    "OPENAI_API_KEY": lambda v: None if v.startswith("sk-") else "should start with 'sk-'",
    "ANTHROPIC_API_KEY": lambda v: None if v.startswith("sk-ant-") else "should start with 'sk-ant-'",
    "DEEPSEEK_API_KEY": lambda v: None if v.startswith("sk-") else "should start with 'sk-'",
    "GEMINI_API_KEY": _is_alphanum_long,
    "GOOGLE_API_KEY": _is_alphanum_long,
    "MOONSHOT_API_KEY": lambda v: None if v.startswith("sk-") or _is_alphanum_long(v) is None else "expected sk-* or alphanum >20 chars",
    "KIMI_API_KEY": lambda v: None if v.startswith("sk-") or _is_alphanum_long(v) is None else "expected sk-* or alphanum >20 chars",
}

# Suffix-based validators (apply if no EXACT match).
SUFFIX_VALIDATORS: List = [
    ("_URL", _is_url_ok),
    ("_PORT", _is_port),
    ("_ENABLED", _is_bool),
    ("_FEATURE_FLAG", _is_bool),
]

# Vars that need the looser DB/Redis URL validator (allow postgres://, redis://).
NAME_DB_REDIS_URLS = {
    "DATABASE_URL",
    "POSTGRES_URL",
    "SUPABASE_DB_URL",
    "REDIS_URL",
}
# Vars that must be https:// (or http://localhost).
NAME_HTTPS_URL_OVERRIDES = {"SUPABASE_URL", "SLACK_WEBHOOK_URL"}
NAME_PORT_OVERRIDES = {"PORT"}


def is_placeholder(val: str) -> bool:
    if not val:
        return False
    stripped = val.strip()
    if stripped in PLACEHOLDER_EXACT:
        return True
    upper = stripped.upper()
    for tok in PLACEHOLDER_TOKENS:
        if tok.upper() in upper:
            return True
    return False


def validate_value(name: str, val: str) -> Optional[str]:
    """Return None on success, or a reason string on failure.

    Caller has already confirmed `val` is non-empty and non-placeholder.
    """
    # Exact-name validators win.
    if name in EXACT_VALIDATORS:
        return EXACT_VALIDATORS[name](val)
    # Name-based overrides.
    if name in NAME_DB_REDIS_URLS:
        return _is_db_or_redis_url_ok(val)
    if name in NAME_HTTPS_URL_OVERRIDES:
        return _is_url_ok(val)
    if name in NAME_PORT_OVERRIDES:
        return _is_port(val)
    # Suffix-based — but skip _URL if a name override already handled it.
    for suffix, fn in SUFFIX_VALIDATORS:
        if name.endswith(suffix):
            return fn(val)
    return None  # no validator -> presence/non-placeholder is enough


# ─────────────────────────────────────────────────────────────────────
# Core check.
# ─────────────────────────────────────────────────────────────────────
def check_var(spec: VarSpec, env: Dict[str, str]) -> CheckResult:
    raw = env.get(spec.name, "")
    val = raw.strip() if isinstance(raw, str) else ""
    if val == "":
        # Missing/empty.
        if spec.tier == TIER_REQUIRED:
            return CheckResult(spec, STATUS_FAIL, "missing or empty")
        if spec.tier == TIER_RECOMMENDED:
            return CheckResult(spec, STATUS_WARN, "missing (recommended)")
        return CheckResult(spec, STATUS_OK, "unset (optional)")
    if is_placeholder(val):
        if spec.tier == TIER_REQUIRED:
            return CheckResult(spec, STATUS_FAIL, "placeholder value")
        if spec.tier == TIER_RECOMMENDED:
            return CheckResult(spec, STATUS_WARN, "placeholder value (recommended)")
        return CheckResult(spec, STATUS_WARN, "placeholder value (optional)")
    err = validate_value(spec.name, val)
    if err is not None:
        if spec.tier == TIER_REQUIRED:
            return CheckResult(spec, STATUS_FAIL, f"invalid format: {err}")
        if spec.tier == TIER_RECOMMENDED:
            return CheckResult(spec, STATUS_WARN, f"invalid format: {err}")
        return CheckResult(spec, STATUS_WARN, f"invalid format: {err}")
    return CheckResult(spec, STATUS_OK, "")


def check_recommended_group(
    results: List[CheckResult], group_names: List[str]
) -> List[CheckResult]:
    """For LLM keys: at least one must pass. If all fail, escalate the
    first one's status to FAIL with reason 'no LLM key configured'.
    Returns the (possibly modified) results list."""
    in_group = [r for r in results if r.spec.name in group_names]
    if not in_group:
        return results
    if any(r.status == STATUS_OK for r in in_group):
        return results
    # All recommended LLM keys failed/missing — escalate first one.
    for r in results:
        if r.spec.name == in_group[0].spec.name:
            r.status = STATUS_FAIL
            r.reason = "no LLM provider key configured (need at least 1)"
            break
    return results


# ─────────────────────────────────────────────────────────────────────
# Output rendering.
# ─────────────────────────────────────────────────────────────────────
def render_table(results: List[CheckResult]) -> str:
    rows = []
    name_w = max(len(r.spec.name) for r in results)
    section_w = max(len(r.spec.section) for r in results)
    tier_w = max(len(r.spec.tier) for r in results)
    name_w = max(name_w, len("VAR"))
    section_w = max(section_w, len("SECTION"))
    tier_w = max(tier_w, len("TIER"))
    header = f"  {'VAR'.ljust(name_w)}  {'SECTION'.ljust(section_w)}  {'TIER'.ljust(tier_w)}  STATUS  REASON"
    sep = "  " + "-" * (name_w + section_w + tier_w + len("STATUS  REASON  ") + 4)
    rows.append(header)
    rows.append(sep)
    for r in results:
        glyph = GLYPH.get(r.status, "?")
        # Pad glyph cell to width 2 (some glyphs are wide chars; pad
        # generously to keep columns mostly aligned).
        status_cell = f"{glyph} {r.status}".ljust(7)
        rows.append(
            f"  {r.spec.name.ljust(name_w)}  "
            f"{r.spec.section.ljust(section_w)}  "
            f"{r.spec.tier.ljust(tier_w)}  "
            f"{status_cell}  {r.reason}"
        )
    return "\n".join(rows)


def render_summary(
    results: List[CheckResult], ignore_optional: bool
) -> tuple[str, int]:
    n_total = len(results)
    n_ok = sum(1 for r in results if r.status == STATUS_OK)
    n_warn = sum(1 for r in results if r.status == STATUS_WARN)
    n_fail = sum(1 for r in results if r.status == STATUS_FAIL)
    summary = (
        f"\nSummary: {n_total} vars checked  |  "
        f"{n_ok} OK  |  {n_warn} WARN  |  {n_fail} FAIL"
    )
    if ignore_optional:
        summary += "  (--ignore-optional: only required-tier failures are fatal)"
    # Determine exit code.
    if n_fail > 0:
        # If --ignore-optional, only count required-tier fails.
        if ignore_optional:
            req_fail = sum(
                1
                for r in results
                if r.status == STATUS_FAIL and r.spec.tier == TIER_REQUIRED
            )
            return summary, (1 if req_fail > 0 else 0)
        return summary, 1
    return summary, 0


# ─────────────────────────────────────────────────────────────────────
# CLI.
# ─────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="check-prod-config.py",
        description=(
            "Verify the current process environment (or an --env-file) "
            "against .env.example. Never echoes actual values; emits "
            "var name + tier + status + static reason only."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a .env file to check instead of os.environ.",
    )
    parser.add_argument(
        "--ignore-optional",
        action="store_true",
        help="Only fail (exit 1) on required-tier issues.",
    )
    parser.add_argument(
        "--env-example",
        type=Path,
        default=ENV_EXAMPLE,
        help=f"Path to .env.example (default: {ENV_EXAMPLE}).",
    )
    args = parser.parse_args()

    try:
        specs = parse_env_example(args.env_example)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not specs:
        print(
            f"ERROR: no var specs parsed from {args.env_example}",
            file=sys.stderr,
        )
        return 2

    if args.env_file is not None:
        try:
            env = parse_env_file(args.env_file)
        except FileNotFoundError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        source = str(args.env_file)
    else:
        env = dict(os.environ)
        source = "process environment"

    print(f"jobHunt config check  |  source: {source}")
    print(f"canonical spec: {args.env_example} ({len(specs)} vars)\n")

    results = [check_var(spec, env) for spec in specs]

    # Special handling: at least one recommended LLM key must pass.
    llm_keys = [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
    ]
    # Only enforce the "any LLM" rule for vars whose declared tier is
    # `recommended`. ANTHROPIC_API_KEY/OPENAI_API_KEY are required and
    # already enforced individually.
    rec_llm = [
        s.name
        for s in specs
        if s.name in llm_keys and s.tier == TIER_RECOMMENDED
    ]
    if rec_llm:
        # Only escalate if NONE of the LLM keys (required OR recommended)
        # are configured — otherwise the user is fine.
        any_llm_ok = any(
            r.status == STATUS_OK for r in results if r.spec.name in llm_keys
        )
        if not any_llm_ok:
            for r in results:
                if r.spec.name == rec_llm[0]:
                    r.status = STATUS_FAIL
                    r.reason = "no LLM provider key configured (need >=1)"
                    break

    print(render_table(results))
    summary, exit_code = render_summary(results, args.ignore_optional)
    print(summary)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
