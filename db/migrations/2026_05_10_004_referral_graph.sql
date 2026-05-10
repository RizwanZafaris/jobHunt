-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_004 — Referral graph (P1.1)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Schema spine for the warm-introduction path-finder. This is half of
--   the stated product vision (audit ref docs/AUDIT_360_SYNTHESIS.md
--   §4 P1.1). Today the graph is 5% built — we have `companies` and
--   `target_companies` but zero schema for people, edges, employments,
--   or path-finding.
--
--   This migration adds:
--     - people                   — anyone in the user's network
--     - employments              — person ↔ company over time
--     - edges                    — typed, weighted connections between people
--     - target_company_employees — denormalised cache: target → employee for
--                                  the path-finder. Refreshed by a worker
--                                  job (agents/referral_graph.py
--                                  ::populate_target_company_employees).
--
-- Multi-tenancy
--   Every table carries `user_id uuid not null references users(id)`.
--   `employments` inherits via `people.user_id` (no direct user_id column,
--   only person_id) — its RLS policy joins on people.
--
-- Idempotency
--   Every CREATE / ALTER guarded by IF NOT EXISTS or pg_constraint check.
--   Re-running is a no-op.
--
-- Apply order
--   001 (multi-tenancy) → 002 (status enum) → 003 (jobs_runs) → 004 (this)
--
-- Privacy stance (V1)
--   - V1 supports manual CSV import of LinkedIn connections only.
--   - No scraping. No third-party network ingestion.
--   - All rows are scoped to the importing user; service-role bypass is
--     intentional for the worker pool only.
--   - See api/NETWORK.md "Privacy & TOS" for the full stance.
--
-- Rollback
--   DROP TABLE target_company_employees;
--   DROP TABLE edges;
--   DROP TABLE employments;
--   DROP TABLE people;
--   (Destructive — only safe before any user has imported a network.)
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── extensions ────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;
-- pg_trgm powers fuzzy company-name matching in
-- populate_target_company_employees() (handles "Stripe" vs "Stripe, Inc.").
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ══════════════════════════════════════════════════════════════════════════
-- people — anyone in the user's network
-- ══════════════════════════════════════════════════════════════════════════
-- A row tagged source='me' is the user's own node — the path-finder roots
-- here. Only one such row exists per user_id; agents/referral_graph.py
-- guarantees this on import. `linkedin_url` is the natural unique key
-- when present (people.linkedin_url is null for manually-added contacts).

CREATE TABLE IF NOT EXISTS people (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    full_name           TEXT        NOT NULL,
    linkedin_url        TEXT,
    linkedin_handle     TEXT,
    email               TEXT,
    headline            TEXT,
    source              TEXT        CHECK (source IN (
                                        'me',
                                        'manual',
                                        'linkedin_csv',
                                        'google_contacts',
                                        'apify_scrape',
                                        'inferred',
                                        'introduction'
                                    )),
    notes               TEXT,
    profile_picture_url TEXT,
    location            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Same LinkedIn URL twice for the same user collapses to one row.
-- Partial unique allows multiple NULLs (rows where we don't have the URL).
CREATE UNIQUE INDEX IF NOT EXISTS people_user_linkedin_url_uidx
    ON people (user_id, linkedin_url)
    WHERE linkedin_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS people_user_id_idx          ON people (user_id);
CREATE INDEX IF NOT EXISTS people_user_full_name_idx   ON people (user_id, full_name);
-- Trigram index for the "search people by name" endpoint.
CREATE INDEX IF NOT EXISTS people_full_name_trgm_idx
    ON people USING gin (full_name gin_trgm_ops);

-- updated_at trigger (reuses update_updated_at() declared in db/schema.sql).
DROP TRIGGER IF EXISTS update_people_updated_at ON people;
CREATE TRIGGER update_people_updated_at
    BEFORE UPDATE ON people
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── RLS: people ───────────────────────────────────────────────────────────
-- service-role bypasses RLS; api/server.py uses service role for now
ALTER TABLE people ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS people_select_own ON people;
DROP POLICY IF EXISTS people_insert_own ON people;
DROP POLICY IF EXISTS people_update_own ON people;
DROP POLICY IF EXISTS people_delete_own ON people;

CREATE POLICY people_select_own ON people
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY people_insert_own ON people
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY people_update_own ON people
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY people_delete_own ON people
    FOR DELETE USING (auth.uid() = user_id);


-- ══════════════════════════════════════════════════════════════════════════
-- employments — person ↔ company over time
-- ══════════════════════════════════════════════════════════════════════════
-- `company_id` references companies(id) WHEN we recognise the company; many
-- people in the user's network work at companies we don't track. So we
-- always denormalise `company_name` and treat company_id as a soft FK that
-- the worker fills in later if it can.
--
-- `is_current` is a stored generated column — null ended_at means current.
-- A partial index on (person_id) WHERE is_current speeds up the
-- "find current employer" hot path used by the path-finder.

CREATE TABLE IF NOT EXISTS employments (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id       UUID        NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    company_id      UUID        REFERENCES companies(id),
    company_name    TEXT        NOT NULL,
    role            TEXT,
    role_seniority  TEXT        CHECK (role_seniority IN (
                                    'ic',
                                    'senior_ic',
                                    'staff',
                                    'principal',
                                    'manager',
                                    'senior_manager',
                                    'director',
                                    'vp',
                                    'c_level'
                                )),
    started_at      DATE,
    ended_at        DATE,
    is_current      BOOLEAN     GENERATED ALWAYS AS (ended_at IS NULL) STORED,
    source          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS employments_person_id_idx ON employments (person_id);
-- Hot path: "what is X's current employer?" — partial index keeps it tiny.
CREATE INDEX IF NOT EXISTS employments_person_current_idx
    ON employments (person_id)
    WHERE is_current;
-- Used by populate_target_company_employees to scan all current rows
-- and fuzzy-match company_name → target_companies.
CREATE INDEX IF NOT EXISTS employments_current_company_idx
    ON employments (company_name)
    WHERE is_current;
-- pg_trgm index on company_name powers the fuzzy match in the worker.
CREATE INDEX IF NOT EXISTS employments_company_name_trgm_idx
    ON employments USING gin (company_name gin_trgm_ops);

-- ── RLS: employments ──────────────────────────────────────────────────────
-- employments has no direct user_id column; we route through people.user_id.
-- This is a JOIN-based policy.
-- service-role bypasses RLS; api/server.py uses service role for now
ALTER TABLE employments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS employments_select_own ON employments;
DROP POLICY IF EXISTS employments_insert_own ON employments;
DROP POLICY IF EXISTS employments_update_own ON employments;
DROP POLICY IF EXISTS employments_delete_own ON employments;

CREATE POLICY employments_select_own ON employments
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM people p
            WHERE p.id = employments.person_id
              AND p.user_id = auth.uid()
        )
    );
CREATE POLICY employments_insert_own ON employments
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM people p
            WHERE p.id = employments.person_id
              AND p.user_id = auth.uid()
        )
    );
CREATE POLICY employments_update_own ON employments
    FOR UPDATE USING (
        EXISTS (
            SELECT 1 FROM people p
            WHERE p.id = employments.person_id
              AND p.user_id = auth.uid()
        )
    ) WITH CHECK (
        EXISTS (
            SELECT 1 FROM people p
            WHERE p.id = employments.person_id
              AND p.user_id = auth.uid()
        )
    );
CREATE POLICY employments_delete_own ON employments
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM people p
            WHERE p.id = employments.person_id
              AND p.user_id = auth.uid()
        )
    );


-- ══════════════════════════════════════════════════════════════════════════
-- edges — typed, weighted connections between people
-- ══════════════════════════════════════════════════════════════════════════
-- A directed edge "src_person knows dst_person via <kind> with strength s".
-- The path-finder builds an in-memory NetworkX DiGraph from these rows and
-- runs Dijkstra with edge weight = (1 - strength) so the strongest edges
-- become the shortest paths.
--
-- kind values:
--   me_first_degree         — from user's own row to a directly-connected person
--   colleague               — same employer overlap
--   classmate               — same school
--   friend                  — non-professional but real
--   family
--   introduced_by           — third party introduced src to dst
--   same_company_overlap    — auto-inferred from employment intersection
--   referenced_in_outreach  — recruiter chain / "Bob said you should reach out"
--
-- evidence is a free-form jsonb (e.g. {"source":"linkedin_csv","row":42}).

CREATE TABLE IF NOT EXISTS edges (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    src_person  UUID        NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    dst_person  UUID        NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    kind        TEXT        NOT NULL CHECK (kind IN (
                                'me_first_degree',
                                'colleague',
                                'classmate',
                                'friend',
                                'family',
                                'introduced_by',
                                'same_company_overlap',
                                'referenced_in_outreach'
                            )),
    strength    REAL        NOT NULL DEFAULT 0.5
                            CHECK (strength >= 0 AND strength <= 1),
    evidence    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Sanity: don't allow self-edges.
    CHECK (src_person <> dst_person)
);

-- One typed edge per (src, dst, kind) per user.
CREATE UNIQUE INDEX IF NOT EXISTS edges_user_src_dst_kind_uidx
    ON edges (user_id, src_person, dst_person, kind);

CREATE INDEX IF NOT EXISTS edges_user_id_idx       ON edges (user_id);
CREATE INDEX IF NOT EXISTS edges_user_src_idx      ON edges (user_id, src_person);
CREATE INDEX IF NOT EXISTS edges_user_dst_idx      ON edges (user_id, dst_person);

-- ── RLS: edges ────────────────────────────────────────────────────────────
-- service-role bypasses RLS; api/server.py uses service role for now
ALTER TABLE edges ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS edges_select_own ON edges;
DROP POLICY IF EXISTS edges_insert_own ON edges;
DROP POLICY IF EXISTS edges_update_own ON edges;
DROP POLICY IF EXISTS edges_delete_own ON edges;

CREATE POLICY edges_select_own ON edges
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY edges_insert_own ON edges
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY edges_update_own ON edges
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY edges_delete_own ON edges
    FOR DELETE USING (auth.uid() = user_id);


-- ══════════════════════════════════════════════════════════════════════════
-- target_company_employees — denormalised cache for the path-finder
-- ══════════════════════════════════════════════════════════════════════════
-- This is a TABLE (not a materialized view) so we can refresh it from the
-- worker at well-defined times (after CSV import, on demand, on cron).
-- Columns:
--   target_company_id  — FK into target_companies
--   person_id          — FK into people (the employee)
--   role               — denormalised from employments.role at refresh time
--   role_seniority     — denormalised
--   current_since      — employments.started_at
--   last_seen_at       — when this row was last refreshed by the worker
--
-- One row per (target_company_id, person_id). The worker upserts on that
-- composite. Stale rows for a target are pruned per refresh.

CREATE TABLE IF NOT EXISTS target_company_employees (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id            UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_company_id  UUID        NOT NULL,  -- FK added below if table exists
    person_id          UUID        NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role               TEXT,
    role_seniority     TEXT,
    current_since      DATE,
    last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS tce_target_person_uidx
    ON target_company_employees (target_company_id, person_id);

CREATE INDEX IF NOT EXISTS tce_user_id_idx       ON target_company_employees (user_id);
CREATE INDEX IF NOT EXISTS tce_user_target_idx
    ON target_company_employees (user_id, target_company_id);
CREATE INDEX IF NOT EXISTS tce_person_id_idx     ON target_company_employees (person_id);

-- target_companies may not exist yet in fresh installs; only add the FK if
-- the table is present. The migration is re-runnable, so when the table
-- arrives the FK is added automatically on the next apply.
DO $tce$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'target_companies'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tce_target_company_id_fkey'
    ) THEN
        EXECUTE 'ALTER TABLE target_company_employees '
                'ADD CONSTRAINT tce_target_company_id_fkey '
                'FOREIGN KEY (target_company_id) REFERENCES target_companies(id) '
                'ON DELETE CASCADE';
    END IF;
END
$tce$;

-- ── RLS: target_company_employees ─────────────────────────────────────────
-- service-role bypasses RLS; api/server.py uses service role for now
ALTER TABLE target_company_employees ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS target_company_employees_select_own ON target_company_employees;
DROP POLICY IF EXISTS target_company_employees_insert_own ON target_company_employees;
DROP POLICY IF EXISTS target_company_employees_update_own ON target_company_employees;
DROP POLICY IF EXISTS target_company_employees_delete_own ON target_company_employees;

CREATE POLICY target_company_employees_select_own ON target_company_employees
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY target_company_employees_insert_own ON target_company_employees
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY target_company_employees_update_own ON target_company_employees
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY target_company_employees_delete_own ON target_company_employees
    FOR DELETE USING (auth.uid() = user_id);


-- ── reload PostgREST schema cache (Supabase) ──────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
