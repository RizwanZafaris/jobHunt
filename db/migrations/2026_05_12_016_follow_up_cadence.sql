-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_016 — G6 Follow-up Cadence table
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Phase 1.3 (G6 Follow-up Graph). The user has ~60 in-flight applications
--   with zero follow-ups today. ~30% of callbacks come from follow-ups.
--   This migration adds the persistence layer for the daily 09:00 cron
--   that drafts a follow-up for every active application — gated by HITL
--   approval before any send.
--
--   Numbering note: 015 was reserved for the sibling Phase 1.2 story_bank
--   work which has not landed at the time of this commit; this migration
--   takes 016 by explicit instruction in the roadmap (Phase 1.3 §1) so the
--   two phases can land independently without renumbering. 015 will be
--   filled in when story_bank ships.
--
-- Schema
--   follow_up_cadence — one row per (application, follow-up attempt). The
--   "current" state for an application is the most-recent row by
--   created_at DESC.
--
-- Indexes
--   idx_follow_up_user_urgency  — the /workspace/follow-ups queue query
--                                 (user_id + urgency + next date).
--   idx_follow_up_application   — fast lookup of the latest row per app.
--   idx_follow_up_outcome       — outcome attribution (Phase 3 credit
--                                 assignment back to company_persona).
--
-- RLS
--   Permissive policy: the row is visible/writable when
--   user_id = auth.uid() OR auth.uid() IS NULL. The OR-NULL branch keeps
--   the service-role key (no auth.uid()) working for the cron + worker
--   write paths — matches the pattern in 001_multi_tenancy.sql.
--
-- Rollback
--   DROP TABLE follow_up_cadence;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS follow_up_cadence (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID REFERENCES profiles(id) NOT NULL,
    application_id              BIGINT REFERENCES applications(id) NOT NULL,

    -- Snapshot of the application status at draft time. Stored separately
    -- from applications.status so the cadence row remains stable even when
    -- the application moves on (e.g. application transitions to
    -- 'interviewing' two days after we drafted an 'applied' follow-up).
    current_status              TEXT NOT NULL,

    -- 0-indexed; the Nth follow-up. New row per attempt, so a single
    -- application may have multiple rows over its lifetime.
    follow_up_count             INT NOT NULL DEFAULT 0,

    -- When the user last contacted the recruiter (NOT when we drafted the
    -- email — that's created_at). NULL until the user marks "approve" /
    -- "sent".
    last_contact_date           TIMESTAMPTZ,

    -- When the next follow-up should be drafted. Used by /today queue
    -- ordering and the cron's eligibility filter.
    next_follow_up_date         TIMESTAMPTZ,

    -- One of: 'URGENT' | 'OVERDUE' | 'waiting' | 'COLD'. The COLD value
    -- triggers an applications.status='withdrawn' transition (max
    -- follow-ups exhausted) once the user confirms via /skip.
    urgency                     TEXT,

    -- The draft email body (markdown). Surfaced to the user via /workspace/
    -- follow-ups; copy-paste-friendly. The user clicks "approve", we record
    -- last_contact_date=NOW(), and they send from their own client.
    draft_email                 TEXT,

    -- Phase 3 outcome attribution: each citation in the draft carries a
    -- cite:knowledge_id=<uuid> back to a company_knowledge row (RAG) OR a
    -- linkedin_drafts row (personal win). Shape:
    --   [{"kind": "knowledge", "id": "uuid"}, {"kind": "linkedin", "id": "uuid"}]
    draft_cites                 JSONB,

    -- Output from the persona_critic + tone_calibrator merge. Shape:
    --   {"banned_hits": [], "banned_phrases_used": [],
    --    "voice_match_score": 0.x, "iterations": int}
    draft_persona_critique      JSONB,

    cost_usd                    NUMERIC(10, 4) DEFAULT 0,

    -- Outcome closure (for Phase 3 credit assignment). Values:
    --   response       — recruiter replied
    --   no_response    — silence after the follow-up
    --   callback       — moved to phone screen / interview
    -- NULL means "not yet resolved".
    outcome                     TEXT,
    outcome_recorded_at         TIMESTAMPTZ,

    -- Subset of draft_cites that contributed to the outcome. Populated when
    -- outcome IS NOT NULL; readable by agents/outcome_to_persona.
    outcome_attributed_cites    JSONB,

    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_follow_up_user_urgency
    ON follow_up_cadence (user_id, urgency, next_follow_up_date);

CREATE INDEX IF NOT EXISTS idx_follow_up_application
    ON follow_up_cadence (application_id);

-- Partial: only rows where outcome is recorded matter for credit assignment.
CREATE INDEX IF NOT EXISTS idx_follow_up_outcome
    ON follow_up_cadence (user_id, outcome)
    WHERE outcome IS NOT NULL;

-- ── RLS ─────────────────────────────────────────────────────────────────
ALTER TABLE follow_up_cadence ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename = 'follow_up_cadence'
           AND policyname = 'follow_up_user_isolation'
    ) THEN
        CREATE POLICY follow_up_user_isolation ON follow_up_cadence
            FOR ALL
            USING (user_id = auth.uid() OR auth.uid() IS NULL);
    END IF;
END $$;

COMMENT ON TABLE follow_up_cadence IS
    'Phase 1.3 (G6): one row per drafted follow-up. The most recent row '
    'per application is the "current" cadence state. Daily 09:00 cron '
    'creates rows; HITL approval records last_contact_date and increments '
    'follow_up_count.';

-- ── reload PostgREST schema cache (Supabase) ────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
