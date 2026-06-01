# E2E Flywheel Verification

## Purpose

Verify the outcome --> persona credit loop works end-to-end. The flywheel is the
system's core value proposition: every interview outcome (pass/fail) credits the
company knowledge rows cited in the resume build, and those credits feed back
into persona evolution.

Today every outcome-driven table has 0 rows. This script proves the plumbing is
working.

## Prerequisites

1. A Supabase **branch** (do NOT run against production).
2. At least one converged `resume_build` row in that branch.
3. The service role key for the branch.

## Setup

```bash
# 1. Ensure you're in the project root
cd jobHunt/

# 2. Install dependencies if needed
# pip install -r requirements.txt

# 3. Verify the branch has converged builds
python -c "
import os
os.environ['SUPABASE_URL'] = '<branch_url>'
os.environ['SUPABASE_SERVICE_ROLE_KEY'] = '<branch_key>'
from db.client import get_supabase
db = get_supabase()
rows = db.table('resume_builds').select('id, company_name').eq('status','converged').limit(3).execute().data
print(f'Found {len(rows)} converged builds')
for r in rows:
    print(f'  {r[\"id\"]} -> {r[\"company_name\"]}')
"
```

## Run

```bash
python scripts/seed_e2e_flywheel.py --url <branch_url> --key <branch_key>
```

### Dry-run mode (no writes)

```bash
python scripts/seed_e2e_flywheel.py --url <branch_url> --key <branch_key> --dry-run
```

### Verbose mode

```bash
python scripts/seed_e2e_flywheel.py --url <branch_url> --key <branch_key> --verbose
```

## Expected Output

```json
{
  "passed": true,
  "build_id": "<uuid>",
  "app_id": "<uuid>",
  "outcome_id": "<uuid>",
  "credited_knowledge_count": 3,
  "delta_per_row": 0.05,
  "source": "transcript_citations",
  "errors": []
}
```

A successful run means:

1. `credit_outcome()` found the outcome event.
2. It resolved the `resume_build` via the `application_id` chain.
3. It recovered cited knowledge rows (from transcript citations or fallback).
4. It inserted one `knowledge_outcome_credits` row per cited knowledge.
5. It recomputed `company_knowledge.outcome_score` for each cited row.

If `passed` is `false`, check the `errors` array for the failure reason:

| Error | Meaning |
|---|---|
| `No converged resume_builds found` | Branch has no converged builds to test with. |
| `credit_outcome error: outcome_not_found` | The outcome row was not found (rare — we just inserted it). |
| `credit_outcome error: no_resume_build_for_outcome` | The outcome has no linked `resume_build_id` and no `application_id` chain. |
| `credit_outcome error: no_knowledge_to_credit` | The `resume_build` has no transcript citations and no company knowledge fallback rows. |
| `No knowledge_outcome_credits rows created` | The credit rows were not inserted (check `_write_credit_row` logs). |
| `Knowledge <id>: outcome_credit_count is still 0` | The score recompute did not fire (check `_update_company_knowledge_score` logs). |

## Rollback

The script creates exactly one `interview_outcomes` row. To clean up:

```sql
-- Delete the synthetic outcome (cascades to knowledge_outcome_credits)
DELETE FROM interview_outcomes
WHERE notes = 'e2e flywheel verification seed';

-- Or, if you want to be more precise:
DELETE FROM knowledge_outcome_credits
WHERE reason LIKE '%interview_round_1_pass%';

-- Reset company_knowledge scores if needed
UPDATE company_knowledge
SET outcome_score = NULL, outcome_credit_count = 0
WHERE outcome_credit_count = 1
  AND id IN (
      SELECT knowledge_id FROM knowledge_outcome_credits
      WHERE reason LIKE '%interview_round_1_pass%'
  );
```

## CI Test

See `tests/test_outcome_flywheel.py` for the automated unit-test version that
mocks all DB calls and exercises the four key scenarios:

- Interview pass --> +0.05 delta
- Interview fail --> -0.02 delta
- Missing resume_build --> error `no_resume_build_for_outcome`
- No knowledge citations --> error `no_knowledge_to_credit`

Run the unit tests with:

```bash
pytest tests/test_outcome_flywheel.py -v
```

## Architecture Reminder

```
interview_outcomes row (passed=True)
         |
         v
credit_outcome(outcome_id, kind="interview")
         |
         +---> _load_interview_outcome()
         +---> resolve resume_build via application_id
         +---> _knowledge_ids_for_resume_build()
         |           +---> transcript cite:knowledge_id=<uuid> tokens
         |           +---> fallback: top-5 knowledge rows for company
         +---> _compute_interview_delta() = +0.05
         +---> _write_credit_row() per knowledge_id
         +---> _update_company_knowledge_score() per knowledge_id
         |
         v
knowledge_outcome_credits rows (one per cited knowledge)
company_knowledge.outcome_score updated
```

The constants governing credit deltas live in `agents/outcome_to_persona.py`:

| Event | Delta |
|---|---|
| Interview round passed | +0.05 |
| Interview round failed | -0.02 |
| Resume recruiter responded | +0.04 |
| Resume rejected | -0.01 |
| Offer received | +0.10 |
