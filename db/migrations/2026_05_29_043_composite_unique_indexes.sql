-- 2026_05_29_043_composite_unique_indexes.sql
-- Multi-tenancy DB-1 (step 1 of 2) — composite-key tenancy.
-- CTO scalability audit, 2026-05-29. See docs/SCALABILITY.md §3 P0
-- ("Global UNIQUE constraints break tenancy") and
-- docs/SCALABILITY_BUILD_PLAN.md (PR DB-1).
--
-- THE PROBLEM
--   Five tables carry GLOBAL UNIQUE constraints that silently assume a
--   single user. Once a 2nd tenant exists, tenant B upserting the same
--   company / job url / knowledge section / persona / profile section
--   OVERWRITES tenant A's row (cross-tenant data corruption), because the
--   matching `on_conflict` upserts target the bare column, not (user_id, …):
--       companies.name                            UNIQUE (name)
--       jobs.url                                   UNIQUE (url)
--       company_knowledge (company_name, section) UNIQUE (company_name, section)
--       company_personas.company_name             UNIQUE (company_name)
--       rizwan_profile.section                    UNIQUE (section)
--
-- THE FIX (two migrations + a client-code change between them)
--   Make each uniqueness composite with user_id, and point every upsert's
--   on_conflict at the composite key.
--
-- ┌─ APPLY ORDER (do NOT reorder — this is the safe cutover) ──────────────┐
-- │  1. Apply THIS migration (043) — builds the 5 composite UNIQUE indexes │
-- │     ALONGSIDE the still-present old global UNIQUEs. Both arbiters are   │
-- │     valid during the cutover window, so the running (old) client code  │
-- │     keeps working unchanged.                                           │
-- │  2. Deploy the DB-1 client code (the on_conflict edits + user_id-on-   │
-- │     write fixes) — it now conflicts on (user_id, …).                   │
-- │  3. Apply 2026_05_29_044_drop_global_unique.sql — drops the 5 old      │
-- │     global UNIQUEs. ONLY after step 2 is live in prod.                 │
-- └───────────────────────────────────────────────────────────────────────┘
--
-- WHY 043 IS SAFE TO APPLY WHILE THE OLD UNIQUES STILL EXIST
--   At today's single-user scale every row already shares the seed user_id,
--   and the existing global UNIQUE already guarantees the bare column is
--   unique. So (user_id, col) is trivially unique too — the composite index
--   build finds zero duplicates and cannot fail on live data. The two index
--   sets simply coexist until 044 removes the old one.
--
-- IDEMPOTENT: every CREATE uses IF NOT EXISTS — re-running is a no-op.
--
-- SCALE NOTE: a plain CREATE UNIQUE INDEX takes a write-blocking lock for
-- the build duration. On a large `jobs`/`company_knowledge` table at scale,
-- run the CONCURRENTLY variants at the bottom INSTEAD (each on its own, with
-- NO surrounding transaction — CONCURRENTLY cannot run inside BEGIN/COMMIT;
-- APPLY.sh wraps files in a transaction, so use a non-txn apply path for
-- those). At single-user scale the in-txn forms below are fine.

BEGIN;

-- 1. companies: per-user unique company name.
--    (replaces global UNIQUE (name) — dropped in 044)
CREATE UNIQUE INDEX IF NOT EXISTS companies_user_name_uidx
    ON companies (user_id, name);

-- 2. jobs: per-user unique url. PARTIAL — url is nullable, and a unique on a
--    nullable column must allow multiple NULLs (mirrors the existing
--    people_user_linkedin_url_uidx pattern in migration 004). The matching
--    upsert (db/client.py::upsert_job) only ever conflicts when url IS NOT
--    NULL, so a partial arbiter is exactly right.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_user_url_uidx
    ON jobs (user_id, url)
    WHERE url IS NOT NULL;

-- 3. company_knowledge: per-user unique (company_name, section).
--    (replaces global UNIQUE (company_name, section) — dropped in 044)
CREATE UNIQUE INDEX IF NOT EXISTS company_knowledge_user_company_section_uidx
    ON company_knowledge (user_id, company_name, section);

-- 4. company_personas: per-user unique company_name.
--    (replaces global UNIQUE (company_name) — dropped in 044)
CREATE UNIQUE INDEX IF NOT EXISTS company_personas_user_company_name_uidx
    ON company_personas (user_id, company_name);

-- 5. rizwan_profile: per-user unique section.
--    (replaces global UNIQUE (section) — dropped in 044)
CREATE UNIQUE INDEX IF NOT EXISTS rizwan_profile_user_section_uidx
    ON rizwan_profile (user_id, section);

-- Reload PostgREST schema cache so the new indexes are visible to upserts.
NOTIFY pgrst, 'reload schema';

COMMIT;

-- ── Production-scale (CONCURRENTLY) variants ────────────────────────────
-- On large tables, run these INSTEAD of the CREATE UNIQUE INDEX statements
-- above — each on its own, with NO surrounding transaction:
--
--   CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS companies_user_name_uidx
--       ON companies (user_id, name);
--
--   CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS jobs_user_url_uidx
--       ON jobs (user_id, url)
--       WHERE url IS NOT NULL;
--
--   CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS company_knowledge_user_company_section_uidx
--       ON company_knowledge (user_id, company_name, section);
--
--   CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS company_personas_user_company_name_uidx
--       ON company_personas (user_id, company_name);
--
--   CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS rizwan_profile_user_section_uidx
--       ON rizwan_profile (user_id, section);
--
-- (A CONCURRENTLY build that fails leaves an INVALID index behind; drop it
--  with DROP INDEX <name> and retry. Verify with \d+ <table> or pg_index.)
