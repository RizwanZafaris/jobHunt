# jobHunt Evaluation Harness

> **Hard rule:** Never ship a prompt change to `agents/`, `resume_agents/`, or
> `db/client.py` without a green golden run.

The harness lives entirely under `evals/` and reuses two existing modules:

- `agents/llm_router.get_router()` for all LLM calls (the judge).
- `db/client.search_company_knowledge()` for RAG retrieval.

Do **not** instantiate provider clients in this folder — that defeats the
cost telemetry and key-management the router provides.

## What's here

| File | Purpose |
|---|---|
| `judge.py` | LLM-as-judge. Scores a resume on 5 axes via Claude Opus 4.5. |
| `run_golden.py` | Runs all golden cases through the judge. Optionally invokes G2 first. |
| `rag_eval.py` | Computes recall@5/10 and MRR for `search_company_knowledge` against labelled queries. |
| `regression_check.py` | CI gate. Compares the two most recent golden reports and fails on regression. |
| `rag_queries.json` | 5 placeholder RAG queries — labels are empty until you populate them. |
| `golden/case_*.json` | One JSON per golden case (JD + persona + expected scores). |
| `golden/case_*_golden_resume.md` | Hand-curated reference resume per case. **Currently placeholders.** |
| `reports/` | All eval outputs land here. Checked in via `.gitkeep`. |

## How to run each eval

### 1. Golden eval (judge an existing resume)

```bash
# Score one already-generated resume against case 001
python -m evals.run_golden --case 001 --resume path/to/resume.md

# Score all three cases (one shared resume)
python -m evals.run_golden --case all --resume path/to/resume.md

# One resume per case (in case-id order)
python -m evals.run_golden --case all \
  --resumes path/case1.md,path/case2.md,path/case3.md
```

Output: `evals/reports/{ISO_timestamp}.json` plus a stdout summary table
(rich if installed, else plain).

### 2. Golden eval (invoke G2 graph end-to-end)

```bash
# Invoke G2 against three job_ids, then judge each
python -m evals.run_golden --case all --invoke-g2 --job-ids 1234,1235,1236
```

This costs real money — the G2 graph runs ~5 LLM calls per build. Do not
run on every PR. Reserve for nightly / pre-release builds.

### 3. RAG eval

```bash
python -m evals.rag_eval --queries evals/rag_queries.json --k 10
```

Output: `evals/reports/rag_{ISO_timestamp}.json`.

The metrics are meaningless until the queries are labelled (see below).

### 4. Regression check (CI gate)

```bash
python -m evals.regression_check --reports-dir evals/reports/
```

- Exits 0 if there's < 2 golden reports (noop).
- Exits 0 if no regression.
- Exits 1 if mean-of-means dropped > 0.3 OR any axis dropped > 0.5.
- Exits 2 on bad inputs.

## How to add a golden case

1. Pick the next free id (e.g. `case_004`).
2. Create `evals/golden/case_004_<slug>.json` matching the schema:
   ```json
   {
     "id": "case_004_<slug>",
     "company": "<canonical company name>",
     "role": "<role title>",
     "jd": {"title": "...", "company": "...", "description": "...", "location": "...", "url": "..."},
     "persona": {
       "voice": "...",
       "terminology": ["..."],
       "seniority": "...",
       "ats_keyword_bank": ["..."]
     },
     "profile_ref": "cv.md",
     "expected_axes": {
       "ats_keyword_coverage": 8,
       "evidence_specificity": 8,
       "persona_fit": 8,
       "hallucination_check": 10,
       "length_discipline": 8
     },
     "notes": "..."
   }
   ```
3. Create `evals/golden/case_004_golden_resume.md` and **hand-curate** the
   ideal resume for this JD. Do NOT generate this with G2 — it's the
   reference, the calibration anchor for what 8-10 looks like.
4. Run `python -m evals.run_golden --case 004 --resume <golden>` and
   verify the score matches `expected_axes` (within ±1 per axis). Adjust
   `expected_axes` if the judge consistently scores differently.

## How to label rag_queries.json

The current `rag_queries.json` has empty `relevant_doc_ids` arrays. Until
those are filled, recall@k and MRR are zero and the eval is meaningless.

To label:

1. Run `python -m evals.rag_eval` once. The report will list the top-10
   retrieved doc ids per query (under `results[i].retrieved`).
2. For each query, manually inspect the retrieved snippets and decide
   which doc ids should be considered ground-truth relevant. Aim for
   3-7 relevant docs per query.
3. Paste those ids into the corresponding `relevant_doc_ids` array in
   `rag_queries.json`.
4. Re-run. Recall@5 should now be > 0.

If your `company_knowledge` rows lack stable string ids, the RAG eval
falls back to `{company_name}::{section}` as a composite id — that's
fine to label against, but be consistent across reruns.

## How to interpret the report

### Golden report (`evals/reports/{ts}.json`)

- `overall.mean_of_means` — average score across all cases (0-10).
  Target: ≥ 8.0 once goldens are populated.
- `overall.pass_rate` — fraction of cases where pass=True. Target: 1.0.
- Per-case `axes.<axis>.score` + `rationale` — the judge's reasoning.
  Read the rationale for any axis < 7 to see what to fix.
- `hallucinations_found` — verbatim claims the judge couldn't find in
  the profile. **Any non-empty array is a fail.** Investigate the
  G2 trajectory for that case.

### RAG report (`evals/reports/rag_{ts}.json`)

- `overall.mean_recall@5` — across labelled queries. Target: ≥ 0.7.
- `overall.MRR` — first relevant doc rank, averaged. Target: ≥ 0.6
  (i.e. relevant doc usually within top-2).
- Per-query `retrieved` — the actual returned docs in rank order.

## How to wire to CI

The workflow at `.github/workflows/eval-regression.yml` runs the
regression check on every PR that touches `agents/`, `resume_agents/`,
or `db/client.py`.

It is a **noop until a baseline report exists**. To produce the first
baseline:

```bash
# 1. Generate or hand-edit the three golden resumes (one per case).
# 2. Run the eval and commit the resulting report:
python -m evals.run_golden --case all --resumes a.md,b.md,c.md
git add evals/reports/<the-report>.json
git commit -m "evals: baseline golden report"
git push
```

Once the baseline is in `main`, the next PR's golden run will be
compared against it and the gate becomes meaningful.

## Why Opus 4.5 for the judge

Three audit teams independently flagged that switching judge models is
a hidden source of eval drift. Opus 4.5 is fixed in `evals/judge.py`
intentionally. To change it, update `JUDGE_MODEL` and re-baseline ALL
existing reports — never mix scores from two judges in the same
regression series.

## Hard rule

> **Never ship a prompt change without a green golden run.**

If you're tempted to skip the eval ("it's just a small wording tweak"):
the audit agents that demanded this harness identified prompt-only
changes as the highest-frequency source of silent regressions in
production. The cost of one extra `run_golden` call is < $1. The cost
of a bad resume reaching a real recruiter is your career.
