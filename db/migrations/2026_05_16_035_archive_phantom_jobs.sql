-- 2026_05_16_035_archive_phantom_jobs.sql
-- B12: 183 of 405 jobs rows have validation_failed='phantom_company_string'
-- — scraper artifacts where the company field contains job-listing chrome
-- ("Senior PM at Visa", "Jobs in Dubai", date stamps, ellipses). Migration
-- 030 flagged them; the UI filters them out, but they pollute analytics
-- and storage. Move them to jobs_archive so the live jobs table only
-- holds real entries.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + the move uses a CTE that is
-- safe to re-run (no-op once the source rows are gone). The same is true
-- for the archive_reason backfill: rows already in jobs_archive are not
-- duplicated.
--
-- Forensics: jobs_archive keeps the rows for scrape-quality investigation.
-- Do NOT drop it.

CREATE TABLE IF NOT EXISTS public.jobs_archive (LIKE public.jobs INCLUDING ALL);
ALTER TABLE public.jobs_archive
  ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS archived_reason TEXT;

-- RLS: same per-user pattern as jobs. Without it, multi-tenant exposure.
ALTER TABLE public.jobs_archive ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS jobs_archive_select_own ON public.jobs_archive;
CREATE POLICY jobs_archive_select_own
  ON public.jobs_archive FOR SELECT
  USING ((SELECT auth.uid()) = user_id);

DROP POLICY IF EXISTS jobs_archive_insert_own ON public.jobs_archive;
CREATE POLICY jobs_archive_insert_own
  ON public.jobs_archive FOR INSERT
  WITH CHECK ((SELECT auth.uid()) = user_id);

-- Move (DELETE...RETURNING into INSERT). Atomic. Rows already moved won't
-- match the WHERE clause on subsequent runs.
WITH archived AS (
  DELETE FROM public.jobs
  WHERE validation_failed = 'phantom_company_string'
  RETURNING *
)
INSERT INTO public.jobs_archive
SELECT *, NOW() AS archived_at, 'phantom_company_string' AS archived_reason
FROM archived;
