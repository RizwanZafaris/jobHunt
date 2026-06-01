-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_025 — Fix v_company_conversion_funnel
-- ──────────────────────────────────────────────────────────────────────────
-- BUG-043 (NEW, from E2E verification 2026-05-12)
--
-- Symptom
--   /insights Conversion Funnel chart kept showing closed/phantom jobs as
--   funnel rows even after:
--     • BUG-002 closed OKX 1641 (still showed as "OKX" in funnel)
--     • BUG-013 filtered phantom companies from /companies + /personas
--       (still showed "Job in Dubai,UAE byFinkraft.ai" + "Adyen Careers"
--       in funnel x-axis)
--
-- Root cause
--   The view aggregated by `resume_builds.company_name` — the raw
--   LinkedIn-pulled string captured at build time. It didn't JOIN to
--   `jobs` (so closed-posting filter never ran) and didn't JOIN to
--   `companies.is_phantom` (so phantom filter never ran). Whatever
--   `company_name` string was on resume_builds at insert time stayed
--   in the funnel forever.
--
-- Fix
--   Recreate the view with two new defensive filters:
--     1. JOIN jobs ON jobs.id = resume_builds.job_id
--        WHERE jobs.posting_closed_at IS NULL
--          AND jobs.validation_failed IS NULL
--     2. LEFT JOIN companies ON companies.name = resume_builds.company_name
--        WHERE companies.is_phantom IS NOT TRUE  -- NULL or false
--        (LEFT JOIN so we don't drop rows whose company_name has no
--         matching companies row — those are kept as "unknown but
--         not-phantom".)
--   Plus a defensive name-pattern blocklist for phantom names that
--   slipped through before BUG-013 added is_phantom (legacy data).
--
-- Compatibility
--   Same column list, same column types — drop-in replacement. Existing
--   readers (frontend ConversionFunnel.tsx, /insights page) work
--   unchanged. The change is purely a tighter WHERE.
--
-- Rollback
--   See history: the previous definition is in the BUG-043 entry in the
--   Bug Log §17, or recover via the pre-025 catalog snapshot.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

DROP VIEW IF EXISTS public.v_company_conversion_funnel;

CREATE VIEW public.v_company_conversion_funnel AS
SELECT
    rb.company_name,
    count(DISTINCT rb.id) AS resumes_built,
    count(DISTINCT ro.id) FILTER (WHERE ro.recruiter_responded = true) AS responses,
    count(DISTINCT ro.id) FILTER (WHERE ro.interview_received = true) AS interviews,
    count(DISTINCT ro.id) FILTER (WHERE ro.offer_received = true) AS offers,
    round(avg(rb.polisher_score), 1) AS avg_polisher_score,
    round(avg(rb.cost_usd_total), 2) AS avg_cost_usd
FROM resume_builds rb
    -- Join via job_id to filter closed / failed-validation postings.
    LEFT JOIN jobs j ON j.id = rb.job_id
    -- Left join companies for the is_phantom signal. NULL match → row
    -- survives the filter (company_name has no row in companies, treat
    -- as "unknown but not phantom" so we don't accidentally drop legit
    -- rows).
    LEFT JOIN companies c ON lower(c.name) = lower(rb.company_name)
    -- Outcomes join unchanged.
    LEFT JOIN resume_outcomes ro ON ro.resume_build_id = rb.id
WHERE rb.status = 'converged'::text
  -- 1. Build itself must be on a still-open job (or have no job FK).
  AND (j.id IS NULL OR j.posting_closed_at IS NULL)
  AND (j.id IS NULL OR j.validation_failed IS NULL)
  -- 2. Company must not be flagged as phantom.
  AND (c.id IS NULL OR c.is_phantom IS NOT TRUE)
  -- 3. Defensive name-pattern blocklist for legacy phantom rows that
  --    pre-date the is_phantom flag (e.g. resume_builds inserted before
  --    PR #89 backfilled companies.is_phantom).
  AND rb.company_name NOT ILIKE '%Job in %'
  AND rb.company_name NOT ILIKE '%Jobs in %'
  AND rb.company_name NOT ILIKE '%Vacancies%'
  AND rb.company_name NOT ILIKE '%Vacancy%'
  AND rb.company_name NOT ILIKE '%Careers%'
  AND rb.company_name NOT ILIKE '%Hiring%'
  AND rb.company_name !~ '(?i)\d+\s+vacanc'
  AND rb.company_name !~ '(?i)\s+\d{4}\s*$'  -- trailing year
GROUP BY rb.company_name
ORDER BY count(DISTINCT ro.id) FILTER (WHERE ro.interview_received = true) DESC NULLS LAST;

COMMENT ON VIEW public.v_company_conversion_funnel IS
    'BUG-043 (2026-05-12): conversion funnel by company, filtered to '
    'still-open jobs + non-phantom companies + name-pattern blocklist '
    'for legacy phantom rows. Pre-025: surfaced closed OKX + phantom '
    '"Job in Dubai" labels on /insights even after BUG-002 + BUG-013.';

GRANT SELECT ON public.v_company_conversion_funnel TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
