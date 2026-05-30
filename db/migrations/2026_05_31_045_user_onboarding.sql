-- 2026_05_31_045_user_onboarding.sql
-- Phase 4 (auth + onboarding front door) — onboarding state on the users table.
--
-- WHAT THIS DOES
--   Adds three nullable columns to `users` so a freshly-signed-up tenant has a
--   first-run onboarding flow the dashboard can drive:
--     - onboarded_at      timestamptz  — NULL until the user finishes onboarding;
--                                          set to now() when they do. The
--                                          dashboard routes a user with
--                                          onboarded_at IS NULL to /onboarding.
--     - onboarding_step   text         — optional resumable cursor (e.g.
--                                          'welcome' | 'profile' | 'plan' | 'done')
--                                          so a partially-onboarded user can be
--                                          dropped back where they left off.
--     - signup_source     text         — optional provenance ('google' | 'email'
--                                          | 'invite' | ...) for funnel analytics.
--
-- WHY NULLABLE / NO BACKFILL OF onboarded_at
--   The single existing user (user_001, the owner) should NOT be forced through
--   onboarding. Leaving onboarded_at NULL would route the owner to /onboarding,
--   so we BACKFILL THE OWNER ONLY to now() — every *new* signup defaults to NULL
--   and gets the flow. This keeps the live single-user system untouched.
--
-- SAFETY
--   Idempotent: ADD COLUMN IF NOT EXISTS + a guarded owner backfill. Additive
--   only — no column is dropped, no type changed, no constraint added that could
--   reject existing rows. Safe to apply on the live DB at any time; nothing in
--   the running app reads these columns until the Phase-4 frontend ships.
--
--   NOT auto-applied. Apply with your usual migration step (Supabase MCP /
--   psql) when you choose.

BEGIN;

ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarded_at    TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS onboarding_step TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS signup_source   TEXT;

-- Owner (seed user_001) is already a full user — mark onboarded so the live
-- single-user dashboard never routes the owner into the new onboarding flow.
-- Guarded so re-running never clobbers a real value.
UPDATE users
   SET onboarded_at = COALESCE(onboarded_at, NOW()),
       onboarding_step = COALESCE(onboarding_step, 'done')
 WHERE id = '00000000-0000-0000-0000-000000000001'::uuid;

COMMENT ON COLUMN users.onboarded_at IS
    'Phase 4: NULL = onboarding incomplete (dashboard routes to /onboarding); timestamp = completed.';
COMMENT ON COLUMN users.onboarding_step IS
    'Phase 4: resumable onboarding cursor (welcome|profile|plan|done). NULL treated as welcome.';
COMMENT ON COLUMN users.signup_source IS
    'Phase 4: provenance of the signup (google|email|invite|...). For funnel analytics.';

COMMIT;
