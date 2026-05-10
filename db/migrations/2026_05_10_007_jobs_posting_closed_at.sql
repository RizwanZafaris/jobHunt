-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_007 — jobs.posting_closed_at + revalidation cache
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   The /today surface ranks jobs by score and presents a "Start application
--   process" CTA. We do NOT want to surface jobs whose JD URL is dead — the
--   user clicks through and lands on a 404 or "this job is no longer open".
--
--   This migration adds three columns to `jobs`:
--     - posting_closed_at  timestamptz  — set by agents/job_validator.py
--                                          when the JD URL returns 404/410
--                                          or redirects to a careers home.
--                                          NULL means "still open".
--     - last_validated_at  timestamptz  — last revalidation hit on jobs.url.
--                                          Used as a 6-hour cache so /today
--                                          doesn't hammer job boards.
--     - validation_status  text          — one of: open | closed | redirect
--                                          | error | rate_limited.
--
--   And a partial index for the /today hot path:
--     jobs_user_open_idx ON jobs (user_id, match_score DESC)
--                        WHERE posting_closed_at IS NULL
--
-- Idempotency
--   ADD COLUMN IF NOT EXISTS — safe to re-run.
--
-- Apply order
--   001 → 002 → 003 → 004 → 005 → 006 → 007 (this file)
--
-- Rollback
--   ALTER TABLE jobs DROP COLUMN posting_closed_at;
--   ALTER TABLE jobs DROP COLUMN last_validated_at;
--   ALTER TABLE jobs DROP COLUMN validation_status;
--   DROP INDEX jobs_user_open_idx;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS posting_closed_at  timestamptz,
    ADD COLUMN IF NOT EXISTS last_validated_at  timestamptz,
    ADD COLUMN IF NOT EXISTS validation_status  text;

COMMENT ON COLUMN jobs.posting_closed_at  IS 'Set by agents/job_validator when the JD URL returns 404/410 or redirects to a careers home. NULL = still open.';
COMMENT ON COLUMN jobs.last_validated_at  IS 'Last time agents/job_validator hit jobs.url. Used for the 6-hour revalidation cache.';
COMMENT ON COLUMN jobs.validation_status  IS 'open | closed | redirect | error | rate_limited — set by validator alongside posting_closed_at.';

CREATE INDEX IF NOT EXISTS jobs_user_open_idx
    ON jobs (user_id, match_score DESC)
    WHERE posting_closed_at IS NULL;

NOTIFY pgrst, 'reload schema';

COMMIT;
