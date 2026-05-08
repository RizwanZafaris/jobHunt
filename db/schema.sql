-- ══════════════════════════════════════════════════════════════════════════
-- Job Hunt v2 — Supabase Schema
-- Run this in your Supabase SQL editor to set up all tables
-- ══════════════════════════════════════════════════════════════════════════

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ── companies ────────────────────────────────────────────────────────────────
-- One row per company. CompanyAgent data lives here.
CREATE TABLE IF NOT EXISTS companies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,
    domain          TEXT,                           -- e.g. tabby.ai
    careers_url     TEXT,
    ats_type        TEXT,                           -- greenhouse | ashby | lever | workday | custom
    country         TEXT,
    industry        TEXT,
    stage           TEXT,                           -- startup | scaleup | enterprise | public
    headcount       TEXT,                           -- rough band: <50 | 50-200 | 200-1000 | 1000+
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── company_knowledge ────────────────────────────────────────────────────────
-- Persistent company intelligence — built once, reused forever.
-- Embeddings stored as pgvector for semantic search.
CREATE TABLE IF NOT EXISTS company_knowledge (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID REFERENCES companies(id) ON DELETE CASCADE,
    company_name    TEXT NOT NULL,
    section         TEXT NOT NULL,  -- overview | news | funding | culture | tech | strategy | products | challenges
    content         TEXT NOT NULL,  -- raw text of the intelligence chunk
    embedding       vector(1536),   -- text-embedding-3-small dimensions
    source_url      TEXT,
    scraped_at      TIMESTAMPTZ DEFAULT NOW(),
    metadata        JSONB DEFAULT '{}'::jsonb,
    UNIQUE (company_name, section)
);

CREATE INDEX IF NOT EXISTS idx_company_knowledge_company_id
    ON company_knowledge(company_id);

CREATE INDEX IF NOT EXISTS idx_company_knowledge_embedding
    ON company_knowledge USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── jobs ──────────────────────────────────────────────────────────────────────
-- All discovered job postings
CREATE TABLE IF NOT EXISTS jobs (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    company_id      UUID REFERENCES companies(id),
    location        TEXT,
    url             TEXT UNIQUE,
    description     TEXT,
    jd_embedding    vector(1536),
    source          TEXT,           -- linkedin | bayt | gulftaler | indeed | ashby | greenhouse | lever
    match_score     INTEGER DEFAULT 0,  -- 0-100 fit score
    fit_details     JSONB DEFAULT '{}'::jsonb,  -- matched_skills, gaps, tailoring_notes
    status          TEXT DEFAULT 'new',  -- new | evaluated | applying | applied | interviewing | offer | rejected | pass
    report_path     TEXT,
    resume_path     TEXT,
    email_path      TEXT,
    interview_path  TEXT,
    discovered_at   TIMESTAMPTZ DEFAULT NOW(),
    applied_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_match_score ON jobs(match_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered_at ON jobs(discovered_at DESC);

-- ── applications ─────────────────────────────────────────────────────────────
-- Full application tracking (mirrors career-ops applications.md in DB)
CREATE TABLE IF NOT EXISTS applications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          INTEGER REFERENCES jobs(id),
    company         TEXT NOT NULL,
    role            TEXT NOT NULL,
    score           NUMERIC(3,1),   -- 0.0 - 5.0 (career-ops A-F → numeric)
    status          TEXT DEFAULT 'Evaluada',
    applied_date    DATE,
    report_url      TEXT,
    pdf_url         TEXT,
    cover_email     TEXT,
    notes           TEXT,
    follow_up_due   DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── rizwan_profile ────────────────────────────────────────────────────────────
-- Rizwan's profile stored as embeddings for semantic retrieval
CREATE TABLE IF NOT EXISTS rizwan_profile (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    section         TEXT NOT NULL UNIQUE,  -- summary | experience | skills | certifications | education | achievements
    content         TEXT NOT NULL,
    embedding       vector(1536),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rizwan_profile_embedding
    ON rizwan_profile USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);

-- ── story_bank ────────────────────────────────────────────────────────────────
-- STAR+R interview stories accumulated over time
CREATE TABLE IF NOT EXISTS story_bank (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    situation       TEXT,
    task            TEXT,
    action          TEXT,
    result          TEXT,
    reflection      TEXT,
    themes          TEXT[],  -- e.g. {leadership, conflict, delivery, product_vision}
    companies_used  TEXT[],  -- track which companies this story was used for
    embedding       vector(1536),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_story_bank_embedding
    ON story_bank USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 10);

-- ── agent_conversations ───────────────────────────────────────────────────────
-- Stores Company ↔ Rizwan dialogue for resume gap-filling
CREATE TABLE IF NOT EXISTS agent_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id          INTEGER REFERENCES jobs(id),
    company         TEXT NOT NULL,
    role            TEXT NOT NULL,
    turn            INTEGER NOT NULL,
    speaker         TEXT NOT NULL,  -- company_agent | rizwan_agent
    message         TEXT NOT NULL,
    gap_identified  TEXT,           -- if company_agent identified a gap
    gap_filled      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── boss_audit_log ─────────────────────────────────────────────────────────────
-- Boss Agent nightly audit results
CREATE TABLE IF NOT EXISTS boss_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    total_companies INTEGER,
    stale_companies INTEGER,
    refreshed       TEXT[],         -- company names refreshed
    jobs_found_today INTEGER,
    jobs_scored_high INTEGER,       -- >= fit_threshold
    applications_sent INTEGER,
    digest_sent     BOOLEAN DEFAULT FALSE,
    digest_content  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── Utility functions ──────────────────────────────────────────────────────────

-- Semantic search: find relevant company knowledge
CREATE OR REPLACE FUNCTION search_company_knowledge(
    query_embedding vector(1536),
    target_company  TEXT,
    match_count     INT DEFAULT 5
)
RETURNS TABLE (
    section TEXT,
    content TEXT,
    similarity FLOAT,
    scraped_at TIMESTAMPTZ
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ck.section,
        ck.content,
        1 - (ck.embedding <=> query_embedding) AS similarity,
        ck.scraped_at
    FROM company_knowledge ck
    WHERE ck.company_name = target_company
      AND ck.embedding IS NOT NULL
    ORDER BY ck.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Semantic search: find relevant Rizwan profile sections
CREATE OR REPLACE FUNCTION search_rizwan_profile(
    query_embedding vector(1536),
    match_count     INT DEFAULT 5
)
RETURNS TABLE (
    section TEXT,
    content TEXT,
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        rp.section,
        rp.content,
        1 - (rp.embedding <=> query_embedding) AS similarity
    FROM rizwan_profile rp
    WHERE rp.embedding IS NOT NULL
    ORDER BY rp.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Find most relevant STAR stories for a given topic
CREATE OR REPLACE FUNCTION search_story_bank(
    query_embedding vector(1536),
    match_count     INT DEFAULT 3
)
RETURNS TABLE (
    title TEXT,
    situation TEXT,
    task TEXT,
    action TEXT,
    result TEXT,
    themes TEXT[],
    similarity FLOAT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        sb.title,
        sb.situation,
        sb.task,
        sb.action,
        sb.result,
        sb.themes,
        1 - (sb.embedding <=> query_embedding) AS similarity
    FROM story_bank sb
    WHERE sb.embedding IS NOT NULL
    ORDER BY sb.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Trigger: update updated_at automatically
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS update_companies_updated_at ON companies;
CREATE TRIGGER update_companies_updated_at
    BEFORE UPDATE ON companies
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_jobs_updated_at ON jobs;
CREATE TRIGGER update_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_applications_updated_at ON applications;
CREATE TRIGGER update_applications_updated_at
    BEFORE UPDATE ON applications
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
