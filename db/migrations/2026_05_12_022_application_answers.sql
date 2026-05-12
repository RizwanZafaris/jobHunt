-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_022 — G7 Application Graph (Tier 3)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Tier 3 — G7 is the crown-jewel single time-saver in the entire roadmap.
--   It turns a 30-60 minute manual application-form fill into 5-10 minutes,
--   while still requiring per-field HITL approval before any field gets
--   filled. The persistence layer is application_answers: one row per
--   active G7 run, with the questions + drafted answers + HITL state
--   stored as JSONB so we can iterate on the schema without forced
--   ALTER-table churn.
--
--   v1 scope: Greenhouse only (~70% of our target ATSes). Lever / Ashby /
--   Workday land in Phase 4.
--
-- Schema (application_answers)
--   id, user_id, application_id, form_url, ats_type    — keys
--   questions JSONB                                     — list of question objects
--   status TEXT                                         — drafting | awaiting_review |
--                                                         approved | filled | submitted |
--                                                         cancelled
--   total_cost_usd                                      — telemetry
--   outcome*                                            — Phase 4 attribution
--   created_at / updated_at
--
--   Each questions[i] object shape (kept in JSONB for iteration flexibility):
--     {
--       "idx": int,                         -- 0-indexed position in the form
--       "field_id": str,                    -- DOM id / Greenhouse field id
--       "field_label": str,                 -- visible label
--       "field_type": str,                  -- text | textarea | select | radio | url | ...
--       "required": bool,
--       "options": list[str] | null,        -- for select/radio
--       "classification": str,              -- 1 of 10 categories (see roadmap)
--       "retrieved_context": list[dict],    -- {kind, id, content, similarity?}
--       "generated_answer": str,
--       "persona_critique": dict,           -- {verdict, flags, fabricated_metrics, ...}
--       "cites": list[dict],                -- [{kind:'story_bank'|'company_knowledge'|
--                                              'comp_cache'|'company_persona', id:uuid}]
--       "user_edited": str | null,          -- user override, surfaces in dashboard
--       "approved": bool,
--       "ats_filled_ok": bool | null,       -- form_filler outcome per field
--       "fill_error": str | null
--     }
--
-- Indexes
--   idx_application_answers_user_status   — dashboard list ("awaiting_review for me")
--   idx_application_answers_application   — fast lookup by applications.id
--
-- RLS
--   Permissive: user_id = auth.uid() OR auth.uid() IS NULL. Matches the
--   pattern in 001_multi_tenancy.sql so the service-role key keeps working
--   from the Railway worker.
--
-- FK types
--   Live DB (canonical — see db/SCHEMA_LIVE_STATE.md):
--     user_id        UUID NOT NULL REFERENCES users(id)
--     application_id UUID NOT NULL REFERENCES applications(id)
--   This file was originally authored against an imagined "profiles(id)"
--   + "BIGINT application_id" shape that has never existed in this
--   project — the original db/schema.sql declared applications.id UUID
--   from day one, and migration 001 introduces users (not profiles).
--   The FK types below are the live-correct shape; the BUG-042 fix
--   patched them in-place after the first apply attempt failed.
--
-- Rollback
--   DROP TABLE application_answers;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS application_answers (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Owner + parent application. Both required for RLS + cascade safety.
    user_id                     UUID REFERENCES users(id) NOT NULL,
    application_id              UUID REFERENCES applications(id) NOT NULL,

    -- Form metadata captured by form_scanner. form_url is the canonical
    -- URL we drove Playwright against; ats_type lets us pick the right
    -- selector pack for fill (greenhouse | lever | ashby | workday).
    form_url                    TEXT NOT NULL,
    ats_type                    TEXT NOT NULL DEFAULT 'greenhouse',

    -- The whole G7 working state lives here as JSONB. See the file header
    -- for the per-question shape.
    questions                   JSONB NOT NULL DEFAULT '[]'::jsonb,

    -- State machine — drives the dashboard surface + filler eligibility.
    --   drafting           — graph is producing answers (pre-interrupt)
    --   awaiting_review    — graph interrupt() hit, waiting for user
    --   approved           — user POSTed /approve; graph resuming
    --   filled             — form_filler completed; user sees the loaded form
    --   submitted          — user (manually) clicked Submit on the ATS
    --   cancelled          — user cancelled OR an error bubbled up
    status                      TEXT NOT NULL DEFAULT 'drafting'
        CHECK (status IN ('drafting','awaiting_review','approved','filled','submitted','cancelled')),

    -- Total Sonnet 4.6 spend across all nodes. Target ~$0.30/form per
    -- roadmap. Updated by persist_node.
    total_cost_usd              NUMERIC(10, 4) DEFAULT 0,

    -- Phase 4 outcome attribution. When the user marks a callback /
    -- rejection on the parent application, we credit the cites that
    -- contributed to the answers — same pattern as follow_up_cadence.
    outcome                     TEXT
        CHECK (outcome IS NULL OR outcome IN ('callback','no_response','rejection')),
    outcome_recorded_at         TIMESTAMPTZ,

    -- Per-cite attribution arrays. Filled when outcome flips to non-NULL.
    outcome_attributed_stories  JSONB,
    outcome_attributed_knowledge JSONB,

    created_at                  TIMESTAMPTZ DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_application_answers_user_status
    ON application_answers (user_id, status);

CREATE INDEX IF NOT EXISTS idx_application_answers_application
    ON application_answers (application_id);

-- Partial: outcome attribution lookups only care about resolved rows.
CREATE INDEX IF NOT EXISTS idx_application_answers_outcome
    ON application_answers (user_id, outcome)
    WHERE outcome IS NOT NULL;

-- ── RLS ─────────────────────────────────────────────────────────────────
ALTER TABLE application_answers ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
         WHERE schemaname = 'public'
           AND tablename = 'application_answers'
           AND policyname = 'application_answers_user_isolation'
    ) THEN
        CREATE POLICY application_answers_user_isolation ON application_answers
            FOR ALL
            USING (user_id = auth.uid() OR auth.uid() IS NULL);
    END IF;
END $$;

COMMENT ON TABLE application_answers IS
    'Tier 3 / G7: one row per application-form-fill run. Stores the '
    'extracted questions, drafted answers, per-question cites, and HITL '
    'approval state. v1 is Greenhouse-only.';

-- ── reload PostgREST schema cache (Supabase) ────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
