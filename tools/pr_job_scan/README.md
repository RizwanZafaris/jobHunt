# PR Job Scan

Lightweight, dependency-free static-analysis scanner that catches the recurring
bug classes seen in the jobHunt codebase before they reach `main`. Runs against:

- A unified `.diff` / `.patch` file
- A local repository (any branch — defaults to `git diff main...HEAD`)
- A GitHub PR number (via `gh pr diff`)

## What it catches

Each rule maps to a real incident from the jobHunt bug log
(see `Bug-by-Bug Summary` in the parent `README.md`).

| Rule ID  | Pattern                                                         | Severity | Origin   |
|----------|-----------------------------------------------------------------|----------|----------|
| `SEC001` | `logger.exception(` in HTTP/worker entry points (traceback leak)| HIGH     | B17      |
| `SEC002` | `allow_origins=["*"]` together with `allow_credentials=True`    | HIGH     | B3       |
| `SEC003` | Hard-coded fallback like `"change-me-in-production"`            | MED      | settings |
| `SEC004` | `verify=False` in `requests` / `httpx`                          | HIGH     | generic  |
| `REL001` | Deprecated `@app.on_event("startup"\|"shutdown")`               | MED      | B26      |
| `REL002` | Supabase write to non-existent column (`error_message` on       |          |          |
|          | `resume_builds` — should be `error`)                            | HIGH     | B6       |
| `REL003` | `get_router().ask(` not wrapped by `get_hardened_router()`      | MED      | B27/B-OR |
| `REL004` | `APIRouter(prefix="/workspace/...")` collides with              |          |          |
|          | `/workspace/{job_id}` integer route                             | HIGH     | B2       |
| `REL005` | Background task that can be killed by SIGTERM without sweep     | MED      | B7       |
| `QUA001` | Mock-data fallback in `catch` block of a dashboard page         | HIGH     | B15/B10  |
| `QUA002` | Stale TODO older than 90 days (heuristic via `git blame`)       | LOW      | B25      |
| `QUA003` | Duplicate files like `foo 2.ts`, `foo 3.tsx` (Finder copies)    | LOW      | B28      |
| `QUA004` | New API endpoint added but no test in `tests/` references it    | MED      | gap-005  |
| `QUA005` | `model="claude-opus-4-7"` or other stale Claude model ids       | MED      | PR #121  |
| `QUA006` | Pydantic `BaseModel` body without `extra="forbid"`              | LOW      | B9       |

Severity ladder: `HIGH` blocks merge by default, `MED` requires acknowledge,
`LOW` is informational.

## Usage

```bash
# scan a diff file
python -m pr_job_scan scan-diff path/to/b17.diff

# scan a local repo against main
python -m pr_job_scan scan-repo /path/to/jobHunt --base main

# scan a GitHub PR (requires `gh` CLI)
python -m pr_job_scan scan-pr 126 --repo RizwanZafaris/jobHunt

# JSON output for CI
python -m pr_job_scan scan-diff b17.diff --json > findings.json

# fail the build on HIGH findings
python -m pr_job_scan scan-diff b17.diff --fail-on high
```

Exit codes: `0` = clean, `1` = findings >= `--fail-on`, `2` = scanner error.

## CI integration

Drop into `.github/workflows/pr-scan.yml`:

```yaml
name: PR Scan
on: pull_request
jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -e ./tools/pr_job_scan
      - run: python -m pr_job_scan scan-repo . --base origin/main --fail-on high
```

## Layout

```
pr_job_scan/
├── __init__.py
├── __main__.py        # CLI entrypoint
├── cli.py             # arg parsing + dispatch
├── diff.py            # unified-diff parser (no `unidiff` dep)
├── finding.py         # Finding dataclass + Severity enum
├── rules.py           # all 14 rule implementations
├── report.py          # text + JSON + SARIF renderers
└── sources.py         # diff/repo/PR loaders
tests/
└── test_rules.py
examples/
└── sample.diff        # toy diff exercising every rule
```

## License
MIT
