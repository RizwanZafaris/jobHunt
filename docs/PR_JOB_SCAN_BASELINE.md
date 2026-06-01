# PR Job Scan — Baseline Audit

**Date:** 2026-05-17
**Scanner:** [tools/pr_job_scan](../tools/pr_job_scan/) v0.1.0
**Commit:** `feat/pr-job-scan` (this PR)

This document is the one-time baseline audit produced by `pr-job-scan
scan-tree`. It exists so we can tell, on every future PR, which findings
were already there vs. which were introduced by the change.

## Summary

| Severity | Count |
|----------|-------|
| HIGH     | 41    |
| MED      | 139   |
| LOW      | 89    |
| **Total**| **269** |

The big counts (QUA004 + QUA006) reflect well-known gaps from the
existing audit (`jobHunt_Gap_Report_2026_05_14.md` GAP-005). They're
the floor, not the noise.

## Fixed in this PR

Both surfaced by `scan-tree` on `main`:

| ID       | File                                                        | Fix |
|----------|-------------------------------------------------------------|-----|
| QUA005   | `agents/resume_edit_assistant.py:70`                        | `"claude-opus-4-7"` → `"claude-opus-4-5-20251101"` (404 → working model) |
| QUA005   | `agents/resume_edit_assistant.py:83`                        | same                                                                     |
| QUA005   | `agents/llm_router.py:45`                                   | Removed dead `claude-opus-4-7` pricing entry                            |
| QUA005   | `dashboard/src/components/network/IntroDraftModal.tsx:210`  | UI fallback string updated                                              |

PR #121 fixed this same model id in `g4_linkedin_graph.py` last week but
missed the resume editor. **Quick-tweak and rebuild-section endpoints were
silently 404'ing every request** until this PR.

## Open findings on main (NOT touched in this PR)

These are real and worth follow-up tickets, but each requires more
context than a tooling PR should take on.

### HIGH (1)
- `dashboard/src/components/linkedin/LinkedInClient.tsx:10` — QUA001
  comment still references the MOCK_* fallback pattern. The code below
  it has been gutted but the comment is stale. Trivial doc fix.

### MED (5)
- `api/server.py:43` — REL001: `@app.on_event("startup")` still live.
  The B26 lifespan refactor in `b17.diff` hasn't reached main.
- `config/settings.py:214` — SEC003: `secret_key: str = "change-me-in-production"`.
  Matches **GAP-003** from the gap report. Should be `None` default + raise
  at startup if unset.
- `api/queue.py:299` — REL005 false-positive (in docstring); will be
  silenced in scanner v0.1.1.
- `api/orphan_reaper.py:69` — REL002 false-positive (in B6 comment);
  silenced in latest rule update.

### Recurring patterns (counts > 10 → systemic, not per-PR)
- **SEC001** (40) — `logger.exception` in HTTP/worker paths.
  `api/actions.py` and `api/orphan_reaper.py` are the worst offenders.
  Tracked in **GAP-005** / **B17 follow-up**.
- **REL003** (22) — graphs still on raw `get_router().ask`.
  Files: `agents/g6_nodes.py`, `agents/g7_nodes.py`, `agents/g8/*`,
  `agents/g9_nodes.py`, `agents/g11_nodes.py`, `agents/proof_point_extractor.py`,
  `agents/scoring_agent.py`. Tracked in **B27**.
- **QUA002** (10) — TODO/FIXME without owner+date. **B25 follow-up**.
- **QUA004** (117) — endpoints without colocated tests. Matches the
  6% endpoint coverage measured in **GAP-005**. Won't move without a
  dedicated test-coverage sprint.
- **QUA006** (76) — Pydantic bodies without `extra="forbid"`. **B9
  follow-up** — applied to /network only; the rest of the surface
  still silently drops unknown keys.

## How the workflow works

`.github/workflows/pr-job-scan.yml` runs on every PR against `main`.

- It scans **only the PR diff** (`scan-repo --base origin/main`), not
  the whole tree — so this baseline doesn't trigger CI noise.
- It **fails the build on any HIGH** finding introduced by the PR.
- It uploads **SARIF** to GitHub code-scanning so findings show up in
  the PR's "Files changed" tab with inline annotations.

To run locally:

```bash
cd tools/pr_job_scan
python -m pytest tests/ -q                    # self-test (32 tests)
python -m pr_job_scan scan-repo .. --base main --no-color
python -m pr_job_scan scan-tree ..            # whole-repo audit
python -m pr_job_scan list-rules              # what we check
```
