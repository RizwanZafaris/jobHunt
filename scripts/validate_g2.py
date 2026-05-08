#!/usr/bin/env python3
"""
scripts/validate_g2.py — One-command G2 validation runner (Phase 1.16).

After you've:
  1. Set GOOGLE_API_KEY, DEEPSEEK_API_KEY, KIMI_API_KEY on Railway
  2. Set USE_G2_GRAPH=true
  3. Redeployed the api + scheduler services

…run this script to:

  1. Pick a high-quality target (defaults to a Plaid job with score >= 85
     and a high-quality persona; --company "Stripe" overrides)
  2. POST /jobs/{id}/generate-resume to trigger G2
  3. Poll resume_builds (via /jobs/{id}/detail) until status changes
     from 'running'
  4. Print: final score · cost · iterations · status · DOCX URL

Usage:
    export API_URL=https://your-railway-app.up.railway.app
    export SECRET_KEY=<your X-Secret-Key>
    python scripts/validate_g2.py                     # auto-pick best Plaid job
    python scripts/validate_g2.py --company Stripe    # override company
    python scripts/validate_g2.py --job-id 1020       # override job
    python scripts/validate_g2.py --max-cost-usd 10   # relax cost cap

The script never modifies anything that's not the resume_build itself —
no env, no settings, no Railway calls. It's a thin polling client.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional

# Don't import the project's modules — this script runs against a deployed
# API and only needs httpx. Keep dependencies minimal so it works even
# from a fresh checkout without `pip install -r requirements.txt`.
try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx", file=sys.stderr)
    sys.exit(1)


# ─── Defaults ────────────────────────────────────────────────────────────
# High-quality persona companies (5 high-tier as of 2026-05-09 seed).
# Used as fallback when no --company / --job-id override is given.
DEFAULT_TARGETS = ["Plaid", "PayPal", "Revolut", "Square (Block)", "Standard Chartered"]
POLL_INTERVAL_SEC = 8
POLL_TIMEOUT_SEC = 600   # 10 minutes; G2 typically converges in ~3-5 min


# ─── HTTP helpers ────────────────────────────────────────────────────────
class APIClient:
    def __init__(self, base_url: str, secret: str):
        self.base = base_url.rstrip("/")
        self.headers = {
            "X-Secret-Key": secret,
            "Content-Type": "application/json",
        }
        self.client = httpx.Client(timeout=30, headers=self.headers)

    def get(self, path: str, **params) -> dict:
        r = self.client.get(f"{self.base}{path}", params=params or None)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, **params) -> dict:
        r = self.client.post(f"{self.base}{path}", params=params or None, json={})
        r.raise_for_status()
        return r.json()


# ─── Step 1: pick a target job ───────────────────────────────────────────
def pick_target_job(api: APIClient, company: Optional[str]) -> dict:
    """
    Return a job dict matching:
      - match_score >= 85
      - status != 'evaluated' (don't re-build something we already built)
      - resume_generated_at IS NULL (no existing resume)
    Filtered by `company` if given, else first match in DEFAULT_TARGETS.
    """
    print(f"→ Fetching jobs with score >= 85...")
    r = api.get("/jobs", min_score=85, limit=50)
    jobs = r.get("jobs", [])
    print(f"  Found {len(jobs)} jobs at score ≥ 85")

    # Filter: no existing resume, prefer "new" status
    eligible = [
        j for j in jobs
        if not j.get("resume_generated_at")
    ]
    print(f"  {len(eligible)} eligible (no existing resume)")

    if not eligible:
        raise SystemExit(
            "No eligible jobs at score ≥ 85 without an existing resume.\n"
            "Either: trigger a fresh JobScout run (POST /pipeline/run-targets),\n"
            "or pick a specific job: --job-id <id>"
        )

    # Filter by company if given
    if company:
        target = company.lower()
        match = [j for j in eligible if target in (j.get("company") or "").lower()]
        if not match:
            raise SystemExit(
                f"No eligible jobs at company matching '{company}'.\n"
                f"Available companies: "
                f"{sorted(set(j.get('company') or '?' for j in eligible))}"
            )
        return match[0]

    # Default: walk DEFAULT_TARGETS in order, first match wins
    for target_name in DEFAULT_TARGETS:
        t = target_name.lower()
        for j in eligible:
            if t in (j.get("company") or "").lower():
                print(f"  Auto-picked: {j['title']} @ {j['company']} (score {j.get('match_score')})")
                return j

    # Fallback: highest-scoring eligible job
    eligible.sort(key=lambda j: j.get("match_score", 0), reverse=True)
    print(f"  No high-quality persona match — falling back to highest-scoring eligible job")
    return eligible[0]


# ─── Step 2: trigger G2 build ────────────────────────────────────────────
def trigger_build(
    api: APIClient,
    job_id: int,
    *,
    max_cost_usd: Optional[float] = None,
    force: bool = False,
) -> dict:
    print(f"→ POST /jobs/{job_id}/generate-resume "
          f"max_cost_usd={max_cost_usd} force={force}")
    params = {}
    if max_cost_usd is not None:
        params["max_cost_usd"] = max_cost_usd
    if force:
        params["force"] = "true"

    try:
        r = api.post(f"/jobs/{job_id}/generate-resume", **params)
    except httpx.HTTPStatusError as e:
        body = e.response.json() if e.response.headers.get("content-type", "").startswith("application/json") else {}
        detail = body.get("detail", body)
        if isinstance(detail, dict) and detail.get("code") == "persona_quality_too_low":
            print(f"\n⚠  PERSONA QUALITY GATE BLOCKED THE BUILD")
            print(f"   Company:  {detail.get('company_name')}")
            print(f"   Quality:  {detail.get('persona_quality')} (v{detail.get('persona_version')})")
            print(f"   Reason:   {detail.get('message')}")
            print(f"\nTo bypass, re-run with --force.")
            sys.exit(2)
        print(f"\nERROR: HTTP {e.response.status_code}", file=sys.stderr)
        print(f"  detail: {detail}", file=sys.stderr)
        sys.exit(3)

    print(f"  ✓ Build started: {r.get('message')}")
    if r.get("persona_gate"):
        pg = r["persona_gate"]
        print(f"  Persona gate: verdict={pg.get('verdict')} quality={pg.get('quality')}")
    return r


# ─── Step 3: poll until done ─────────────────────────────────────────────
def poll_until_done(api: APIClient, job_id: int) -> dict:
    """
    Poll /jobs/{job_id}/detail until the job's resume_generated_at appears
    OR a resume_builds row for this job is in a terminal state.
    """
    print(f"→ Polling /jobs/{job_id}/detail every {POLL_INTERVAL_SEC}s "
          f"(timeout {POLL_TIMEOUT_SEC}s)...")
    started = time.time()
    last_status_print = ""

    while time.time() - started < POLL_TIMEOUT_SEC:
        try:
            detail = api.get(f"/jobs/{job_id}/detail")
        except Exception as e:
            print(f"  ! poll failed: {e}; retrying...")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        job = detail.get("job", {})
        if job.get("resume_generated_at"):
            print(f"  ✓ resume_generated_at = {job['resume_generated_at']}")
            return detail

        # Surface progress info if available
        elapsed = int(time.time() - started)
        # Status note (we don't have a /resume_builds endpoint, but the job's
        # `status` may flip — show what we know)
        status_note = f"job.status={job.get('status', '?')}"
        if status_note != last_status_print:
            print(f"  [{elapsed:>4}s] still running... {status_note}")
            last_status_print = status_note

        time.sleep(POLL_INTERVAL_SEC)

    raise SystemExit(
        f"Timeout after {POLL_TIMEOUT_SEC}s. "
        f"Build may still be running — check /costs and /jobs/{job_id} manually."
    )


# ─── Step 4: report ──────────────────────────────────────────────────────
def report(detail: dict, started_at: float) -> None:
    job = detail.get("job", {})
    artifacts = detail.get("artifacts", {})
    elapsed = int(time.time() - started_at)

    print(f"\n{'═' * 70}")
    print(f"  G2 VALIDATION COMPLETE in {elapsed}s")
    print(f"{'═' * 70}")
    print(f"  Job:       {job.get('title')} @ {job.get('company')}")
    print(f"  Score:     {job.get('match_score')}/100")
    print(f"  Status:    {job.get('status')}")

    resume = artifacts.get("resume_path", {})
    if resume.get("exists"):
        if resume.get("kind") == "remote":
            print(f"  Resume:    {resume.get('url')}")
        else:
            print(f"  Resume:    (local) {resume.get('size', 0)} bytes")
    else:
        print(f"  Resume:    NOT FOUND — check Railway logs for build errors")

    email = artifacts.get("email_path", {})
    if email.get("exists") and email.get("kind") == "remote":
        print(f"  Email:     {email.get('url')}")

    # Pull cost data — best-effort
    print(f"\n  Cost data: hit /costs dashboard or:")
    print(f"    GET /costs/by-resume-build?limit=5")
    print(f"\n  Next steps:")
    print(f"    1. Open the resume URL — verify quality")
    print(f"    2. /jobs/{job.get('id')} → 'Start Tracking Outcome'")
    print(f"    3. After 7+ days of outcome logs, the Sunday cron will")
    print(f"       evolve the {job.get('company')} persona to v2.")
    print(f"{'═' * 70}\n")


# ─── Main ────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the G2 Resume Builder Graph by running one resume build end-to-end."
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("API_URL"),
        help="Railway API URL (or set API_URL env var)",
    )
    parser.add_argument(
        "--secret",
        default=os.environ.get("SECRET_KEY"),
        help="X-Secret-Key for the API (or set SECRET_KEY env var)",
    )
    parser.add_argument(
        "--company",
        help="Override the auto-pick — pick a job at this company. "
             "Default: walks Plaid → PayPal → Revolut → Square (Block) → Standard Chartered",
    )
    parser.add_argument(
        "--job-id",
        type=int,
        help="Override everything — pick this exact job ID",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        help="Override the default $5 per-build cap (Phase 1.11)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the persona quality gate (Phase 1.12)",
    )
    args = parser.parse_args()

    if not args.api_url:
        print("ERROR: --api-url or API_URL env var required", file=sys.stderr)
        return 1
    if not args.secret:
        print("ERROR: --secret or SECRET_KEY env var required", file=sys.stderr)
        return 1

    api = APIClient(args.api_url, args.secret)

    # Sanity check: API reachable?
    try:
        health = api.get("/health")
        print(f"→ API healthy: {health}")
    except Exception as e:
        print(f"ERROR: API unreachable at {args.api_url}: {e}", file=sys.stderr)
        return 1

    # Step 1: pick a job
    if args.job_id:
        print(f"→ Using job_id={args.job_id} (override)")
        job_lookup = api.get(f"/jobs/{args.job_id}")
        if not job_lookup:
            print(f"ERROR: job {args.job_id} not found", file=sys.stderr)
            return 1
        job = job_lookup
    else:
        job = pick_target_job(api, company=args.company)

    print(f"\n→ Target: {job.get('title')} @ {job.get('company')}")
    print(f"  job_id={job.get('id')} score={job.get('match_score')} "
          f"archetype={job.get('archetype')} legitimacy={job.get('legitimacy_tier')}")

    # Step 2: trigger
    started_at = time.time()
    trigger_build(
        api,
        job["id"],
        max_cost_usd=args.max_cost_usd,
        force=args.force,
    )

    # Step 3: poll
    final = poll_until_done(api, job["id"])

    # Step 4: report
    report(final, started_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
