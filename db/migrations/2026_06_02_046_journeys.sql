-- 2026_06_02_046_journeys.sql
-- FRD-16 — High-Fit Auto-Prep Journey.
--
-- WHAT THIS DOES
--   1. Creates `journeys` — one row per auto-prep journey, fired when a job
--      scores >= JOURNEY_MIN_SCORE (default 90). It links the high-fit job to
--      the auto-created draft application and the three child jobs_runs
--      (resume G2, interview-prep G3, network people-finder), and tracks an
--      aggregate status the dashboard reads.
--   2. Adds `applications.auto_created` so the UI can distinguish a draft the
--      journey created (status='resume_ready') from one the user made.
--
-- IDEMPOTENCY / DEDUP
--   UNIQUE (user_id, job_id) makes the journey trigger safe to fire on every
--   re-score: a second insert for the same job is rejected at the DB layer,
--   so a job is journeyed at most once. Composite key matches the
--   2026_05_29_043 multi-tenant convention (tenant-scoped uniqueness).
--
-- SAFETY
--   Additive only — new table + one nullable-with-default column. No drops,
--   no backfill, no data migration. Safe to apply to live prod.

BEGIN;

CREATE TABLE IF NOT EXISTS journeys (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL,
    job_id          integer NOT NULL,
    application_id  uuid,
    trigger_score   integer,
    resume_run_id   uuid,
    prep_run_id     uuid,
    network_run_id  uuid,
    -- running | converged | partial | failed
    status          text NOT NULL DEFAULT 'running',
    note            text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    finalized_at    timestamptz
);

-- Dedup: at most one journey per (tenant, job). The auto-trigger relies on
-- this — a re-score that crosses >=90 again must not create a second journey.
CREATE UNIQUE INDEX IF NOT EXISTS journeys_user_job_uidx
    ON journeys (user_id, job_id);

-- Read path: list a tenant's journeys newest-first (the /today high-fit feed).
CREATE INDEX IF NOT EXISTS journeys_user_created_idx
    ON journeys (user_id, created_at DESC);

-- Provenance: did the auto-prep journey create this draft application?
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS auto_created boolean NOT NULL DEFAULT false;

-- RLS — mirrors jobs_runs (2026_05_10_003): service-role (backend) bypasses
-- RLS; a user's JWT sees only their own journeys. server.py filters by
-- user_id today; these policies are for direct JWT reads when wired.
ALTER TABLE journeys ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'journeys'
          AND policyname = 'user reads own journeys'
    ) THEN
        CREATE POLICY "user reads own journeys" ON journeys
            FOR SELECT USING (auth.uid() = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'journeys'
          AND policyname = 'user inserts own journeys'
    ) THEN
        CREATE POLICY "user inserts own journeys" ON journeys
            FOR INSERT WITH CHECK (auth.uid() = user_id);
    END IF;
END
$$;

COMMIT;
