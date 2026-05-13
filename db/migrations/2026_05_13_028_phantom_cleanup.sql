-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_13_028 — Hard-delete phantom company personas
-- ──────────────────────────────────────────────────────────────────────────
-- BUG-013 follow-through (Stream B, 2026-05-13)
--
-- Why this migration exists
--   BUG-013 (2026-05-12) added `company_personas.is_phantom` and flagged
--   the LinkedIn-title-derived false-positive rows (e.g. "Adyen Careers",
--   "SuperApp", "Merchant Acquiring …", "68 Vacancies Apr 2026"). But the
--   rows were never deleted — they continued to surface in:
--     - /today action cards (E2E test 2026-05-13 found 3 of top 7 cards
--       showed "Adyen Careers" as the company)
--     - /applications kanban (the 2 rejected apps both had phantom company
--       strings — "SuperApp" and "Merchant Acquiring …")
--     - /insights/costs agent breakdown (CompanyAgent[SuperApp] $0.414,
--       CompanyAgent[Adyen Careers] $0.395, CompanyAgent[68 Vacancies] $0.106
--       — real Anthropic spend against fabricated companies)
--     - v_cost_per_outcome view (filtered in dashboard but still rows in DB)
--
--   This migration cleans them up permanently. After apply, the phantom
--   rows no longer exist; the dashboard will get the same empty-state
--   handling it does for any other never-seen company.
--
-- Strategy
--   1. Move company_knowledge rows pointing at phantoms → company_id = NULL
--      (preserves the knowledge text in case any of it was actually correct
--      research; future agents can re-attach if they want).
--   2. NULL out applications.company where it matches a phantom string
--      (preserves the application row; the user can re-classify manually).
--   3. DELETE the phantom company_personas rows.
--   4. Print before/after row counts at every step so the apply log shows
--      exactly what was cleaned.
--
-- Defensive choices
--   - Only operates on rows already flagged `is_phantom = TRUE`. Does NOT
--     try to detect new phantoms. (Re-detection is the discovery layer's
--     job — see BUG-051 LinkedIn parser fix in Stream E.)
--   - Wrapped in a transaction so a mid-migration failure rolls back
--     cleanly.
--   - Adds a partial index `idx_company_personas_is_phantom` so future
--     filter queries (`WHERE is_phantom IS NOT TRUE`) stay fast.
--
-- Rollback
--   This migration is non-reversible — once deleted, the phantom row IDs
--   and any FK-pointing data are gone. If a row was deleted in error, the
--   user must re-discover the company through the normal pipeline. There
--   is no UPDATE that brings the rows back.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

DO $$
DECLARE
    phantom_count INT;
    knowledge_repointed INT;
    applications_nulled INT;
    deleted INT;
BEGIN
    SELECT count(*) INTO phantom_count
    FROM company_personas
    WHERE is_phantom = TRUE;

    RAISE NOTICE '[028] Starting phantom cleanup. Flagged personas: %', phantom_count;

    -- ── Step 1: Repoint company_knowledge.company_id → NULL
    WITH repointed AS (
        UPDATE company_knowledge ck
           SET company_id = NULL
         WHERE ck.company_id IN (
                SELECT id FROM company_personas WHERE is_phantom = TRUE
              )
        RETURNING ck.id
    )
    SELECT count(*) INTO knowledge_repointed FROM repointed;

    RAISE NOTICE '[028] company_knowledge rows repointed (company_id → NULL): %',
                 knowledge_repointed;

    -- ── Step 2: NULL out applications.company where it matches a phantom
    --     name string (case-insensitive). This preserves the application
    --     row + job_id linkage but removes the misleading display value.
    WITH nulled AS (
        UPDATE applications a
           SET company = NULL
         WHERE lower(a.company) IN (
                SELECT lower(company_name)
                  FROM company_personas
                 WHERE is_phantom = TRUE
              )
        RETURNING a.id
    )
    SELECT count(*) INTO applications_nulled FROM nulled;

    RAISE NOTICE '[028] applications.company NULLed: %', applications_nulled;

    -- ── Step 3: DELETE the phantom company_personas rows
    WITH del AS (
        DELETE FROM company_personas
         WHERE is_phantom = TRUE
        RETURNING id
    )
    SELECT count(*) INTO deleted FROM del;

    RAISE NOTICE '[028] company_personas rows deleted: %', deleted;

    -- Sanity assertion
    IF deleted <> phantom_count THEN
        RAISE EXCEPTION '[028] phantom count drift: expected % deletions, got %',
                        phantom_count, deleted;
    END IF;
END $$;

-- ── Index for the (already-present) is_phantom guard ────────────────────
-- Partial index on the small NULL-or-FALSE side so the existing filter
-- (`is_phantom IS NOT TRUE`) stays a fast index lookup even as the table
-- grows. Idempotent: ignored if migration is re-run.
CREATE INDEX IF NOT EXISTS idx_company_personas_is_phantom_false
    ON company_personas (id)
    WHERE is_phantom IS NOT TRUE;

-- Reload PostgREST schema so the dashboard sees the cleaner data right away.
NOTIFY pgrst, 'reload schema';

COMMIT;
