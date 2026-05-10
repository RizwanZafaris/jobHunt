"""
evals/run_golden.py — Run all (or one) golden cases through the judge.

Two modes:

  1. Judge-existing-resume mode (default):
     For each case, read a candidate resume from a path you provide and
     score it. Use when you've generated a resume offline and just want a
     rating.

  2. Invoke-G2 mode (--invoke-g2):
     For each case, call resume_agents.run_g2_graph against a job_id you
     provide via --job-id (single case) or via the case JSON's
     "job_id" field if present. Then score the produced resume.

CLI:
    python -m evals.run_golden --case all --out evals/reports/
    python -m evals.run_golden --case 001 --resume path/to/generated.md
    python -m evals.run_golden --case all --invoke-g2 --job-ids 1,2,3

Output: evals/reports/{ISO_timestamp}.json
        Plus a summary table to stdout (rich if available, else plain).

Design notes:
  - We run cases sequentially (not asyncio.gather) so a slow Opus call
    doesn't time-bombard another. The eval is rate-limited by the judge
    not by parallelism.
  - The G2 invocation is gated behind --invoke-g2 because it actually
    spends LLM money. Default mode just judges resumes you already have.
  - Profile is loaded from /Users/rizwanzafar/Desktop/jobHunt/cv.md by default.
    Override with --profile path/to/profile.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO_ROOT / "evals" / "golden"
DEFAULT_PROFILE = REPO_ROOT / "cv.md"
DEFAULT_REPORTS_DIR = REPO_ROOT / "evals" / "reports"


# ─── Case loading ─────────────────────────────────────────────────────────
def list_case_files() -> list[Path]:
    """Return all case_NNN_*.json files (excluding *_golden_resume.md)."""
    return sorted(p for p in GOLDEN_DIR.glob("case_*.json"))


def resolve_case_files(case_arg: str) -> list[Path]:
    """
    --case all      → every case_*.json
    --case 001      → the case whose filename starts with case_001
    --case 001,003  → multiple
    """
    all_cases = list_case_files()
    if case_arg.lower() == "all":
        return all_cases

    wanted = [c.strip() for c in case_arg.split(",") if c.strip()]
    chosen: list[Path] = []
    for w in wanted:
        # accept "001", "case_001", or full filename
        prefix = w if w.startswith("case_") else f"case_{w}"
        matches = [p for p in all_cases if p.name.startswith(prefix)]
        if not matches:
            raise SystemExit(f"No golden case file matches {w!r} in {GOLDEN_DIR}")
        chosen.extend(matches)
    return chosen


def load_case(path: Path) -> dict:
    with path.open() as f:
        case = json.load(f)
    # Resolve sibling golden resume if present
    golden_md_path = path.with_name(path.stem.replace(
        "_marqeta_groupgmpm_fraud", ""
    ).replace(
        "_visa_seniorpm_payments", ""
    ).replace(
        "_stripe_lead_pm", ""
    ) + "_golden_resume.md")
    # Simpler: case_001_*.json → case_001_golden_resume.md
    case_number = path.stem.split("_")[1]  # "001"
    sibling = GOLDEN_DIR / f"case_{case_number}_golden_resume.md"
    if sibling.exists():
        case["_golden_resume_md"] = sibling.read_text()
    return case


def load_profile(profile_path: Path) -> str:
    if not profile_path.exists():
        raise SystemExit(
            f"Profile file not found: {profile_path}. "
            f"Pass --profile path/to/profile.md or place a cv.md at the repo root."
        )
    return profile_path.read_text()


# ─── G2 invocation (optional) ────────────────────────────────────────────
async def _maybe_invoke_g2(case: dict, job_id: Optional[int]) -> str:
    """
    Run the G2 graph for one job_id and return the produced resume markdown.
    Lazy import — we don't want to drag langgraph + g2 nodes into the path
    when --invoke-g2 isn't set.
    """
    if job_id is None:
        raise SystemExit(
            f"Case {case.get('id')!r}: --invoke-g2 requires a --job-ids mapping. "
            f"Pass --job-ids or set 'job_id' in the case JSON."
        )
    from resume_agents import run_g2_graph
    final = await run_g2_graph(job_id=job_id, company_name=case.get("company"))
    md = final.get("final_resume_md") or final.get("current_draft") or ""
    if not md:
        raise SystemExit(
            f"G2 graph for job_id={job_id} returned no resume markdown. "
            f"Check resume_builds row for failure."
        )
    return md


# ─── Per-case orchestration ──────────────────────────────────────────────
async def run_one_case(
    case: dict,
    *,
    profile_md: str,
    resume_md: Optional[str] = None,
    invoke_g2: bool = False,
    job_id: Optional[int] = None,
) -> dict:
    """
    Score one case. Returns a per-case result dict.

    Either resume_md is provided (judge-existing mode) OR invoke_g2=True
    triggers a G2 build that produces the resume.
    """
    from evals.judge import judge_resume

    if invoke_g2:
        resume_md = await _maybe_invoke_g2(case, job_id)
    if not resume_md:
        raise SystemExit(
            f"Case {case['id']!r}: no resume to judge. "
            f"Pass --resume <path> or use --invoke-g2."
        )

    golden_payload = None
    if case.get("_golden_resume_md"):
        golden_payload = {"resume_md": case["_golden_resume_md"]}

    verdict = await judge_resume(
        resume_md=resume_md,
        jd=case["jd"],
        persona=case["persona"],
        profile=profile_md,
        golden=golden_payload,
    )

    return {
        "case_id": case["id"],
        "company": case.get("company"),
        "role": case.get("role"),
        "expected_axes": case.get("expected_axes", {}),
        "axes": verdict["axes"],
        "mean": verdict["mean"],
        "pass": verdict["pass"],
        "summary": verdict["summary"],
        "hallucinations_found": verdict["hallucinations_found"],
        "judge_telemetry": verdict["judge"],
        "resume_chars": len(resume_md),
    }


# ─── Reporting ───────────────────────────────────────────────────────────
def aggregate(results: list[dict]) -> dict:
    """Compute overall pass-rate and mean-of-means across all cases."""
    if not results:
        return {"n": 0, "pass_rate": 0.0, "mean_of_means": 0.0}
    n_pass = sum(1 for r in results if r["pass"])
    return {
        "n": len(results),
        "n_pass": n_pass,
        "pass_rate": round(n_pass / len(results), 3),
        "mean_of_means": round(
            sum(r["mean"] for r in results) / len(results), 2
        ),
        "total_judge_cost_usd": round(
            sum(r["judge_telemetry"]["cost_usd"] for r in results), 4
        ),
    }


def write_report(out_dir: Path, payload: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"{ts}.json"
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2)
    return out_path


def print_summary(results: list[dict], overall: dict, out_path: Path) -> None:
    """
    Stdout summary table. Plain print fallback if `rich` is unavailable —
    keeps this CLI usable without adding a dep.
    """
    try:
        from rich.console import Console
        from rich.table import Table
        console = Console()
        t = Table(title=f"Golden eval — {overall['n']} cases")
        t.add_column("Case", style="cyan")
        t.add_column("Company")
        t.add_column("Mean", justify="right")
        for axis in ("ats_keyword_coverage", "evidence_specificity",
                     "persona_fit", "hallucination_check", "length_discipline"):
            t.add_column(axis[:6], justify="right")
        t.add_column("Pass", justify="center")
        t.add_column("Halls", justify="right")
        for r in results:
            t.add_row(
                r["case_id"][:24],
                str(r.get("company", ""))[:14],
                f"{r['mean']:.2f}",
                *[str(r["axes"][a]["score"])
                  for a in ("ats_keyword_coverage", "evidence_specificity",
                            "persona_fit", "hallucination_check", "length_discipline")],
                "PASS" if r["pass"] else "FAIL",
                str(len(r["hallucinations_found"])),
            )
        console.print(t)
        console.print(
            f"\nOverall: pass={overall['n_pass']}/{overall['n']} "
            f"({overall['pass_rate'] * 100:.0f}%), "
            f"mean-of-means={overall['mean_of_means']}, "
            f"judge cost=${overall['total_judge_cost_usd']:.4f}"
        )
        console.print(f"Report written to {out_path}")
    except ImportError:
        print(f"\nGolden eval — {len(results)} cases")
        for r in results:
            print(
                f"  {r['case_id']:<40} {r.get('company', ''):<14} "
                f"mean={r['mean']:.2f} pass={r['pass']} "
                f"halls={len(r['hallucinations_found'])}"
            )
        print(
            f"\nOverall pass={overall['n_pass']}/{overall['n']} "
            f"mean-of-means={overall['mean_of_means']} "
            f"cost=${overall['total_judge_cost_usd']:.4f}"
        )
        print(f"Report: {out_path}")


# ─── Entry point ─────────────────────────────────────────────────────────
async def amain(args) -> int:
    case_files = resolve_case_files(args.case)
    if not case_files:
        print(f"No matching cases for {args.case!r} in {GOLDEN_DIR}", file=sys.stderr)
        return 2

    profile_md = load_profile(args.profile)

    # Map a single resume path or a comma-list of resume paths to cases.
    resume_paths: list[Optional[Path]] = []
    if args.resume:
        # Single resume given — apply to every selected case.
        resume_paths = [Path(args.resume)] * len(case_files)
    elif args.resumes:
        parts = [Path(p.strip()) for p in args.resumes.split(",") if p.strip()]
        if len(parts) != len(case_files):
            raise SystemExit(
                f"--resumes provided {len(parts)} paths but {len(case_files)} cases selected."
            )
        resume_paths = parts
    else:
        resume_paths = [None] * len(case_files)

    job_ids: list[Optional[int]] = [None] * len(case_files)
    if args.job_ids:
        ids = [int(x.strip()) for x in args.job_ids.split(",") if x.strip()]
        if len(ids) != len(case_files):
            raise SystemExit(
                f"--job-ids provided {len(ids)} ids but {len(case_files)} cases selected."
            )
        job_ids = ids

    results: list[dict] = []
    for case_path, resume_path, job_id in zip(case_files, resume_paths, job_ids):
        case = load_case(case_path)
        resume_md: Optional[str] = None
        if resume_path is not None:
            if not resume_path.exists():
                raise SystemExit(f"Resume file not found: {resume_path}")
            resume_md = resume_path.read_text()
        try:
            r = await run_one_case(
                case,
                profile_md=profile_md,
                resume_md=resume_md,
                invoke_g2=args.invoke_g2,
                job_id=job_id,
            )
        except Exception as e:
            logger.exception(f"Case {case['id']} failed")
            r = {
                "case_id": case["id"],
                "company": case.get("company"),
                "role": case.get("role"),
                "error": f"{type(e).__name__}: {e}",
                "pass": False,
                "mean": 0.0,
                "axes": {},
                "summary": "",
                "hallucinations_found": [],
                "judge_telemetry": {"cost_usd": 0.0},
            }
        results.append(r)

    overall = aggregate([r for r in results if "error" not in r])
    payload = {
        "kind": "golden_eval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_model": "claude-opus-4-5",
        "case_files": [str(p) for p in case_files],
        "results": results,
        "overall": overall,
    }
    out_path = write_report(args.out, payload)
    print_summary([r for r in results if "axes" in r], overall, out_path)
    return 0 if overall.get("pass_rate", 0) == 1.0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run golden resume eval cases")
    parser.add_argument(
        "--case", default="all",
        help="'all', a single id like '001', or comma-list '001,003'",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_REPORTS_DIR,
        help="Output directory for the JSON report",
    )
    parser.add_argument(
        "--profile", type=Path, default=DEFAULT_PROFILE,
        help="Path to candidate master profile (markdown). Defaults to <repo>/cv.md",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--resume",
        help="Path to a single already-generated resume to judge against every selected case",
    )
    src.add_argument(
        "--resumes",
        help="Comma-list of resume paths, one per selected case (in order)",
    )
    parser.add_argument(
        "--invoke-g2", action="store_true",
        help="Actually run resume_agents.run_g2_graph against --job-ids before judging.",
    )
    parser.add_argument(
        "--job-ids",
        help="Comma-list of job_id values (matching case order) when using --invoke-g2",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
