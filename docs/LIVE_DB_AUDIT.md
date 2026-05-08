# Live Database Audit — Snapshot

**Project**: `oodvelyzdsncsssqvmyb.supabase.co`
**Snapshot taken**: 2026-05-09 via Supabase MCP
**Purpose**: Ground truth for Phase 0+ design. Numbers will move; treat
this as a reference point, not a contract.

---

## 1. Table inventory

| Table | Rows | RLS | Notes |
|---|---|---|---|
| `companies` | 140 | OFF | 67 marked `is_target=true` |
| `company_knowledge` | 507 | OFF | 13 sections × varying coverage |
| `jobs` | 245 | OFF | 11 at score ≥ 85 |
| `applications` | 2 | OFF | both `status='evaluated'` |
| `agent_conversations` | 152 | OFF | 76 `company_agent` + 76 `rizwan_agent` turns / 35 jobs |
| `boss_audit_log` | 4 | OFF | all 2026-05-08 |
| `rizwan_profile` | 5 | OFF | LEGACY — see below |
| `story_bank` | 0 | OFF | empty — needs seeding for G3 |
| `profile_master` | 1 | ON (no policies) | canonical structured profile |
| `profile_experience` | 4 | ON (no policies) | Simpaisa CPO → Daraz → TapmadTV → Infinity/Wing Logic |
| `profile_certification` | 6 | ON (no policies) | PMP, PMI-ACP, CSPO, CSM, etc |
| `profile_education` | 3 | ON (no policies) | |
| `profile_keyword` | 310 | ON (no policies) | 11 categories |
| `profile_keyword_category` | 11 | ON (no policies) | category rollup |
| `profile_source_document` | 233 | ON (no policies) | parsed source files (CVs, JDs, notes) |
| `profile_recommendation` | 41 | ON (no policies) | AI-generated profile improvements |

Extensions installed: `vector` (0.8.0, in `public` — flagged), `pgcrypto`,
`uuid-ossp`, `pg_stat_statements`, `supabase_vault`, `plpgsql`. Not
installed (and likely not needed): `pg_cron`.

Migration history: empty — schema applied directly via SQL editor, not
via the Supabase migrations system. Phase 0+ migrations should land via
`apply_migration` for proper version tracking.

---

## 2. The two "profile" stores — important distinction

Two unrelated table groups both serve "profile" data. **Do not confuse them**:

### Canonical (use this)
- `profile_master` — 1 row, structured fields
  - `core_competencies`: `text[]` (35 items)
  - `technical_knowledge`: `text[]` (17 items)
  - `languages`: `jsonb` (3 items)
  - `ai_solutions`: `jsonb` (4 items)
- `profile_experience` — 4 rows, each with `groups: jsonb` for nested bullets
- `profile_certification` — 6 rows
- `profile_education` — 3 rows
- `profile_keyword` — 310 rows + `profile_keyword_category` for rollups

### Legacy (read-only, embedding cache)
- `rizwan_profile` — 5 rows under stale section names:
  - `summary`, `current_simpaisa`, `daraz_experience`, `pmo_experience`, `certifications_skills`
  - Has `embedding vector(1536)` column → useful for pgvector retrieval
  - **Do not treat as the canonical text source** — content is older than `profile_master`
  - Still referenced by `search_rizwan_profile()` RPC, used elsewhere in the codebase

---

## 3. Jobs distribution

### Status
| status | n |
|---|---|
| new | 176 |
| pass | 37 |
| evaluated | 32 |

(No `applied`, `expired`, etc. in current data — even though the codebase writes those.)

### Match score
| band | n | distinct companies |
|---|---|---|
| 85+ | 11 | 10 |
| 70-84 | 48 | 29 |
| 50-69 | 72 | 26 |
| 40-49 | 77 | 23 |
| <40 | 37 | 12 |

### Archetype × Legitimacy
| archetype | legitimacy | n |
|---|---|---|
| Senior PM | Proceed with Caution | 142 |
| Program Manager | Proceed with Caution | 42 |
| Senior PM | High Confidence | 19 |
| Head of Product | Proceed with Caution | 8 |
| Senior PM | Suspicious | 7 |
| Head of Product | High Confidence | 6 |
| PMO Director | Proceed with Caution | 5 |
| CPO | High Confidence | 3 |
| Group PM | Proceed with Caution | 3 |
| VP Product | Proceed with Caution | 3 |
| ... (smaller buckets) | | |

84% of all jobs are "Proceed with Caution" — the legitimacy classifier
defaults to caution when signals are mixed. Consistent with the Bayt /
aggregator-heavy source mix.

### Top jobs (≥85, deduplicated)
| ID | Company | Title | Score | Archetype | Status | Has resume |
|---|---|---|---|---|---|---|
| 1022 | Adyen | Head of Product Management, Issuing | 95 | Head of Product | new | – |
| 1023 | Adyen Careers | Head of Product, Credit | 95 | Head of Product | new | – |
| 1020 | Adyen Careers | Head of Product Operations | 95 | Head of Product | evaluated | **YES** |
| 395 | SuperApp | Head of Product (Dubai) | 90 | Head of Product | new | – |
| 96 | Adecco | Chief Product Officer (Fintech & B2B) | 90 | CPO | new | – |
| 97 | 1inch | Chief Product & Technology Officer | 85 | CPO | new | – |
| 89 | Finkraft.ai | Head of Product (Dubai) | 85 | Head of Product | new | – |
| 456 | Mastercard careers | Lead Product Manager-Technical | 85 | Senior PM | new | – |
| 356 | Bayt.com | Product Manager Fintech (Riyadh) | 85 | Head of Product | new (caution) | – |
| 265 | Saudi Arabia | Commercial Product Manager Fintech | 85 | Senior PM | new (caution) | – |
| 274 | Squadio | Senior Product Manager (ARAB) | 85 | Senior PM | new (caution) | – |

**Only 1 job (Adyen HoP Operations) has a generated resume.** This is the
single example of an end-to-end run that finished. G2 will be cold-starting
on essentially everything else.

Note: "Adyen" and "Adyen Careers" are listed as separate companies — known
canonicalisation issue. `CompanyAgent._canonicalize()` strips " Careers"
suffix at agent level but the upstream JobScout writes the unstripped name
straight to `jobs.company`.

---

## 4. Company knowledge coverage

Of 140 companies, **44 have any company_knowledge rows**. Of those:

- 44 have `overview`, `news`
- 43 have `strategy`, `competitors`
- 42 have `funding`, `tech_stack`, `challenges`, `culture`
- **33 have all 5 recruitment-intel sections** (`recruitment_process`, `resume_dos_donts`, `ats_signals`, `interview_format`, `hiring_signals`)

The 33 fully-complete companies (the persona-seed pool):

```
Adyen · Adyen Careers · Airwallex · American Express · Careem Pay ·
Checkout.com · dLocal · Foodics · Lean Technologies · Marqeta ·
Mashreq Neo · Mastercard · Merchant Acquiring … · Network International ·
Nium · Payoneer · PayPal · Plaid · Rapyd · Remitly · Revolut ·
Square (Block) · Standard Chartered · STC Pay · Stripe · SuperApp ·
Tabby · Tamara · TerraPay · Thunes · Visa · Wio Bank · Wise
```

### Quality caveat
Sampled `Mastercard` recruitment intel:
- `interview_format`: starts with "Unknown — insufficient data" (use static fallback)
- `recruitment_process`: vague ("Online application submission")
- `ats_signals`: useful (concrete keyword list)
- `resume_dos_donts`: useful (concrete recommendations)
- `hiring_signals`: useful

The persona seed script (`db/seed_company_personas.sql`) grades each
persona based on count of `Unknown — insufficient data` sections and
tags it in `metadata.persona_quality` (`high`/`medium`/`low`).

---

## 5. Targets — 67 companies marked `is_target=true`

By category (priority-sorted):

| Category | Count | Tier |
|---|---|---|
| Cross-Border / Remittance | 12 | mostly high |
| Payment Processor | 14 | mostly high/medium |
| MENA Fintech | 9 | all high |
| BaaS / Embedded Finance | 7 | mixed |
| NeoBank | 8 | high/medium |
| Card Networks | 6 | high (Mastercard, Visa, Amex) |
| Big Bank | 6 | medium |
| Core Banking / Infra | 4 | medium/low |

**MENA-heavy, payments-heavy.** Matches the candidate's positioning
(Dubai-based, payments / cross-border specialist).

---

## 6. Agent conversation history

152 turns total — 76 from `company_agent`, 76 from `rizwan_agent`
(perfectly balanced — exactly one rizwan response per company prompt).
Spread across 35 distinct jobs.

**Critical observation**: even top targets like Mastercard have **0**
agent_conversations rows. This means G2's meta-critic will frequently
operate with no historical context. The cold-start path must be the
default-supported path, not a corner case.

---

## 7. Applications & outcomes

| | Value |
|---|---|
| Total applications | 2 |
| `status='evaluated'` | 2 |
| `status='applied'` | 0 |
| Recruiter responses | unknown — no field |
| Interviews received | unknown — no `interview_outcomes` table yet (added in Phase 0 migration) |
| Offers | 0 |

**Implication**: zero outcome ground truth to feed the meta-critic's
`success_patterns` / `failure_patterns`. The learning loop only starts
once Phase 0 migration is applied AND the dashboard adds a "log outcome"
form AND the user manually logs at least 5–10 results.

This isn't a bug — it's the realistic starting state for a system that
hasn't been used to actually apply yet.

---

## 8. Boss agent runs

4 audit log rows, all from 2026-05-08:

| run_date | jobs_found_today | jobs_scored_high | applications_sent | digest_sent |
|---|---|---|---|---|
| 2026-05-08 | 105 | 105 | 0 | false |
| 2026-05-08 | 105 | 97 | 0 | false |
| 2026-05-08 | 110 | 93 | 0 | false |
| 2026-05-08 | 149 | 112 | 0 | false |

`digest_sent=false` across all runs — SendGrid integration likely not
wired or no key configured. Doesn't block any Phase 0+ work.

---

## 9. Schema drift vs `db/schema.sql` in the repo

Live schema has these additions beyond what's in the repo's `schema.sql`:

| Table | Added columns |
|---|---|
| `companies` | `is_target`, `priority` (text), `category`, `notes`, `target_added_at`, `last_scanned_at` |
| `jobs` | `archetype`, `legitimacy_tier`, `legitimacy_signals`, `resume_generated_at`, `evaluation_blocks` |
| `applications` | `sort_order`, `resume_path`, `email_path`, `interview_path`, `readiness_score`, `readiness_assessment`, `company_id` |

These were added incrementally via separate SQL files (`profile_schema.sql`, `targets_schema.sql`, `workflow_v2_schema.sql`) that aren't all in repo HEAD.

**Implication**: `db/multi_llm_schema.sql` (new) is type-safe against the
live schema:
- `resume_builds.job_id INTEGER` ✓ (live `jobs.id` is `int4`)
- `resume_builds.application_id UUID` ✓ (live `applications.id` is `uuid`)
- `resume_outcomes.job_id INTEGER` ✓
- `interview_outcomes.application_id UUID` ✓

Safe to apply via `apply_migration`.
