-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_14_030 — Flag phantom-string jobs
-- ──────────────────────────────────────────────────────────────────────────
-- Follow-up to migration 028 (phantom company cleanup, 2026-05-13).
--
-- Problem
--   028 deleted the phantom rows from the `companies` table and NULLed
--   FK references on jobs / applications / employments. But the
--   denormalised `jobs.company` TEXT column was NOT cleaned (it has a
--   NOT NULL-equivalent constraint on some rows so the cleanup chose
--   to leave the strings alone).
--
--   Result: production /today + /applications continued to surface
--   "Adyen Careers", "SuperApp", "Merchant Acquiring …", "64 Vacancies
--   Apr 2026" because the queries filter on `company_personas.is_phantom`
--   / `companies.is_phantom` but the orphan job rows had `company_id =
--   NULL` and a phantom STRING.
--
-- Fix
--   Flag every job whose `company` string matches a phantom pattern
--   (mirrors agents/company_agent.py::_is_phantom_company_name regex)
--   by setting `validation_failed = 'phantom_company_string'`.
--   The existing query filter (`validation_failed IS NULL`) on /today
--   + /applications + workspace builders catches them without any
--   runtime code change.
--
-- Reversibility
--   `UPDATE jobs SET validation_failed = NULL WHERE validation_failed =
--    'phantom_company_string';` restores them.
--
-- Live apply (2026-05-14)
--   First pass:  129 jobs flagged.
--   Follow-up:    54 more flagged (specific phantom names that didn't
--                 match the regex: SuperApp, Myworkdayjobs.com, parse-
--                 broken strings starting with non-letter, "vacancies
--                 month year" date stamps).
--   Total:       183 phantom jobs hidden from /today + /applications.
--   Remaining at score>=80: 16 jobs, all real companies.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

DO $$
DECLARE
    flagged INT;
BEGIN
    WITH x AS (
        UPDATE jobs
           SET validation_failed = 'phantom_company_string'
         WHERE (validation_failed IS NULL OR validation_failed = '')
           AND posting_closed_at IS NULL
           AND (
                -- Job-listing chrome
                company ILIKE '%Job in %'
             OR company ILIKE '%Jobs in %'
             OR company ILIKE '%Vacanc%'
             OR company ILIKE '% Careers'
             OR company ILIKE '%Careers%'
             OR company ILIKE '%Hiring%'
             OR company ILIKE '%Job Board%'
             OR company ILIKE '%Job ID%'
             OR company ILIKE '%Job Opening%'
             OR company ILIKE '%Job Details%'
                -- Date stamps embedded in the name
             OR company ~* '(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+202[0-9]'
                -- Trailing ellipsis = truncated scrape
             OR company LIKE '%...%'
                -- "Role @ Company" pattern
             OR company LIKE '% @ %'
                -- Specific phantom names observed in production
             OR lower(company) IN (
                  -- Function / title fragments
                  'careers','hiring','wallet','wallet and payment',
                  'card payments','card processing','credit card',
                  'payment methods','payments & banking','merchant acquiring',
                  'ai/ml platforms','ci/cd infra & scm','marketing platform',
                  'digital product','product owner','product growth',
                  'vice president','job id','fintech','fintech payments',
                  'fintech/payments','data and ai','new product development',
                  'activities, emerging verticals','clients portals',
                  'business account experience',
                  'global products and initiatives',
                  'light commercial vehicle','linkedin singapore',
                  'ksa/uae markets (remote)','latam','europe','remote',
                  'dubai','riyadh','saudi arabia','- pm repo',
                  -- Production-observed phantom names
                  'superapp','mevp job board','myworkdayjobs.com',
                  '64 vacancies apr 2026','91 vacancies may 2026',
                  '1 vacancies may 2026','68 vacancies apr 2026'
                )
                -- Parse-broken strings (parens, numbers, non-letter starts)
             OR company ~ '^[^A-Za-z]'
             OR company ~ '\)\s*with\s+\d'
             OR company ~ '\d{2,}\s+Vacanc'
             OR company ~ '\bClient\s+of\b'
                -- Portal/host names masquerading as companies
             OR company ILIKE 'myworkdayjobs%'
             OR company ILIKE '%bayt%'
             OR company ILIKE '%naukrigulf%'
             OR company ILIKE '%gulftalent%'
                -- Single-character / whitespace-only
             OR length(trim(company)) < 2
              )
        RETURNING id
    )
    SELECT count(*) INTO flagged FROM x;
    RAISE NOTICE '[030] jobs flagged as phantom_company_string: %', flagged;
END $$;

NOTIFY pgrst, 'reload schema';

COMMIT;
