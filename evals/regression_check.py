"""
evals/regression_check.py — CI gate that compares the two most recent
golden reports and fails non-zero on a regression.

Trigger conditions (any of):
  - Mean-of-means score on the golden set dropped > 0.3 vs. previous
  - Any individual axis dropped > 0.5 on a case that exists in both reports

The CI workflow at .github/workflows/eval-regression.yml runs this on
every PR that touches agents/, resume_agents/, or db/client.py.

CLI:
    python -m evals.regression_check --reports-dir evals/reports/

Exit codes:
    0   No regression (or insufficient history — first report ever)
    1   Regression detected
    2   Bad inputs (no reports, malformed report)

Notes:
  - Only golden_eval reports (kind == "golden_eval") are compared.
    rag_*.json reports are skipped.
  - "Two most recent" means the two most recently mtimed JSON files.
  - When axes don't exist in the previous report (new case added),
    we skip the per-axis check for that case rather than failing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPORTS_DIR = REPO_ROOT / "evals" / "reports"

MEAN_REGRESSION_THRESHOLD = 0.3   # mean-of-means delta > 0.3 = regression
AXIS_REGRESSION_THRESHOLD = 0.5   # any axis delta > 0.5 = regression


def find_golden_reports(reports_dir: Path) -> list[Path]:
    """
    Return all golden_eval JSON files in the directory, newest first.
    Files are detected by the "kind" field, not filename — robust to
    rag_*.json living alongside golden reports.
    """
    candidates = sorted(
        reports_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    golden: list[Path] = []
    for p in candidates:
        try:
            with p.open() as f:
                payload = json.load(f)
            if payload.get("kind") == "golden_eval":
                golden.append(p)
        except (json.JSONDecodeError, OSError):
            continue
    return golden


def index_results_by_case(report: dict) -> dict[str, dict]:
    """Map case_id → the per-case result dict."""
    return {r.get("case_id"): r for r in report.get("results", []) if r.get("case_id")}


def diff_reports(prev: dict, curr: dict) -> dict:
    """
    Compute regression info between two golden reports.
    Returns:
      {
        "mean_delta": float,       # negative = regression
        "mean_regressed": bool,
        "axis_regressions": [
          {"case_id", "axis", "prev", "curr", "delta"}, ...
        ],
        "summary": str,
      }
    """
    prev_overall = prev.get("overall", {}) or {}
    curr_overall = curr.get("overall", {}) or {}
    prev_mean = prev_overall.get("mean_of_means", 0.0)
    curr_mean = curr_overall.get("mean_of_means", 0.0)
    mean_delta = round(curr_mean - prev_mean, 3)
    mean_regressed = mean_delta < -MEAN_REGRESSION_THRESHOLD

    prev_by_case = index_results_by_case(prev)
    curr_by_case = index_results_by_case(curr)

    axis_regressions: list[dict] = []
    for case_id, curr_case in curr_by_case.items():
        prev_case = prev_by_case.get(case_id)
        if not prev_case:
            continue  # new case, no comparison
        for axis, curr_a in (curr_case.get("axes") or {}).items():
            prev_a = (prev_case.get("axes") or {}).get(axis)
            if not prev_a:
                continue
            prev_score = prev_a.get("score")
            curr_score = curr_a.get("score")
            if prev_score is None or curr_score is None:
                continue
            delta = curr_score - prev_score
            if delta < -AXIS_REGRESSION_THRESHOLD:
                axis_regressions.append({
                    "case_id": case_id,
                    "axis": axis,
                    "prev": prev_score,
                    "curr": curr_score,
                    "delta": delta,
                })

    summary_lines: list[str] = []
    summary_lines.append(
        f"mean-of-means: {prev_mean:.2f} → {curr_mean:.2f} (Δ {mean_delta:+.2f})"
    )
    if mean_regressed:
        summary_lines.append(
            f"  REGRESSION: mean dropped > {MEAN_REGRESSION_THRESHOLD}"
        )
    if axis_regressions:
        summary_lines.append(f"axis regressions ({len(axis_regressions)}):")
        for ar in axis_regressions:
            summary_lines.append(
                f"  {ar['case_id']} :: {ar['axis']}: "
                f"{ar['prev']} → {ar['curr']} (Δ {ar['delta']:+d})"
            )
    elif not mean_regressed:
        summary_lines.append("  no regressions")

    return {
        "mean_delta": mean_delta,
        "mean_regressed": mean_regressed,
        "axis_regressions": axis_regressions,
        "summary": "\n".join(summary_lines),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check golden eval for regression")
    parser.add_argument(
        "--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR,
        help="Directory containing golden_eval JSON reports",
    )
    args = parser.parse_args()

    reports_dir: Path = args.reports_dir
    if not reports_dir.exists():
        print(f"Reports dir does not exist: {reports_dir}", file=sys.stderr)
        return 2

    reports = find_golden_reports(reports_dir)
    if len(reports) == 0:
        print(
            "No golden_eval reports yet — gate is a no-op. "
            "Run `python -m evals.run_golden --case all` to produce the first baseline.",
            file=sys.stderr,
        )
        return 0  # noop, not a failure
    if len(reports) == 1:
        print(
            f"Only one golden report exists ({reports[0].name}) — nothing to "
            f"compare. Gate is a no-op until a second report is produced.",
            file=sys.stderr,
        )
        return 0

    curr_path, prev_path = reports[0], reports[1]
    with curr_path.open() as f:
        curr = json.load(f)
    with prev_path.open() as f:
        prev = json.load(f)

    diff = diff_reports(prev, curr)
    print(f"Comparing\n  current : {curr_path.name}\n  previous: {prev_path.name}\n")
    print(diff["summary"])

    failed = diff["mean_regressed"] or bool(diff["axis_regressions"])
    if failed:
        print(
            "\nFAIL: regression detected. Investigate before merging this PR.\n"
            "If this regression is intentional (e.g. you re-tuned the persona),\n"
            "delete the previous report from evals/reports/ to re-baseline AFTER\n"
            "you've reviewed the rationale change with a teammate.",
            file=sys.stderr,
        )
        return 1
    print("\nOK: no regression.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
