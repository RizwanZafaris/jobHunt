"""argparse-driven CLI. Avoids click/typer to keep deps at zero."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .finding import Severity
from .report import render_json, render_sarif, render_text
from .sources import scan_diff_file, scan_pr, scan_repo, scan_tree


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--select",
        help="Comma-separated rule IDs to run (e.g. SEC001,REL002). Default: all.",
    )
    p.add_argument(
        "--format",
        choices=["text", "json", "sarif"],
        default="text",
        help="Output format (default: text).",
    )
    p.add_argument(
        "--fail-on",
        choices=["low", "med", "high", "none"],
        default="high",
        help="Exit 1 when a finding at this severity or higher is reported.",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colors in text output.",
    )


def _exit_code(result, threshold: str) -> int:
    if threshold == "none" or not result.findings:
        return 0
    cutoff = Severity.parse(threshold)
    return 1 if result.max_severity() and result.max_severity() >= cutoff else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pr-job-scan",
        description="Static-analysis scanner for the jobHunt bug classes.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_diff = sub.add_parser("scan-diff", help="Scan a unified .diff/.patch file.")
    p_diff.add_argument("path", type=Path)
    _add_common_flags(p_diff)

    p_repo = sub.add_parser("scan-repo", help="Scan local repo (git diff base...HEAD).")
    p_repo.add_argument("repo", type=Path)
    p_repo.add_argument("--base", default="main", help="Base branch (default: main).")
    _add_common_flags(p_repo)

    p_pr = sub.add_parser("scan-pr", help="Scan a GitHub PR via `gh pr diff`.")
    p_pr.add_argument("number", type=int)
    p_pr.add_argument("--repo", help="owner/name (optional if cwd is the repo).")
    _add_common_flags(p_pr)

    p_tree = sub.add_parser(
        "scan-tree",
        help="Audit a whole repo as a one-shot (every line treated as new).",
    )
    p_tree.add_argument("root", type=Path)
    _add_common_flags(p_tree)

    p_list = sub.add_parser("list-rules", help="List all rule IDs.")

    args = parser.parse_args(argv)

    select = [s.strip() for s in args.select.split(",")] if getattr(args, "select", None) else None

    try:
        if args.cmd == "list-rules":
            from .rules import ALL_RULES
            for rid, fn in ALL_RULES:
                doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
                print(f"  {rid}  {doc}")
            return 0
        elif args.cmd == "scan-diff":
            result = scan_diff_file(args.path, select=select)
        elif args.cmd == "scan-repo":
            result = scan_repo(args.repo, base=args.base, select=select)
        elif args.cmd == "scan-pr":
            result = scan_pr(args.number, repo=args.repo, select=select)
        elif args.cmd == "scan-tree":
            result = scan_tree(args.root, select=select)
        else:
            parser.error(f"unknown command {args.cmd}")
            return 2
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(render_json(result))
    elif args.format == "sarif":
        print(render_sarif(result))
    else:
        sys.stdout.write(render_text(result, color=not args.no_color))

    return _exit_code(result, args.fail_on)


if __name__ == "__main__":
    raise SystemExit(main())
