-- ══════════════════════════════════════════════════════════════════════════
-- Profile Foundation Schema — Phase A
-- Adds profile_* tables for the Master Profile and Keyword Intelligence dashboards.
-- Safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════

-- ── profile_master ────────────────────────────────────────────────────────
-- Single-row table holding the top-level identity + summary.
CREATE TABLE IF NOT EXISTS profile_master (
    id              INTEGER PRIMARY KEY DEFAULT 1,
    name            TEXT NOT NULL,
    headline        TEXT,
    summary         TEXT,
    location        TEXT,
    email           TEXT,
    phones          TEXT[],
    linkedin_url    TEXT,
    core_competencies TEXT[],
    technical_knowledge TEXT[],
    languages       JSONB DEFAULT '[]'::jsonb,
    ai_solutions    JSONB DEFAULT '[]'::jsonb,
    tailored_resumes_count INTEGER DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT singleton CHECK (id = 1)
);

-- ── profile_experience ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_experience (
    id              SERIAL PRIMARY KEY,
    sort_order      INTEGER NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT,
    scope           TEXT,
    dates           TEXT,
    summary         TEXT,
    highlights      TEXT[],
    groups          JSONB DEFAULT '[]'::jsonb,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profile_experience_sort ON profile_experience(sort_order);

-- ── profile_certification ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_certification (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    full_name       TEXT,
    issuer          TEXT,
    year            INTEGER,
    sort_order      INTEGER DEFAULT 100
);

-- ── profile_education ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile_education (
    id              SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    details         TEXT,
    year            TEXT,
    notes           TEXT,
    sort_order      INTEGER DEFAULT 100
);

-- ── profile_keyword ───────────────────────────────────────────────────────
-- The output of the keyword extractor. One row per (keyword, category).
CREATE TABLE IF NOT EXISTS profile_keyword (
    id              SERIAL PRIMARY KEY,
    keyword         TEXT NOT NULL,
    category        TEXT NOT NULL,
    total_occurrences INTEGER DEFAULT 0,
    files_count     INTEGER DEFAULT 0,
    coverage_pct    NUMERIC(5,2) DEFAULT 0,
    avg_per_file    NUMERIC(6,2) DEFAULT 0,
    ats_strength    NUMERIC(6,2) DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (keyword, category)
);

CREATE INDEX IF NOT EXISTS idx_profile_keyword_category ON profile_keyword(category);
CREATE INDEX IF NOT EXISTS idx_profile_keyword_strength ON profile_keyword(ats_strength DESC);

-- ── profile_keyword_category ──────────────────────────────────────────────
-- Per-category aggregates for the dashboard.
CREATE TABLE IF NOT EXISTS profile_keyword_category (
    category        TEXT PRIMARY KEY,
    keyword_count   INTEGER DEFAULT 0,
    total_occurrences INTEGER DEFAULT 0,
    avg_strength    NUMERIC(5,1) DEFAULT 0,
    top_keywords    TEXT[],
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ── profile_source_document ───────────────────────────────────────────────
-- Registry of all parsed source files (resumes, LinkedIn docs, etc.)
CREATE TABLE IF NOT EXISTS profile_source_document (
    id              SERIAL PRIMARY KEY,
    file_hash       TEXT NOT NULL UNIQUE,
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    document_class  TEXT NOT NULL,
    char_count      INTEGER DEFAULT 0,
    file_size       INTEGER DEFAULT 0,
    source_mtime    TIMESTAMPTZ,
    is_duplicate    BOOLEAN DEFAULT FALSE,
    parsed_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_source_doc_class ON profile_source_document(document_class);

-- ── profile_recommendation ────────────────────────────────────────────────
-- AI-generated suggestions: missing keywords, weak categories, conflicts.
CREATE TABLE IF NOT EXISTS profile_recommendation (
    id              SERIAL PRIMARY KEY,
    kind            TEXT NOT NULL,  -- missing_keyword | weak_category | conflict | quantify
    severity        TEXT DEFAULT 'medium',  -- low | medium | high
    title           TEXT NOT NULL,
    detail          TEXT,
    related_keyword TEXT,
    related_category TEXT,
    dismissed       BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recommendation_kind ON profile_recommendation(kind);

-- ── updated_at triggers ───────────────────────────────────────────────────
DROP TRIGGER IF EXISTS update_profile_master_updated_at ON profile_master;
CREATE TRIGGER update_profile_master_updated_at
    BEFORE UPDATE ON profile_master
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_profile_experience_updated_at ON profile_experience;
CREATE TRIGGER update_profile_experience_updated_at
    BEFORE UPDATE ON profile_experience
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_profile_keyword_updated_at ON profile_keyword;
CREATE TRIGGER update_profile_keyword_updated_at
    BEFORE UPDATE ON profile_keyword
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
