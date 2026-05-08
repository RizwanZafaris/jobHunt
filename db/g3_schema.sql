-- ─────────────────────────────────────────────────────────────────────────
-- db/g3_schema.sql — Phase 2: G3 Interview Prep Graph schema
--
-- DO NOT apply directly. The user will apply via apply_migration after PR
-- review. All statements are idempotent (CREATE TABLE IF NOT EXISTS +
-- CREATE INDEX IF NOT EXISTS) so re-application is safe.
--
-- This migration adds one table: public.interview_prep
--   - One row per G3 invocation (application × round × generation_run)
--   - Mirrors public.resume_builds in shape and lifecycle
--   - References applications(id) and jobs(id) — orphan rows survive job
--     deletion (jobs FK is SET NULL) but follow application deletion (CASCADE)
--
-- Status hierarchy (mirrors resume_builds):
--   running       → row created at entry_node
--   converged     → mock_critic_score >= g3_target_answer_score (or final pass)
--   exhausted     → hit g3_max_iterations without converging
--   cost_capped   → hit g3_max_cost_usd mid-build
--   failed        → fatal error mid-graph
-- ─────────────────────────────────────────────────────────────────────────

-- ── 1. interview_prep table ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS interview_prep (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id              UUID REFERENCES applications(id) ON DELETE CASCADE,
    job_id                      INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    company_name                TEXT,

    -- Round identification
    round_number                INT DEFAULT 1,
    round_type                  TEXT,        -- recruiter | hm | panel | exec | technical | take_home

    -- Lifecycle
    status                      TEXT DEFAULT 'running',  -- running | converged | exhausted | cost_capped | failed
    iterations                  INT DEFAULT 0,           -- mock_interview_loop iter count

    -- Output artifacts
    prep_pack_md                TEXT,
    prep_pack_url               TEXT,        -- Supabase Storage signed URL (interview-packs/ bucket)

    -- Structured outputs (JSONB for queryability)
    likely_questions            JSONB DEFAULT '[]',   -- [{question, competency, importance, source}]
    star_stories                JSONB DEFAULT '[]',   -- [{question, story_id, match_quality, needs_rizwan_input}]
    company_hooks               JSONB DEFAULT '[]',   -- [str, ...]
    red_flag_questions          JSONB DEFAULT '[]',   -- [str, ...]
    salary_negotiation_notes    TEXT,

    -- Audit
    agent_transcript            JSONB DEFAULT '[]',   -- full G3 graph transcript
    cost_usd_total              NUMERIC(10,4) DEFAULT 0,
    latency_ms_total            INT DEFAULT 0,
    graph_thread_id             TEXT,                  -- langgraph checkpointer thread id

    -- Error capture (only set when status='failed')
    error                       TEXT,

    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    finalized_at                TIMESTAMPTZ
);


-- ── 2. Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_interview_prep_application
    ON interview_prep(application_id);
CREATE INDEX IF NOT EXISTS idx_interview_prep_job
    ON interview_prep(job_id);
CREATE INDEX IF NOT EXISTS idx_interview_prep_company
    ON interview_prep(company_name);
CREATE INDEX IF NOT EXISTS idx_interview_prep_status
    ON interview_prep(status);
