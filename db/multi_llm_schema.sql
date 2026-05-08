-- ══════════════════════════════════════════════════════════════════════════
-- Multi-LLM + LangGraph Schema Migration (Phase 0 + Phase 1 prep)
-- Run this in your Supabase SQL editor on top of schema.sql.
-- Idempotent: safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════

-- ── 1. agent_call_log ────────────────────────────────────────────────────
-- Per-LLM-call observability written by agents/llm_router.py.
-- Lets us answer: which provider/model/agent burns the most tokens? Which
-- nodes are slowest? When did costs spike?
CREATE TABLE IF NOT EXISTS agent_call_log (
    id              BIGSERIAL PRIMARY KEY,
    agent_name      TEXT,                          -- e.g. "CompanyAgent[Stripe]"
    graph           TEXT,                          -- 'g1' | 'g2' | 'g3' | NULL (legacy)
    node_name       TEXT,                          -- LangGraph node name (when graphs land)
    provider        TEXT NOT NULL,                 -- anthropic | openai | google | deepseek | moonshot
    model           TEXT NOT NULL,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        NUMERIC(10, 6) DEFAULT 0,
    latency_ms      INTEGER DEFAULT 0,
    -- Foreign keys are nullable — calls happen outside any single job/app context too
    job_id          INTEGER,
    application_id  UUID,
    resume_build_id UUID,
    error           TEXT,                          -- non-null if call failed
    called_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_call_log_called_at  ON agent_call_log(called_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_call_log_provider   ON agent_call_log(provider);
CREATE INDEX IF NOT EXISTS idx_agent_call_log_agent      ON agent_call_log(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_call_log_resume     ON agent_call_log(resume_build_id);


-- ── 2. company_personas ──────────────────────────────────────────────────
-- Auto-synthesized per-company system prompt + ATS keyword bank + success/
-- failure pattern library. Regenerated weekly from outcomes + transcripts.
-- Loaded by G2's Insider Expert node at the start of every resume build.
CREATE TABLE IF NOT EXISTS company_personas (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id               UUID REFERENCES companies(id) ON DELETE CASCADE,
    company_name             TEXT NOT NULL UNIQUE,
    system_prompt_template   TEXT NOT NULL,           -- the actual prompt
    ats_keyword_bank         JSONB DEFAULT '{}'::jsonb,
        -- shape: {required: [...], boost: [...], banned: [...]}
    success_patterns         JSONB DEFAULT '[]'::jsonb,
        -- bullet structures from past resume_outcomes where interview_received=true
    failure_patterns         JSONB DEFAULT '[]'::jsonb,
        -- bullet structures from past resume_outcomes where rejected=true
    last_synthesized_at      TIMESTAMPTZ DEFAULT NOW(),
    n_examples_used          INTEGER DEFAULT 0,
    persona_version          INTEGER DEFAULT 1,
    metadata                 JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_company_personas_company
    ON company_personas(company_name);


-- ── 3. resume_builds ─────────────────────────────────────────────────────
-- One row per G2 invocation (Resume Builder graph run).
-- The agent_transcript jsonb captures every node's input + output for the
-- meta-critic to learn from on subsequent builds for the same company.
CREATE TABLE IF NOT EXISTS resume_builds (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    application_id      UUID REFERENCES applications(id) ON DELETE SET NULL,
    company_name        TEXT,
    persona_version     INTEGER,                     -- which company_personas version was used
    status              TEXT DEFAULT 'running',      -- running | converged | exhausted | failed
    iterations          INTEGER DEFAULT 0,           -- writer ↔ critic loops
    ats_score_a         INTEGER,                     -- DeepSeek-R1 critic score
    ats_score_b         INTEGER,                     -- Kimi K2 critic score
    polisher_score      INTEGER,                     -- final self-score 0-100
    resume_md           TEXT,
    resume_docx_url     TEXT,                        -- Supabase Storage signed URL
    resume_pdf_url      TEXT,
    cover_email_md      TEXT,
    agent_transcript    JSONB DEFAULT '[]'::jsonb,
        -- list of {node, provider, model, input_summary, output, cost_usd, latency_ms}
    cost_usd_total      NUMERIC(10, 4) DEFAULT 0,
    latency_ms_total    INTEGER DEFAULT 0,
    graph_thread_id     TEXT,                        -- LangGraph checkpointer thread id
    error               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    finalized_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_resume_builds_job        ON resume_builds(job_id);
CREATE INDEX IF NOT EXISTS idx_resume_builds_company    ON resume_builds(company_name);
CREATE INDEX IF NOT EXISTS idx_resume_builds_status     ON resume_builds(status);
CREATE INDEX IF NOT EXISTS idx_resume_builds_created_at ON resume_builds(created_at DESC);


-- ── 4. resume_outcomes ───────────────────────────────────────────────────
-- THE learning-loop table. User logs outcomes from the dashboard:
-- "Did you hear back? Did you get an interview?"
-- Without this, persona synthesis has nothing to learn from.
CREATE TABLE IF NOT EXISTS resume_outcomes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resume_build_id         UUID REFERENCES resume_builds(id) ON DELETE CASCADE UNIQUE,
    application_id          UUID REFERENCES applications(id) ON DELETE SET NULL,
    job_id                  INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    company_name            TEXT,
    -- Outcome flags (NULL = not yet known)
    ats_passed              BOOLEAN,                 -- did pre-flight ATS test pass?
    submitted_at            TIMESTAMPTZ,
    recruiter_responded     BOOLEAN,
    recruiter_response_at   TIMESTAMPTZ,
    interview_received      BOOLEAN,
    rounds_reached          INTEGER,                 -- 0..N
    offer_received          BOOLEAN,
    rejected_reason         TEXT,                    -- free text if known
    self_rated_quality      INTEGER,                 -- 1-5 from user
    notes                   TEXT,
    logged_at               TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_resume_outcomes_company
    ON resume_outcomes(company_name);
CREATE INDEX IF NOT EXISTS idx_resume_outcomes_logged_at
    ON resume_outcomes(logged_at DESC);


-- ── 5. ats_test_results ──────────────────────────────────────────────────
-- Pre-flight ATS scores from external APIs (jobscan, resumeworded, etc).
-- Optional but high-signal — lets us measure ATS pass rate before
-- submission and before recruiter feedback exists.
CREATE TABLE IF NOT EXISTS ats_test_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resume_build_id     UUID REFERENCES resume_builds(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL,               -- jobscan | resumeworded | manual
    score               INTEGER,                     -- 0-100
    missing_keywords    TEXT[],
    raw_response        JSONB,
    tested_at           TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ats_test_results_resume
    ON ats_test_results(resume_build_id);


-- ── 6. interview_outcomes ────────────────────────────────────────────────
-- Per-round interview outcomes. Drives the interview prep graph (G3 Stage B).
CREATE TABLE IF NOT EXISTS interview_outcomes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_id      UUID REFERENCES applications(id) ON DELETE CASCADE,
    job_id              INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    company_name        TEXT,
    round_number        INTEGER NOT NULL,            -- 1, 2, 3...
    round_type          TEXT,                        -- recruiter | hm | panel | exec | technical | take_home
    passed              BOOLEAN,
    questions_asked     TEXT[],                      -- as remembered by user
    feedback_text       TEXT,
    interviewer_names   TEXT[],
    conducted_at        TIMESTAMPTZ,
    logged_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(application_id, round_number)
);

CREATE INDEX IF NOT EXISTS idx_interview_outcomes_application
    ON interview_outcomes(application_id);
CREATE INDEX IF NOT EXISTS idx_interview_outcomes_company
    ON interview_outcomes(company_name);


-- ── 7. Triggers — keep updated_at fresh ──────────────────────────────────
-- (uses update_updated_at() from schema.sql)
DROP TRIGGER IF EXISTS update_resume_outcomes_updated_at ON resume_outcomes;
CREATE TRIGGER update_resume_outcomes_updated_at
    BEFORE UPDATE ON resume_outcomes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ── 8. Useful views ──────────────────────────────────────────────────────

-- Daily cost rollup
CREATE OR REPLACE VIEW v_daily_llm_cost AS
SELECT
    DATE(called_at)              AS day,
    provider,
    model,
    COUNT(*)                     AS calls,
    SUM(input_tokens)            AS in_toks,
    SUM(output_tokens)           AS out_toks,
    ROUND(SUM(cost_usd)::numeric, 4) AS total_cost_usd,
    ROUND(AVG(latency_ms)::numeric, 0) AS avg_latency_ms
FROM agent_call_log
GROUP BY 1, 2, 3
ORDER BY 1 DESC, 6 DESC;


-- Per-company resume conversion funnel
CREATE OR REPLACE VIEW v_company_conversion_funnel AS
SELECT
    rb.company_name,
    COUNT(DISTINCT rb.id)                                    AS resumes_built,
    COUNT(DISTINCT ro.id) FILTER (WHERE ro.recruiter_responded = TRUE) AS responses,
    COUNT(DISTINCT ro.id) FILTER (WHERE ro.interview_received = TRUE)  AS interviews,
    COUNT(DISTINCT ro.id) FILTER (WHERE ro.offer_received = TRUE)      AS offers,
    ROUND(AVG(rb.polisher_score)::numeric, 1)                AS avg_polisher_score,
    ROUND(AVG(rb.cost_usd_total)::numeric, 2)                AS avg_cost_usd
FROM resume_builds rb
LEFT JOIN resume_outcomes ro ON ro.resume_build_id = rb.id
WHERE rb.status = 'converged'
GROUP BY 1
ORDER BY interviews DESC NULLS LAST;
