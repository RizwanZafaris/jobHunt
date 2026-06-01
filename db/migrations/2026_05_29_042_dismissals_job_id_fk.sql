-- 2026_05_29_042_dismissals_job_id_fk.sql
-- Scale-hardening migration (CTO scalability audit, 2026-05-29 — PR DB-2).
--
-- Fixes three defects in job_card_dismissals (migration 036):
--
--   1. TYPE MISMATCH — job_id was declared `bigint`, but jobs.id is
--      `integer` (SERIAL, see db/schema.sql). A bigint→integer mismatch
--      blocks an index-only / merge join against jobs(id) and is a
--      correctness foot-gun. Narrow it to `integer`.
--
--   2. MISSING FK — there was no foreign key from job_card_dismissals.job_id
--      to jobs(id), so dismissals could reference deleted jobs (orphans)
--      and a job delete left stale dismissal rows behind. Add the FK with
--      ON DELETE CASCADE.
--
--   3. NON-SARGABLE PARTIAL INDEX — idx_dismissals_active used a
--      `WHERE snoozed_until IS NULL OR snoozed_until > now()` predicate.
--      now() is STABLE-but-not-IMMUTABLE, so the planner cannot use a
--      partial index whose predicate depends on it: the index snapshots
--      the truth at *build* time and goes stale immediately. The app
--      (api/actions.py::_active_dismissed_job_ids) therefore fetched ALL
--      of a user's dismissal rows and filtered the active snoozes in
--      Python. Redefine the index WITHOUT the now() predicate, covering
--      (user_id, job_id) INCLUDE (snoozed_until) so the rewritten query
--      `eq(user_id).or_(snoozed_until.is.null,snoozed_until.gt.<now>)`
--      is index-eligible and the snooze filter is pushed into Postgres.
--      (DB audit finding DB-2.)
--
-- SAFE TO APPLY AS-IS on the current single-user DB (table is small). At
-- multi-tenant scale, build idx_dismissals_active with CREATE INDEX
-- CONCURRENTLY *outside* a transaction instead (the CONCURRENTLY form is
-- given at the bottom) — a plain CREATE INDEX takes a write-blocking lock
-- for the build duration, and CONCURRENTLY cannot run inside BEGIN/COMMIT.
--
-- Idempotent + transactional.

BEGIN;

-- 1. Narrow job_id bigint → integer to match jobs.id (SERIAL/integer) ─────
--    No-op if already integer; the USING cast is a safe widening-free
--    narrowing (dismissal job_ids are real jobs.id values, well within
--    int4 range).
ALTER TABLE job_card_dismissals
    ALTER COLUMN job_id TYPE integer USING job_id::integer;

-- 2. Add the FK to jobs(id), idempotently. ADD CONSTRAINT has no
--    IF NOT EXISTS, so guard with a catalog check.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM   pg_constraint
        WHERE  conname = 'job_card_dismissals_job_id_fkey'
        AND    conrelid = 'job_card_dismissals'::regclass
    ) THEN
        ALTER TABLE job_card_dismissals
            ADD CONSTRAINT job_card_dismissals_job_id_fkey
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE;
    END IF;
END$$;

-- 3. Redefine idx_dismissals_active WITHOUT the now() predicate. ──────────
--    The old partial index (WHERE snoozed_until IS NULL OR
--    snoozed_until > now()) is non-sargable — its predicate freezes at
--    build time, so the planner won't use it for the runtime active-snooze
--    query. Replace with a plain composite index covering the lookup key
--    + the snoozed_until column the query reads, so the rewritten
--    `eq(user_id).or_(snoozed_until.is.null,snoozed_until.gt.<now>)` is
--    fully index-eligible.
DROP INDEX IF EXISTS idx_dismissals_active;
CREATE INDEX IF NOT EXISTS idx_dismissals_active
    ON job_card_dismissals (user_id, job_id)
    INCLUDE (snoozed_until);

COMMIT;

-- Tell PostgREST to pick up the column-type + constraint changes.
NOTIFY pgrst, 'reload schema';

-- ── Production-scale (CONCURRENTLY) variant ─────────────────────────────
-- On a large job_card_dismissals table, run these INSTEAD of the
-- DROP INDEX / CREATE INDEX pair above — each on its own, with NO
-- surrounding transaction:
--
--   DROP INDEX CONCURRENTLY IF EXISTS idx_dismissals_active;
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dismissals_active
--       ON job_card_dismissals (user_id, job_id)
--       INCLUDE (snoozed_until);
