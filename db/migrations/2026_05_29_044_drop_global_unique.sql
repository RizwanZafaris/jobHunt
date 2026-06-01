-- 2026_05_29_044_drop_global_unique.sql
-- Multi-tenancy DB-1 (step 2 of 2) — drop the old GLOBAL UNIQUE constraints.
-- CTO scalability audit, 2026-05-29. See docs/SCALABILITY.md §3 P0 and
-- docs/SCALABILITY_BUILD_PLAN.md (PR DB-1). Companion to
-- 2026_05_29_043_composite_unique_indexes.sql.
--
-- WHAT THIS DOES
--   Removes the five global UNIQUE constraints that broke tenancy. The
--   per-user composite UNIQUE indexes built in 043 take over as the
--   uniqueness guarantee (and as the on_conflict arbiters for the upserts
--   shipped in the DB-1 client code).
--
-- ┌─ APPLY ORDER (do NOT run this before the client code is deployed) ─────┐
-- │  1. Apply 2026_05_29_043_composite_unique_indexes.sql                  │
-- │  2. Deploy the DB-1 client code (on_conflict → (user_id, …) edits +    │
-- │     user_id-on-write fixes). The new code conflicts on the composite   │
-- │     index from 043, so it works WHILE the old global UNIQUEs still     │
-- │     exist.                                                             │
-- │  3. Apply THIS migration (044) — ONLY after step 2 is live in prod.    │
-- │                                                                        │
-- │  Why this exact order: dropping the old UNIQUE before the new code is  │
-- │  deployed would leave the OLD client (conflict target = bare column)   │
-- │  with no matching arbiter → upserts would error. Deploying the new     │
-- │  code before 043's indexes exist would likewise have no arbiter.       │
-- │  043 → code → 044 keeps a valid arbiter for the live code at every     │
-- │  step.                                                                 │
-- └───────────────────────────────────────────────────────────────────────┘
--
-- CONSTRAINT NAMES (verified live via Supabase MCP on 2026-05-29 with:
--   SELECT conname, conrelid::regclass AS tbl, pg_get_constraintdef(oid)
--   FROM pg_constraint WHERE contype='u' AND conrelid IN
--     ('companies'::regclass,'jobs'::regclass,'company_knowledge'::regclass,
--      'company_personas'::regclass,'rizwan_profile'::regclass); )
--       companies_name_key                     UNIQUE (name)
--       jobs_url_key                           UNIQUE (url)
--       company_knowledge_company_section_key  UNIQUE (company_name, section)
--       company_personas_company_name_key      UNIQUE (company_name)
--       rizwan_profile_section_key             UNIQUE (section)
--
-- ROBUSTNESS: rather than a bare `ALTER TABLE ... DROP CONSTRAINT <name>`
-- (which would silently no-op-or-mismatch if a name differs in some
-- environment — e.g. a from-scratch rebuild that backed uniqueness with an
-- index rather than a named constraint), each block below:
--   (a) drops the verified-named constraint IF it exists, else
--   (b) looks the constraint up by its EXACT column set on that table and
--       drops it by whatever name it actually has, else
--   (c) drops a stray unique INDEX with the same column set (covers the
--       case where uniqueness was created as a UNIQUE INDEX, not a
--       CONSTRAINT — those don't appear in pg_constraint).
-- It deliberately does NOT touch the composite (user_id, …) indexes from
-- 043: the lookups filter the old constraints by their precise column set,
-- which never matches the composite key.
--
-- IDEMPOTENT: after the old constraints are gone, every block is a no-op.
-- Re-running is safe.

BEGIN;

DO $drop_global_uniques$
DECLARE
    target   RECORD;
    tbl_oid  regclass;
    found_name TEXT;
BEGIN
    -- (table name, verified constraint name, ordered column set of the OLD
    --  global unique). Table name kept as plain text and resolved per-loop
    --  via to_regclass() so a non-existent table (fresh install) is skipped
    --  rather than erroring.
    -- NOTE: a (VALUES …) AS t(…) alias takes bare column names only — no
    -- per-column type declarations (that form is for set-returning funcs).
    -- So column types are inferred from the literals; cast the arrays to
    -- text[] explicitly so `= target.cols` compares against text[].
    FOR target IN
        SELECT * FROM (VALUES
            ('companies',         'companies_name_key',                    ARRAY['name']::text[]),
            ('jobs',              'jobs_url_key',                          ARRAY['url']::text[]),
            ('company_knowledge', 'company_knowledge_company_section_key', ARRAY['company_name','section']::text[]),
            ('company_personas',  'company_personas_company_name_key',     ARRAY['company_name']::text[]),
            ('rizwan_profile',    'rizwan_profile_section_key',            ARRAY['section']::text[])
        ) AS t(tbl, conname, cols)
    LOOP
        tbl_oid := to_regclass(target.tbl);
        IF tbl_oid IS NULL THEN
            RAISE NOTICE 'table % does not exist — skipping', target.tbl;
            CONTINUE;
        END IF;

        -- (a) verified-named constraint?
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = tbl_oid
              AND contype = 'u'
              AND conname = target.conname
        ) THEN
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', tbl_oid, target.conname);
            RAISE NOTICE 'dropped constraint %.% (verified name)', target.tbl, target.conname;
            CONTINUE;
        END IF;

        -- (b) any UNIQUE constraint on this table whose column set is EXACTLY
        --     the old global unique's columns (in order)?
        SELECT c.conname INTO found_name
        FROM pg_constraint c
        WHERE c.conrelid = tbl_oid
          AND c.contype = 'u'
          AND (
            -- attname is type `name`; cast to text so it compares to text[]
            SELECT array_agg(a.attname::text ORDER BY k.ord)
            FROM unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
          ) = target.cols
        LIMIT 1;

        IF found_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', tbl_oid, found_name);
            RAISE NOTICE 'dropped constraint %.% (matched by column set %)',
                target.tbl, found_name, target.cols;
            CONTINUE;
        END IF;

        -- (c) a bare UNIQUE INDEX (not a constraint) with exactly those
        --     columns, non-partial, on this table? (must NOT match the 043
        --     composite, which leads with user_id → different column set.)
        SELECT i.indexrelid::regclass::text INTO found_name
        FROM pg_index i
        WHERE i.indrelid = tbl_oid
          AND i.indisunique
          AND i.indpred IS NULL                       -- non-partial only
          AND NOT EXISTS (                            -- not backing a constraint
            SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid
          )
          AND (
            -- attname is type `name`; cast to text so it compares to text[]
            SELECT array_agg(a.attname::text ORDER BY k.ord)
            FROM unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
          ) = target.cols
        LIMIT 1;

        IF found_name IS NOT NULL THEN
            EXECUTE format('DROP INDEX %s', found_name);
            RAISE NOTICE 'dropped unique index % (matched by column set %)',
                found_name, target.cols;
            CONTINUE;
        END IF;

        RAISE NOTICE 'no old global unique found on % for columns % (already dropped?) — skipping',
            target.tbl, target.cols;
    END LOOP;
END
$drop_global_uniques$;

-- Reload PostgREST schema cache so the dropped constraints are reflected.
NOTIFY pgrst, 'reload schema';

COMMIT;

-- ── Rollback (best-effort; only on a throwaway branch) ──────────────────
-- Re-adding the global UNIQUEs is destructive once a 2nd tenant exists
-- (duplicate (name)/(url)/… across tenants would block the ADD). Safe only
-- at single-user scale:
--
--   BEGIN;
--   ALTER TABLE companies         ADD CONSTRAINT companies_name_key                    UNIQUE (name);
--   ALTER TABLE jobs              ADD CONSTRAINT jobs_url_key                          UNIQUE (url);
--   ALTER TABLE company_knowledge ADD CONSTRAINT company_knowledge_company_section_key UNIQUE (company_name, section);
--   ALTER TABLE company_personas  ADD CONSTRAINT company_personas_company_name_key     UNIQUE (company_name);
--   ALTER TABLE rizwan_profile    ADD CONSTRAINT rizwan_profile_section_key            UNIQUE (section);
--   NOTIFY pgrst, 'reload schema';
--   COMMIT;
