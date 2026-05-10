-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_001 — Multi-tenancy foundation
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Pivot jobHunt from single-tenant (one user: Rizwan) to multi-tenant
--   SaaS. Adds `users` and `orgs` tables, scopes per-tenant tables by
--   user_id (+ optional org_id), and turns on Postgres RLS.
--
-- Idempotency
--   Every CREATE / ALTER is guarded by IF NOT EXISTS or by a defensive
--   DO-block that checks for prior state. Re-running is a no-op.
--
-- Apply order
--   001 (this file) → 002 (status enum) → 003 (queue worker, separate agent)
--
-- Rollback
--   See db/migrations/README.md. Dropping users/orgs is destructive; do not
--   run rollback against production after rows have been written.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── extensions ────────────────────────────────────────────────────────────
-- pgcrypto provides gen_random_uuid(); Supabase ships it but enable defensively.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── users table ───────────────────────────────────────────────────────────
-- Application-level users table. Mirrors auth.users for our app-specific
-- columns (plan, is_admin, profile links). The seed (db/seeds/user_001.sql)
-- inserts Rizwan with a stable id used as the backfill default below.
CREATE TABLE IF NOT EXISTS users (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT        NOT NULL UNIQUE,
    full_name       TEXT,
    linkedin_url    TEXT,
    github_url      TEXT,
    plan            TEXT        NOT NULL DEFAULT 'lifetime',
    is_admin        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ── orgs table ────────────────────────────────────────────────────────────
-- Optional B2B grouping. `org_id` on per-tenant tables is nullable; a row
-- with org_id IS NULL is owned solely by its user.
CREATE TABLE IF NOT EXISTS orgs (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    plan            TEXT        NOT NULL DEFAULT 'free',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── seed user_001 (Rizwan) — must exist BEFORE FK constraints below ───────
-- Inlined here (not the separate seed file) because the DO block below adds
-- foreign keys that reference users(id), backfilled to this id. If user_001
-- doesn't exist when the FK is created, the migration fails. Idempotent via
-- ON CONFLICT, so re-running is a no-op. Source: cv.md.
INSERT INTO users (id, email, full_name, linkedin_url, github_url, plan, is_admin, created_at, updated_at)
VALUES (
    '00000000-0000-0000-0000-000000000001'::uuid,
    'rizwanzaffar.pk@gmail.com',
    'Rizwan Zafar',
    'https://linkedin.com/in/rizwanzafar',
    NULL,
    'lifetime',
    TRUE,
    NOW(),
    NOW()
)
ON CONFLICT (id) DO NOTHING;

-- ── per-tenant column additions ───────────────────────────────────────────
-- We add `user_id` (NOT NULL after backfill) and `org_id` (nullable) to
-- every per-tenant table. The temporary default points at Rizwan's seeded
-- id so existing rows backfill cleanly; we drop the default at the end.
--
-- Tables tenant-ized by this migration (Option A — extended 2026-05-10
-- after Supabase MCP inventory revealed the full 22-table production schema):
--
--   Core (per audit P0.1):
--     jobs, applications, company_personas, company_knowledge
--   Target list & cost telemetry:
--     companies, target_companies, costs, agent_call_log, agent_conversations
--   User-owned content & artefacts:
--     rizwan_profile, story_bank, resume_builds, resume_outcomes,
--     ats_test_results, interview_outcomes, interview_prep
--   Profile system (RLS already on; we add user_id + concrete policies):
--     profile_master, profile_experience, profile_certification,
--     profile_education, profile_keyword, profile_keyword_category,
--     profile_source_document, profile_recommendation
--   Reserved future:
--     personas (defensive — handles a future bare table), outreach
--   Intentionally NOT tenant-ized (admin/global):
--     boss_audit_log — admin-only operational log.
--
-- Notes on naming:
--   * `personas` is materialised as `company_personas`. We add columns to
--     `company_personas` here and skip the bare `personas` cleanly if absent.
--   * Tables that don't yet exist (target_companies, costs, outreach) are
--     skipped via information_schema lookup; re-run is idempotent.
--   * `profile_keyword_category` has TEXT pk (`category`); adding user_id
--     is fine — the existing pk is preserved.

DO $mt$
DECLARE
    -- Backfill default → Rizwan (inserted above).
    backfill_uid CONSTANT UUID := '00000000-0000-0000-0000-000000000001'::uuid;
    target_tables CONSTANT TEXT[] := ARRAY[
        -- Core
        'jobs',
        'applications',
        'company_personas',   -- canonical name for "personas"
        'personas',           -- defensive: handles a future bare table too
        'company_knowledge',
        -- Target list + cost telemetry
        'companies',
        'target_companies',
        'costs',
        'agent_call_log',
        'agent_conversations',
        -- User-owned content & artefacts
        'rizwan_profile',
        'story_bank',
        'resume_builds',
        'resume_outcomes',
        'ats_test_results',
        'interview_outcomes',
        'interview_prep',
        -- Profile system (RLS already on; we add user_id + real policies)
        'profile_master',
        'profile_experience',
        'profile_certification',
        'profile_education',
        'profile_keyword',
        'profile_keyword_category',
        'profile_source_document',
        'profile_recommendation',
        -- Reserved future
        'outreach'
    ];
    t TEXT;
BEGIN
    FOREACH t IN ARRAY target_tables
    LOOP
        -- Skip tables that don't exist yet (target_companies, costs,
        -- outreach are not authored yet; same for `personas` if only
        -- `company_personas` exists). The migration is re-runnable, so
        -- once those tables are created, applying again will fill them in.
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) THEN
            RAISE NOTICE 'skip: table public.% does not exist yet', t;
            CONTINUE;
        END IF;

        -- Add user_id with a temporary default so existing rows backfill.
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS user_id UUID NOT NULL DEFAULT %L',
            t, backfill_uid
        );
        -- Add org_id (nullable, no default).
        EXECUTE format(
            'ALTER TABLE %I ADD COLUMN IF NOT EXISTS org_id UUID',
            t
        );

        -- Drop the default *after* backfill so future inserts must supply it.
        EXECUTE format('ALTER TABLE %I ALTER COLUMN user_id DROP DEFAULT', t);

        -- Foreign keys. Use IF NOT EXISTS-style guard via pg_constraint lookup.
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = format('%s_user_id_fkey', t)
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I '
                'FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE',
                t, format('%s_user_id_fkey', t)
            );
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = format('%s_org_id_fkey', t)
        ) THEN
            EXECUTE format(
                'ALTER TABLE %I ADD CONSTRAINT %I '
                'FOREIGN KEY (org_id) REFERENCES orgs(id) ON DELETE SET NULL',
                t, format('%s_org_id_fkey', t)
            );
        END IF;

        -- btree index on user_id (fast tenant filter for every query).
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I(user_id)',
            format('idx_%s_user_id', t), t
        );
        -- Bonus: also index org_id since /orgs/<id>/... queries will hit it.
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I(org_id)',
            format('idx_%s_org_id', t), t
        );

        -- Enable RLS. Service-role connections bypass RLS automatically;
        -- this is the desired behaviour for api/server.py for now.
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);

        -- ── Policies ──────────────────────────────────────────────────────
        -- Drop-then-create is idempotent; a CREATE POLICY IF NOT EXISTS
        -- doesn't exist in Postgres, so we do it the manual way.
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_select_own', t), t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_insert_own', t), t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_update_own', t), t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_delete_own', t), t);

        EXECUTE format(
            'CREATE POLICY %I ON %I FOR SELECT USING (auth.uid() = user_id)',
            format('%s_select_own', t), t
        );
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR INSERT WITH CHECK (auth.uid() = user_id)',
            format('%s_insert_own', t), t
        );
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR UPDATE USING (auth.uid() = user_id) '
            'WITH CHECK (auth.uid() = user_id)',
            format('%s_update_own', t), t
        );
        EXECUTE format(
            'CREATE POLICY %I ON %I FOR DELETE USING (auth.uid() = user_id)',
            format('%s_delete_own', t), t
        );

        -- service-role bypasses RLS; api/server.py uses service role for now
        EXECUTE format(
            $cmt$COMMENT ON POLICY %I ON %I IS 'service-role bypasses RLS; api/server.py uses service role for now'$cmt$,
            format('%s_select_own', t), t
        );
        EXECUTE format(
            $cmt$COMMENT ON POLICY %I ON %I IS 'service-role bypasses RLS; api/server.py uses service role for now'$cmt$,
            format('%s_insert_own', t), t
        );
        EXECUTE format(
            $cmt$COMMENT ON POLICY %I ON %I IS 'service-role bypasses RLS; api/server.py uses service role for now'$cmt$,
            format('%s_update_own', t), t
        );
        EXECUTE format(
            $cmt$COMMENT ON POLICY %I ON %I IS 'service-role bypasses RLS; api/server.py uses service role for now'$cmt$,
            format('%s_delete_own', t), t
        );

        RAISE NOTICE 'tenancy applied: public.%', t;
    END LOOP;
END
$mt$;

-- ── users / orgs RLS ──────────────────────────────────────────────────────
-- Users see only their own row; admins (is_admin = TRUE) can see all rows
-- via service role only — we deliberately do NOT add an "admin sees all"
-- policy here, because the dashboard calls service role for admin paths.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE orgs  ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS users_select_self ON users;
DROP POLICY IF EXISTS users_update_self ON users;
CREATE POLICY users_select_self ON users
    FOR SELECT USING (auth.uid() = id);
CREATE POLICY users_update_self ON users
    FOR UPDATE USING (auth.uid() = id) WITH CHECK (auth.uid() = id);

COMMENT ON POLICY users_select_self ON users IS
    'service-role bypasses RLS; api/server.py uses service role for now';
COMMENT ON POLICY users_update_self ON users IS
    'service-role bypasses RLS; api/server.py uses service role for now';

-- orgs: members may select; writes go through service role only.
DROP POLICY IF EXISTS orgs_select_member ON orgs;
CREATE POLICY orgs_select_member ON orgs
    FOR SELECT USING (
        -- A user is "in" an org iff any of their tenant rows points at it.
        -- Cheap and good enough until we add an explicit org_members table.
        EXISTS (
            SELECT 1 FROM jobs WHERE jobs.org_id = orgs.id AND jobs.user_id = auth.uid()
        )
    );
COMMENT ON POLICY orgs_select_member ON orgs IS
    'service-role bypasses RLS; api/server.py uses service role for now';

-- ── updated_at trigger on users ───────────────────────────────────────────
-- Reuses the update_updated_at() function declared in db/schema.sql.
DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── reload PostgREST schema cache (Supabase) ──────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
