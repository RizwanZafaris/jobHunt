-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_013 — companies.is_phantom flag (BUG-013)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   BUG-013: JobScoutAgent had been creating "phantom" company rows whenever
--   it scraped a search-result page where the company column held garbage
--   text instead of a real company name. Examples spotted in production:
--     - "SuperApp"
--     - "Merchant Acquiring ..."
--     - "Adyen Careers"           (sub-string of the page title)
--     - "Job in Dubai, UAE by Finkraft.ai"
--     - "68 Vacancies Apr 2026"
--     - "Careem (Uber subsidiary)"
--     - "- PM Repo", "Job ID", "Wallet", "Riyadh"
--
--   Persona deep-research then ran on these phantoms, wasting Anthropic +
--   Serper + Apify spend (~$3.35 over 146 calls; phantoms were ~3 of 71
--   personas → ≥4% of cost burnt on garbage).
--
--   The agents/company_agent.py::_is_phantom_company_name validator (added in
--   the same commit as this migration) now blocks new phantoms at write time.
--   This migration tags the existing rows that have FK dependents so the
--   downstream queries can filter them out without dropping data.
--
-- Schema change
--   ALTER TABLE companies ADD COLUMN is_phantom BOOLEAN NOT NULL DEFAULT false;
--
-- Backfill
--   Rows that already have NO dependents (jobs / personas / applications)
--   are deleted out-of-band via the same operational script that ran this
--   migration. Rows that DO have dependents get is_phantom=true here.
--
-- Idempotency
--   ADD COLUMN guarded by IF NOT EXISTS; UPDATE statements only touch rows
--   matching the phantom patterns and only when is_phantom is still false.
--
-- Rollback
--   ALTER TABLE companies DROP COLUMN IF EXISTS is_phantom;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── 1. add the flag column ──────────────────────────────────────────────
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS is_phantom BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN companies.is_phantom IS
    'Set true when this row is a scraping artifact (job-title fragment, '
    'careers-page suffix, date-stamped vacancy count, etc.) that survived '
    'JobScout''s historic loose company-name parsing. Downstream queries '
    '(/companies, /personas, persona deep-research) MUST exclude phantoms. '
    'New phantoms are blocked at write time by '
    'agents/company_agent._is_phantom_company_name. See BUG-013.';

-- Helpful partial index for the common "exclude phantoms" filter.
CREATE INDEX IF NOT EXISTS idx_companies_real_only
    ON companies (name)
    WHERE is_phantom = false;

-- ── 2. tag rows that have FK dependents (cannot DELETE without cascade) ─
-- Three known cases at migration time, identified by BUG-013 audit:
--   * SuperApp             — jobs=1, personas=1, applications=1
--   * Merchant Acquiring ...— jobs=1, personas=1, applications=1
--   * Adyen Careers         — jobs=3, personas=1
-- We tag by name (which is the unique key) so the migration is replayable
-- if rows are restored from backup.
UPDATE companies
   SET is_phantom = true
 WHERE is_phantom = false
   AND name IN (
       'SuperApp',
       'Merchant Acquiring ...',
       'Adyen Careers'
   );

-- ── 3. reload PostgREST schema cache (Supabase) ─────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
