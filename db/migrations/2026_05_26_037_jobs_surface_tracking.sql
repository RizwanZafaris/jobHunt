-- 2026_05_26_037_jobs_surface_tracking.sql
--
-- Card lifecycle tracking (Fix 3 of the Adyen stale-card bug).
--
-- Adds surface counters to jobs so the rank function can penalize cards
-- that have been shown repeatedly without action. Combined with Fix 1
-- (age penalty in rank) and Fix 2 (explicit dismiss/snooze), this gives
-- the system three independent forces pushing stale cards off /today:
--
--   1. Time decay        (age penalty in _rank)
--   2. Surface decay     (this migration's surface_count in _rank)
--   3. User signal       (job_card_dismissals — explicit hide)
--
-- Idempotent + transactional. NULL-safe defaults so existing rows don't
-- break the new ranking logic.

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS first_surfaced_at   timestamptz,
    ADD COLUMN IF NOT EXISTS last_surfaced_at    timestamptz,
    ADD COLUMN IF NOT EXISTS surface_count       int NOT NULL DEFAULT 0;

-- Backfill: for existing rows, treat created_at as the first surfacing.
-- This makes the very first deploy after this migration not lift a stale
-- Adyen card to "fresh" status. (created_at always exists per schema.)
UPDATE jobs
SET first_surfaced_at = COALESCE(first_surfaced_at, created_at),
    last_surfaced_at  = COALESCE(last_surfaced_at, created_at)
WHERE first_surfaced_at IS NULL OR last_surfaced_at IS NULL;

-- Index for the rank function's hot query (surface_count read per /today
-- request). Partial index on surfaced rows keeps it small.
CREATE INDEX IF NOT EXISTS idx_jobs_surface_count
    ON jobs (user_id, surface_count)
    WHERE posting_closed_at IS NULL;

COMMENT ON COLUMN jobs.surface_count IS
    'Number of times this job has been surfaced on /today for this user. Incremented atomically by api/actions.py after each successful response. Used by _rank() to penalize repeatedly-shown, unactioned cards.';

COMMENT ON COLUMN jobs.first_surfaced_at IS
    'First time this job appeared on /today for this user.';

COMMENT ON COLUMN jobs.last_surfaced_at IS
    'Most recent time this job appeared on /today for this user.';

COMMIT;
