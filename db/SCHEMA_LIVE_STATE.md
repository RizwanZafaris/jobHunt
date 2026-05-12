# Live database — canonical schema snapshot

**Date:** 2026-05-12
**Project:** Supabase (production)
**Source:** `information_schema` + `pg_constraint` via Supabase MCP, taken on 2026-05-12 from the `chore/migration-drift-discovery` branch.

This document is **the source of truth** for the FK targets and PK types
that new migrations must satisfy. The files under `db/migrations/` are
the historical record of *intent*; this file is the record of *reality*.

> If you are authoring a new migration, read this first. Do not infer
> shape from migration 001's narrative — it pre-dates several
> applied-direct-via-MCP migrations that are not in source. See §4 below.

---

## 1. Why this file exists

Three bugs in one session (BUG-037 / BUG-041 / BUG-042) all had the
same shape: a new migration was authored against an *imagined* schema
("`profiles(id)`" / "`applications.id BIGINT`") that has **never been
real**. The original `db/schema.sql` bootstrap already declared
`applications.id UUID` with `gen_random_uuid()`, and migration 001
already targets `users(id)` — there is no missing rewrite migration.

The drift is purely between **author's mental model** and **live**.
This file fixes that asymmetry.

---

## 2. The two rules

1. **Every `user_id` FK targets `users(id)`** (UUID). There is no
   `profiles` table in `public`. There never was.
2. **Every `application_id` FK targets `applications(id)` (UUID, default
   `gen_random_uuid()`)**. There is no BIGINT `applications.id` and
   there never was — see `db/schema.sql` lines 81-99 for the original
   declaration and migration 001 lines 31-72 for the tenancy overlay.

---

## 3. Table inventory (42 base tables, 2026-05-12)

| Table | PK type | RLS | Notes |
|---|---|---|---|
| `agent_call_log` | `id bigint` | on | LLM cost telemetry |
| `agent_conversations` | `id uuid` | on | |
| `application_answers` | `id uuid` | on | G7 Tier-3 application form fill |
| `applications` | `id uuid` | on | **NOT bigint** — UUID since day-1 |
| `ats_test_results` | `id uuid` | on | |
| `boss_audit_log` | `id uuid` | **off** | admin-only operational log; intentional |
| `comp_cache` | `id uuid` | on | G5 comp_research cache |
| `companies` | `id uuid` | on | |
| `company_knowledge` | `id uuid` | on | pgvector |
| `company_personas` | `id uuid` | on | |
| `edges` | `id uuid` | on | referral graph |
| `employments` | `id uuid` | on | |
| `follow_up_cadence` | `id uuid` | on | G6 |
| `interview_outcomes` | `id uuid` | on | |
| `interview_prep` | `id uuid` | on | G3 |
| `interview_tutor_messages` | `id uuid` | on | G3 tier-2 |
| `jobs` | `id integer` | on | SERIAL — only non-UUID FK target |
| `jobs_runs` | `id uuid` | on | durable queue |
| `knowledge_outcome_credits` | `id uuid` | on | |
| `linkedin_drafts` | `id uuid` | on | |
| `linkedin_posting_schedule` | `id uuid` | on | |
| `linkedin_voice_profile` | `id uuid` | on | |
| `offer_evaluations` | `id uuid` | on | **G8 — applied direct via MCP 2026-05-12, no source file before this branch. Backfilled as `2026_05_12_026_offer_evaluations.sql`.** |
| `orgs` | `id uuid` | on | |
| `people` | `id uuid` | on | referral graph |
| `persona_versions` | `id uuid` | on | |
| `profile_certification` | `id integer` | on | legacy SERIAL |
| `profile_education` | `id integer` | on | legacy SERIAL |
| `profile_experience` | `id integer` | on | legacy SERIAL |
| `profile_keyword` | `id integer` | on | legacy SERIAL |
| `profile_keyword_category` | `category text` | on | text PK — special case |
| `profile_master` | `id integer` | on | legacy SERIAL |
| `profile_recommendation` | `id integer` | on | legacy SERIAL |
| `profile_source_document` | `id integer` | on | legacy SERIAL |
| `proof_points` | `id uuid` | on | G10 |
| `resume_builds` | `id uuid` | on | |
| `resume_outcomes` | `id uuid` | on | |
| `rizwan_profile` | `id uuid` | on | LEGACY single-user profile (still queried by some flows) |
| `story_bank` | `id uuid` | on | G9 STAR+R |
| `target_company_employees` | `id uuid` | on | |
| `users` | `id uuid` | on | application user table (`auth.users` is separate) |
| `writing_samples` | `id uuid` | on | G11 voice corpus |

Notable shapes:

- `jobs.id` is **`integer` (SERIAL)** — every `job_id` FK is `INTEGER REFERENCES jobs(id)`.
- `profile_*` legacy tables use `integer` PKs — fine, leave them alone.
- `profile_keyword_category` has `category text` PK (not `id`).
- `applications.id`, despite the BUG-042 fiction, has **always** been UUID.

---

## 4. The 2026-05-12 backfill: migration 026

`supabase_migrations.schema_migrations` row `20260512130838`
(`offer_evaluations_g8_026`) was applied directly via Supabase MCP
during the Tier-4 G8 ship without ever landing in `db/migrations/`.
The DDL was reconstructed from `pg_dump`-equivalent
(`information_schema` + the MCP-stored statement body) and committed as

```
db/migrations/2026_05_12_026_offer_evaluations.sql
```

The file carries an `IF NOT EXISTS` guard, so re-running it against the
live DB is a no-op. On a fresh database it now creates the table at the
right point in the dependency order (after `applications` + `users`).

---

## 5. Drift audit — migrations 013–026 (2026-05-12 source vs live)

| File | Matches live? | Issue |
|---|---|---|
| `2026_05_12_012_linkedin_drafts_source_company_name.sql` | yes | ALTER only; no FK issue |
| `2026_05_12_013_comp_cache.sql` | yes | `user_id` → `users(id)` (fixed in #105) |
| `2026_05_12_013_companies_is_phantom.sql` | yes | ALTER only |
| `2026_05_12_014_applications_applied_date_check.sql` | yes | CHECK only |
| `2026_05_12_015_story_bank.sql` | yes | `user_id` → `users(id)` |
| `2026_05_12_016_follow_up_cadence.sql` | yes | `user_id` → `users(id)`, `application_id` UUID → `applications(id)` (fixed in #105) |
| `2026_05_12_017_story_bank_not_null.sql` | yes | ALTER only |
| `2026_05_12_018_search_story_bank_v2.sql` | yes | function only |
| `2026_05_12_019_jobs_fit_score_breakdown.sql` | yes | ALTER only |
| `2026_05_12_020_jobs_legitimacy_v1.sql` | yes | ALTER only |
| `2026_05_12_021_interview_prep_g3_tier2_columns.sql` | yes | ALTER only |
| `2026_05_12_022_application_answers.sql` | yes | `user_id` → `users(id)`, `application_id` UUID → `applications(id)` (fixed in #105); **header comment about "earlier BIGINT/profiles types" is misleading — corrected on this branch** |
| `2026_05_12_023_profiles_voice_calibration.sql` | yes | adds `writing_samples`; FK `user_id` → `users(id)` |
| `2026_05_12_024_proof_points.sql` | yes | `user_id` → `users(id)` |
| `2026_05_12_025_funnel_view_filter_closed_and_phantom.sql` | yes | view only |
| `2026_05_12_026_offer_evaluations.sql` | yes | **backfilled this branch** — was applied direct via MCP, never in source |

All 15 session migrations are now in source-vs-live agreement.

---

## 6. The two `013_` files

There are intentionally two `2026_05_12_013_*.sql` files
(`comp_cache` and `companies_is_phantom`). They were authored
independently by parallel agents and applied in different orders; both
are idempotent. `supabase_migrations.schema_migrations` shows
`2026_05_12_013_companies_is_phantom` was applied with version
`20260512020552` while `comp_cache` was applied with a sibling version
under a different name. **Not a bug** — collision-tolerant by design.

---

## 7. How to keep this file fresh

Any agent applying a migration via `mcp__supabase__apply_migration`
**must** also commit a matching file under `db/migrations/` in the same
PR. If the migration is reconstructive (was applied direct and a source
file is being backfilled later), guard it with `IF NOT EXISTS` /
`ADD COLUMN IF NOT EXISTS` so apply-on-fresh = no-op-on-live.

When in doubt, regenerate the §3 table by running:

```sql
SELECT t.table_name,
       (SELECT c.data_type FROM information_schema.columns c
        WHERE c.table_schema='public' AND c.table_name=t.table_name
          AND c.column_name = (
            SELECT kcu.column_name FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
            WHERE tc.constraint_type='PRIMARY KEY'
              AND tc.table_schema='public' AND tc.table_name=t.table_name
            LIMIT 1
          )
       ) AS pk_type
FROM information_schema.tables t
WHERE t.table_schema='public' AND t.table_type='BASE TABLE'
ORDER BY t.table_name;
```

against the live project.
