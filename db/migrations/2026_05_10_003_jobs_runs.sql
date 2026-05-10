-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_003 — jobs_runs (queue durability)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Backing table for the Redis/RQ work queue. Every long-running graph
--   (G1 discovery, G2 resume, G3 interview, future G4 LinkedIn post)
--   writes one row here BEFORE enqueueing. The worker mutates the row
--   through its lifecycle (queued → running → succeeded/failed/cancelled).
--
--   Pre-Phase-3 these graphs ran via FastAPI's BackgroundTasks. A Railway
--   redeploy mid-build dropped the work on the floor with no recovery.
--   This table + the api/orphan_reaper.py sweeper + RQ retries close
--   that hole.
--
-- Idempotency
--   Every CREATE / ALTER guarded by IF NOT EXISTS. Re-running is a no-op.
--
-- Apply order
--   001 (multi-tenancy) → 002 (status enum) → 003 (this file)
--
-- Rollback
--   DROP TABLE jobs_runs;
--   But: if the worker is running this will lose in-flight work. Drain
--   the RQ queue first (`rq empty <queue>`) before rollback.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── extension ─────────────────────────────────────────────────────────────
-- pgcrypto provides gen_random_uuid(); idempotent on Supabase.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── table ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jobs_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL,
    kind            text NOT NULL CHECK (
        kind IN ('g1_discovery', 'g2_resume', 'g3_interview', 'g4_linkedin_post')
    ),
    payload         jsonb NOT NULL,
    status          text NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
    ),
    -- sha256(user_id|kind|sorted_json(payload)) — unique so duplicate
    -- enqueues collapse to one row. See api/queue.py::_idempotency_key.
    idempotency_key text UNIQUE NOT NULL,
    started_at      timestamptz,
    finished_at     timestamptz,
    attempts        int NOT NULL DEFAULT 0,
    last_error      text,
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ── indexes ───────────────────────────────────────────────────────────────
-- Hot path: dashboard lists "my recent runs" filtered by status.
CREATE INDEX IF NOT EXISTS jobs_runs_user_status_idx
    ON jobs_runs (user_id, status);

-- Hot path: orphan reaper sweep — find rows still 'running' with old
-- started_at. Partial index keeps it tiny since most rows are terminal.
CREATE INDEX IF NOT EXISTS jobs_runs_running_started_idx
    ON jobs_runs (status, started_at)
    WHERE status = 'running';

-- Created-at for time-range queries (e.g. "show today's runs").
CREATE INDEX IF NOT EXISTS jobs_runs_created_at_idx
    ON jobs_runs (created_at DESC);

-- ── RLS ───────────────────────────────────────────────────────────────────
-- service-role (used by api/server.py + worker) bypasses RLS automatically,
-- so server-side inserts/updates work without policy gymnastics. The
-- policies below are for the user's JWT-authenticated reads from the
-- dashboard (when we wire that up; not used by server.py today).
ALTER TABLE jobs_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'jobs_runs'
          AND policyname = 'user reads own runs'
    ) THEN
        CREATE POLICY "user reads own runs" ON jobs_runs
            FOR SELECT USING (auth.uid() = user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'jobs_runs'
          AND policyname = 'user inserts own runs'
    ) THEN
        CREATE POLICY "user inserts own runs" ON jobs_runs
            FOR INSERT WITH CHECK (auth.uid() = user_id);
    END IF;
END
$$;

COMMIT;
