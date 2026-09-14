-- Migration 047: linkedin_apply_queue
-- Tracks LinkedIn Easy Apply pipeline state per (user_id, job_id).
-- One row per job. Status transitions:
--   pending → resume_building → preparing → paused → approved → submitted
--   Any step can go to 'error' or 'skipped'.

CREATE TABLE IF NOT EXISTS linkedin_apply_queue (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id              INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,

    -- Status of this pipeline entry.
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending',
                            'resume_building',
                            'preparing',
                            'paused',          -- Playwright filled, stopped at Review
                            'approved',        -- User approved via HITL
                            'submitted',       -- LinkedIn Submit clicked successfully
                            'error',
                            'skipped'
                        )),

    -- The G2 resume build used for this application.
    resume_build_id     UUID REFERENCES resume_builds(id),

    -- The LinkedIn job ID extracted from apply_url (e.g. "1234567890").
    linkedin_job_id     TEXT,
    apply_url           TEXT,

    -- AI-generated form answers (from prefill_application / OpenRouter).
    prefilled_answers   JSONB DEFAULT '[]',

    -- Summary of the Playwright fill pass (steps, filled/failed counts, errors).
    fill_summary        JSONB,

    -- Error message if status='error'.
    error               TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at        TIMESTAMPTZ,

    -- One pipeline entry per (user, job). Upserted on each /prepare call.
    CONSTRAINT linkedin_apply_queue_user_job_uq UNIQUE (user_id, job_id)
);

-- Enable RLS — same pattern as all other user-scoped tables.
ALTER TABLE linkedin_apply_queue ENABLE ROW LEVEL SECURITY;

CREATE POLICY linkedin_apply_queue_user_select
    ON linkedin_apply_queue FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY linkedin_apply_queue_user_insert
    ON linkedin_apply_queue FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY linkedin_apply_queue_user_update
    ON linkedin_apply_queue FOR UPDATE
    USING (auth.uid() = user_id);

-- Index for the eligible-jobs query (status + user).
CREATE INDEX IF NOT EXISTS linkedin_apply_queue_status_user
    ON linkedin_apply_queue (user_id, status);

-- Auto-update updated_at on any change.
CREATE OR REPLACE FUNCTION _update_linkedin_apply_queue_ts()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_linkedin_apply_queue_updated_at ON linkedin_apply_queue;
CREATE TRIGGER trg_linkedin_apply_queue_updated_at
    BEFORE UPDATE ON linkedin_apply_queue
    FOR EACH ROW EXECUTE FUNCTION _update_linkedin_apply_queue_ts();
