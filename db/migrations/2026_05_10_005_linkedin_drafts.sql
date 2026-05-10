-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_005 — LinkedIn presence engine (P1.2)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Schema for the LinkedIn presence engine: a news-anchored draft
--   generator with a user-approval gate. Three tables:
--
--     linkedin_drafts          — one row per generated draft post
--     linkedin_posting_schedule — per-user cadence + pause window
--     linkedin_voice_profile   — per-user voice prompt + avoid-phrase list
--
--   The engine never auto-posts. V1 is draft → user approves → schedule
--   → "Copy to clipboard" + manual paste. The audit (docs/AUDIT_360_SYNTHESIS.md
--   §12, risk #2) flagged auto-posting as account-ban risk; the draft+approve
--   gate is the mitigation.
--
-- Idempotency
--   Every CREATE / ALTER guarded by IF NOT EXISTS. Re-running is a no-op.
--
-- Apply order
--   001 (multi-tenancy) → 002 (status enum) → 003 (jobs_runs) → 005 (this).
--   We do not depend on 004 (referral graph) — those are independent.
--
-- Rollback
--   DROP TABLE linkedin_drafts;
--   DROP TABLE linkedin_posting_schedule;
--   DROP TABLE linkedin_voice_profile;
--   No application data is shared with other tables; rollback is safe
--   pre-launch.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── extensions ────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ══════════════════════════════════════════════════════════════════════════
-- 1. linkedin_drafts — one row per generated/edited/scheduled/posted draft.
-- ══════════════════════════════════════════════════════════════════════════
-- The lifecycle is captured in the `status` column:
--   draft     — fresh out of the G4 graph, unread by the user
--   approved  — user clicked "Approve & Schedule"; scheduled_for set
--   scheduled — same as approved, but scheduled_for is in the future
--               (kept distinct so dashboards can show "next 7 days")
--   posted    — manual_copy_at set OR Buffer/Hypefury reported success
--   rejected  — user clicked "Reject"; kept for analytics + future evals
--   expired   — scheduled time passed without action; sweeper sets this
--
-- `critique` stores the Sonnet critic's findings as JSON so the dashboard
-- can show a "what we caught" tooltip even after the polish round.
CREATE TABLE IF NOT EXISTS linkedin_drafts (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Source anchoring (the news event the post was tied to). Both nullable
    -- because some angles ("build_in_public", "lesson_learned") may be
    -- timeless and not tied to a specific company / scrape.
    source_company_id    uuid REFERENCES companies(id)         ON DELETE SET NULL,
    source_knowledge_id  uuid REFERENCES company_knowledge(id) ON DELETE SET NULL,

    -- Angle picker output from node_pick_angle (G4).
    angle                text NOT NULL CHECK (
        angle IN ('news_commentary', 'contrarian_take', 'build_in_public',
                  'lesson_learned', 'industry_analysis')
    ),

    -- The post itself, split into the three load-bearing parts.
    hook                 text NOT NULL,
    body                 text NOT NULL,
    cta                  text,                                -- optional call-to-action line
    hashtags             text[] NOT NULL DEFAULT ARRAY[]::text[],

    -- Lifecycle.
    status               text NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'approved', 'scheduled', 'posted', 'rejected', 'expired')
    ),
    critique             jsonb,                               -- Sonnet's review (severity-tagged)
    polish_round         integer NOT NULL DEFAULT 0,          -- how many polish iterations consumed
    scheduled_for        timestamptz,
    posted_at            timestamptz,
    posted_url           text,
    manual_copy_at       timestamptz,                         -- "Copy to clipboard" timestamp (V1 publish path)
    engagement_metrics   jsonb,                               -- {likes, comments, views, ts} populated later

    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);

-- Hot path: scheduler sweeper runs every 30 min looking for approved
-- drafts whose scheduled_for is within ±15 min of now(). The composite
-- index makes that pinpoint cheap.
CREATE INDEX IF NOT EXISTS linkedin_drafts_user_status_sched_idx
    ON linkedin_drafts (user_id, status, scheduled_for);

-- Hot path: dashboard list page shows "newest first per user".
CREATE INDEX IF NOT EXISTS linkedin_drafts_user_created_idx
    ON linkedin_drafts (user_id, created_at DESC);

-- Optional: knowledge-row joins for "drafts anchored to this news".
CREATE INDEX IF NOT EXISTS linkedin_drafts_source_knowledge_idx
    ON linkedin_drafts (source_knowledge_id)
    WHERE source_knowledge_id IS NOT NULL;

-- updated_at trigger.
DROP TRIGGER IF EXISTS update_linkedin_drafts_updated_at ON linkedin_drafts;
CREATE TRIGGER update_linkedin_drafts_updated_at
    BEFORE UPDATE ON linkedin_drafts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ══════════════════════════════════════════════════════════════════════════
-- 2. linkedin_posting_schedule — per-user cadence + pause window.
-- ══════════════════════════════════════════════════════════════════════════
-- One row per (user, day_of_week) that the user wants a slot. Defaults
-- (seeded below) are 3 posts/week on Mon/Wed/Fri 09:00. Users edit via
-- the dashboard.
--
-- `posts_per_week` is denormalised onto each row so the API can read it
-- in one shot; the trigger below keeps all rows for a user in sync.
--
-- `pause_until` lets the user pause posting (e.g. while interviewing)
-- without losing their schedule.
CREATE TABLE IF NOT EXISTS linkedin_posting_schedule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day_of_week     integer NOT NULL CHECK (day_of_week BETWEEN 0 AND 6), -- 0=Sun .. 6=Sat
    time_of_day     time   NOT NULL DEFAULT '09:00:00',
    posts_per_week  integer NOT NULL DEFAULT 3 CHECK (posts_per_week BETWEEN 0 AND 7),
    pause_until     timestamptz,
    paused_reason   text,
    created_at      timestamptz NOT NULL DEFAULT now(),

    UNIQUE (user_id, day_of_week)
);

CREATE INDEX IF NOT EXISTS linkedin_posting_schedule_user_idx
    ON linkedin_posting_schedule (user_id);

-- ══════════════════════════════════════════════════════════════════════════
-- 3. linkedin_voice_profile — per-user voice prompt + avoid-phrase list.
-- ══════════════════════════════════════════════════════════════════════════
-- One row per user. Seeded by agents/linkedin_voice_extractor.py from
-- cv.md on first generate. Users edit via PUT /linkedin/voice-profile.
--
-- `avoid_phrases` defaults to the AI-tell list the audit calls out
-- (delve, tapestry, unpack, etc) — node_critique uses this list directly.
-- `example_posts` lets the user paste 1-3 posts they admire so the
-- writer node can mimic cadence/tone.
CREATE TABLE IF NOT EXISTS linkedin_voice_profile (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    profile_md       text,
    tone_directives  text NOT NULL DEFAULT
        'plainspoken, specific, opinionated; never humble-brags; uses concrete metrics; avoids em-dashes-everywhere AI tells',
    avoid_phrases    text[] NOT NULL DEFAULT
        ARRAY['delve','tapestry','unpack','journey','at the end of the day','a testament to']::text[],
    example_posts    text[] NOT NULL DEFAULT ARRAY[]::text[],
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS update_linkedin_voice_profile_updated_at ON linkedin_voice_profile;
CREATE TRIGGER update_linkedin_voice_profile_updated_at
    BEFORE UPDATE ON linkedin_voice_profile
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ══════════════════════════════════════════════════════════════════════════
-- RLS — every table is per-user. service-role bypasses; api/server.py
-- uses service role today, so application code is responsible for the
-- user_id filter. The policies below kick in once the dashboard talks
-- to Supabase with a user JWT directly.
-- ══════════════════════════════════════════════════════════════════════════
ALTER TABLE linkedin_drafts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE linkedin_posting_schedule  ENABLE ROW LEVEL SECURITY;
ALTER TABLE linkedin_voice_profile     ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['linkedin_drafts','linkedin_posting_schedule','linkedin_voice_profile']
    LOOP
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
    END LOOP;
END
$$;

-- ══════════════════════════════════════════════════════════════════════════
-- Default schedule for the seeded user (Rizwan).
-- 3 posts/week on Mon (1) / Wed (3) / Fri (5) at 09:00 local. The seed is
-- idempotent — ON CONFLICT DO NOTHING uses the (user_id, day_of_week)
-- unique constraint.
-- ══════════════════════════════════════════════════════════════════════════
INSERT INTO linkedin_posting_schedule (user_id, day_of_week, time_of_day, posts_per_week)
VALUES
    ('00000000-0000-0000-0000-000000000001'::uuid, 1, '09:00:00', 3),
    ('00000000-0000-0000-0000-000000000001'::uuid, 3, '09:00:00', 3),
    ('00000000-0000-0000-0000-000000000001'::uuid, 5, '09:00:00', 3)
ON CONFLICT (user_id, day_of_week) DO NOTHING;

-- ── reload PostgREST schema cache (Supabase) ──────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
