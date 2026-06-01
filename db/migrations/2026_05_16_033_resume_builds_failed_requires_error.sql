-- 2026_05_16_033_resume_builds_failed_requires_error.sql
-- B6 / B7: belt-and-suspenders for the "failed with NULL error" class of bug.
-- On 2026-05-11 23:34 UTC, 15 in-flight resume_builds were flipped to
-- status='failed' with error=NULL (probably a worker SIGTERM or an older
-- orphan-reaper sweep that wrote to a non-existent column). We can't
-- reconstruct the failure reason now, but we can prevent the regression.
--
-- Backfill first so the new constraint doesn't reject existing rows, then
-- add the CHECK.
--
-- Idempotent: NOT VALID lets us add the constraint even if there were rows
-- violating it; we VALIDATE separately so the migration is non-blocking on
-- large tables (no-op here at 24 rows, future-proofing).

UPDATE public.resume_builds
SET error = 'unknown: backfilled 2026-05-16 (pre-CHECK-constraint era)'
WHERE status = 'failed' AND error IS NULL;

ALTER TABLE public.resume_builds
  DROP CONSTRAINT IF EXISTS resume_builds_failed_has_error;

ALTER TABLE public.resume_builds
  ADD CONSTRAINT resume_builds_failed_has_error
  CHECK (status <> 'failed' OR error IS NOT NULL) NOT VALID;

ALTER TABLE public.resume_builds
  VALIDATE CONSTRAINT resume_builds_failed_has_error;
