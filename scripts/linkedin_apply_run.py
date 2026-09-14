#!/usr/bin/env python3
"""
scripts/linkedin_apply_run.py — one-command runner for the LinkedIn Easy Apply
pipeline (G10).

Runs the whole flow on your own machine, where LinkedIn is reachable:

    python scripts/linkedin_apply_run.py --dry-run          # prepare only, submit nothing
    python scripts/linkedin_apply_run.py --dry-run --limit 1  # single-job smoke test
    python scripts/linkedin_apply_run.py --submit --limit 25   # real submissions

Safety
------
- --dry-run is the DEFAULT. You must pass --submit explicitly to send anything.
- --submit prompts for typed confirmation unless --yes is passed.
- Preflight fails loud and lists exactly which env vars / tables are missing,
  rather than half-running and leaving applications in a weird state.
- Start with --limit 1. LinkedIn changes its Easy Apply DOM regularly; if the
  selectors in agents/ats_patterns/linkedin.yml have drifted you want to find
  out on one job, not twenty-five.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from uuid import UUID

REQUIRED_ENV = [
    ("SUPABASE_URL", "Supabase project URL"),
    ("SUPABASE_SERVICE_KEY", "Supabase service-role key"),
    ("LINKEDIN_SESSION_COOKIE", "LinkedIn li_at cookie (DevTools > Application > Cookies)"),
    ("OPENROUTER_API_KEY", "OpenRouter API key for answer generation"),
]


def preflight() -> str:
    """Verify env + DB table. Returns user_id. Exits non-zero on any failure."""
    missing = [(k, d) for k, d in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print("PREFLIGHT FAILED — missing environment variables:\n")
        for k, d in missing:
            print(f"  {k:<28} {d}")
        print("\nAdd these to .env and re-run.")
        sys.exit(1)

    try:
        from db.client import get_supabase
        db = get_supabase()
    except Exception as exc:
        print(f"PREFLIGHT FAILED — cannot reach Supabase: {exc}")
        sys.exit(1)

    # Migration 047 check.
    try:
        db.table("linkedin_apply_queue").select("id").limit(1).execute()
    except Exception as exc:
        print("PREFLIGHT FAILED — table 'linkedin_apply_queue' not found.")
        print("Run this migration in the Supabase SQL editor first:")
        print("  db/migrations/2026_09_14_047_linkedin_apply_queue.sql")
        print(f"\n({type(exc).__name__}: {str(exc)[:160]})")
        sys.exit(1)

    user_id = os.environ.get("RIZWAN_USER_ID", "00000000-0000-0000-0000-000000000001")
    print("Preflight OK — env vars present, linkedin_apply_queue reachable.")
    print(f"user_id: {user_id}\n")
    return user_id


async def run(args: argparse.Namespace) -> int:
    user_id_str = preflight()
    user_id = UUID(user_id_str)

    from agents.linkedin_easy_apply import get_eligible_jobs

    eligible = get_eligible_jobs(user_id=user_id)
    if not eligible:
        print("No eligible roles found.")
        print("\nA role qualifies when ALL of these hold:")
        print("  - letter_grade = 'A' (G5 composite >= 85)")
        print("  - apply_url is a linkedin.com URL")
        print("  - posting still open, not already applied")
        print("  - location in GCC / Singapore / Europe / UK / USA")
        print("  - title is a product- or program-management role")
        print("\nIf you expected matches, check that G5 scoring has run:")
        print("  POST /workspace/{job_id}/score")
        return 0

    targets = eligible[: args.limit]
    print(f"{len(eligible)} eligible role(s); acting on {len(targets)}:\n")
    for j in targets:
        print(f"  [{j.composite:>3}] {j.title}")
        print(f"        {j.company} — {j.location}")
        print(f"        resume: {'ready' if j.resume_build_id else 'NOT BUILT (G2 will be enqueued)'}")
    print()

    if args.submit and not args.yes:
        print("=" * 68)
        print("These are REAL applications to REAL employers. This cannot be undone.")
        print("=" * 68)
        reply = input(f'Type "APPLY" to submit {len(targets)} application(s): ').strip()
        if reply != "APPLY":
            print("Aborted — nothing submitted.")
            return 1
        print()

    # Drive the batch endpoint's logic directly (no API server needed).
    from api.linkedin_apply import BatchApplyRequest, batch_apply
    from api.users import User

    user = User(id=user_id_str, email=os.environ.get("RIZWAN_EMAIL", ""))
    report = await batch_apply(
        BatchApplyRequest(
            confirmed=args.submit,
            dry_run=not args.submit,
            max_jobs=args.limit,
        ),
        current_user=user,
    )

    print("=" * 68)
    print("REPORT")
    print("=" * 68)
    print(f"  eligible total   : {report.total_eligible}")
    print(f"  attempted        : {report.attempted}")
    print(f"  SUBMITTED        : {report.submitted}")
    print(f"  prepared only    : {report.prepared_only}")
    print(f"  awaiting resume  : {report.resume_building}")
    print(f"  failed           : {report.failed}")
    print(f"  OpenRouter spend : ${report.total_cost_usd}")
    print()
    for it in report.items:
        print(f"  [{it.outcome:>16}] {it.title} @ {it.company}")
        if it.detail:
            print(f"                     {it.detail[:100]}")

    if not args.submit:
        print("\nDry run — nothing was submitted.")
        print("Re-run with --submit once the prepared output looks right.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Run the LinkedIn Easy Apply pipeline.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True,
                      help="Prepare and pause at Review; submit nothing (default).")
    mode.add_argument("--submit", action="store_true",
                      help="Actually submit applications. Prompts unless --yes.")
    p.add_argument("--limit", type=int, default=1,
                   help="Max roles this run (default 1 — raise once selectors are proven).")
    p.add_argument("--yes", action="store_true",
                   help="Skip the typed confirmation prompt with --submit.")
    args = p.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted — nothing further submitted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
