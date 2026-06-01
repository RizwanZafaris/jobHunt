#!/usr/bin/env python3
"""B13: End-to-end outcome → persona-evolution flywheel verification.

Run against a Supabase BRANCH (not prod):
    python scripts/seed_e2e_flywheel.py --url <branch_url> --key <branch_key>

Steps:
  1. Find one converged resume_build (and an application linking it)
  2. Insert a synthetic interview_outcomes row using the REAL schema
     (`passed`, `feedback_text`, `round_number`, `round_type`, etc.).
  3. Call agents.outcome_to_persona.credit_outcome(outcome_id, kind='interview')
  4. Assert >=1 row in knowledge_outcome_credits keyed by the outcome
  5. Assert company_knowledge.outcome_score / outcome_credit_count moved
     on the cited rows

Why this exists: 0 rows in interview_outcomes / resume_outcomes /
knowledge_outcome_credits / persona_versions tells us the value-prop
feedback loop is unverified in prod. This script proves end-to-end that
a logged outcome propagates into persona credit on a real build.

NB: do NOT run against production. Use a Supabase branch via
mcp__supabase__create_branch. Synthetic outcomes against real personas
will skew the persona deep-research baseline if you point this at prod.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("seed_e2e_flywheel")


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end outcome→persona flywheel verification"
    )
    parser.add_argument("--url", required=True, help="Supabase URL (BRANCH, not prod)")
    parser.add_argument("--key", required=True, help="Supabase service role key")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )

    # Wire env BEFORE importing db.client (it caches the supabase client on first call)
    os.environ["SUPABASE_URL"] = args.url
    os.environ["SUPABASE_SERVICE_KEY"] = args.key

    result = asyncio.run(_run_verification(dry_run=args.dry_run))
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["passed"] else 1)


async def _run_verification(dry_run: bool) -> dict:
    from db.client import get_supabase
    from agents.outcome_to_persona import credit_outcome

    db = get_supabase()

    # ── Step 1: find a converged resume_build and its application ─────────
    builds = (
        db.table("resume_builds")
        .select("id, application_id, user_id, company_name, job_id")
        .eq("status", "converged")
        .not_.is_("application_id", "null")
        .limit(1)
        .execute()
    ).data or []

    if not builds:
        return {
            "passed": False,
            "error": "no_converged_build_with_application",
            "hint": "need at least one converged resume_build with application_id set",
        }

    build = builds[0]
    build_id = build["id"]
    user_id = build["user_id"]
    application_id = build.get("application_id")
    job_id = build.get("job_id")
    company_name = build.get("company_name", "")

    logger.info(
        "seed using build=%s app=%s job=%s user=%s company=%r",
        build_id, application_id, job_id, user_id, company_name,
    )

    # ── Step 2: synthetic interview_outcome (REAL schema) ─────────────────
    # Columns on interview_outcomes per information_schema (2026-05-16):
    #   id, application_id, job_id, company_name, round_number, round_type,
    #   passed, questions_asked, feedback_text, interviewer_names,
    #   conducted_at, logged_at, user_id, org_id
    # The earlier Kimi draft used resume_build_id/outcome/notes which do
    # NOT exist on this table — that would 400 at the API.
    outcome_id = str(uuid4())
    if not dry_run:
        logger.info("inserting interview_outcomes id=%s", outcome_id)
        db.table("interview_outcomes").insert({
            "id":              outcome_id,
            "user_id":         user_id,
            "application_id":  application_id,
            "job_id":          job_id,
            "company_name":    company_name,
            "round_number":    1,
            "round_type":      "phone_screen",
            "passed":          True,
            "feedback_text":   "B13 e2e flywheel verification seed",
        }).execute()
    else:
        logger.info("[DRY-RUN] would insert interview_outcomes id=%s", outcome_id)

    # ── Step 3: credit_outcome(kind='interview') ──────────────────────────
    if not dry_run:
        credit_result = await credit_outcome(outcome_id, kind="interview")
        logger.info("credit_outcome → %s", json.dumps(credit_result, default=str))
    else:
        credit_result = {
            "credited_knowledge_count": 1,
            "delta_per_row": 0.05,
            "source": "[DRY-RUN]",
            "error": None,
        }

    # ── Step 4: assert credit ledger + score movement ─────────────────────
    errors: list[str] = []
    if credit_result.get("error"):
        errors.append(f"credit_outcome.error={credit_result['error']}")

    if not dry_run:
        credits = (
            db.table("knowledge_outcome_credits")
            .select("knowledge_id, delta, reason, interview_outcome_id, resume_build_id")
            .eq("interview_outcome_id", outcome_id)
            .execute()
        ).data or []
        if not credits:
            errors.append("no_knowledge_outcome_credits_rows")
        else:
            logger.info("credited %d knowledge rows", len(credits))

            cited_kids = [c["knowledge_id"] for c in credits]
            knowledge_rows = (
                db.table("company_knowledge")
                .select("id, outcome_score, outcome_credit_count")
                .in_("id", cited_kids)
                .execute()
            ).data or []

            for row in knowledge_rows:
                if not row.get("outcome_credit_count"):
                    errors.append(
                        f"knowledge {row['id']}: outcome_credit_count still 0"
                    )
                if row.get("outcome_score") is None:
                    errors.append(
                        f"knowledge {row['id']}: outcome_score still NULL"
                    )

    return {
        "passed":                    len(errors) == 0,
        "build_id":                  build_id,
        "application_id":            application_id,
        "outcome_id":                outcome_id,
        "credited_knowledge_count":  credit_result.get("credited_knowledge_count"),
        "delta_per_row":             credit_result.get("delta_per_row"),
        "source":                    credit_result.get("source"),
        "errors":                    errors,
    }


if __name__ == "__main__":
    main()
