-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_023 — profile voice calibration (Tier 4 G11)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   G11 extracts the user's writing voice ONCE from uploaded
--   writing_samples and persists an injectable prompt block on
--   profile_master.voice_calibration. Every downstream writer node
--   (G2 resume writer, G6 follow-up draft_generator, G7 application
--   answer generator) prepends that block to its user message so every
--   generated piece sounds like the user.
--
--   Two additions in one file (single 023, per roadmap §6.3):
--     1. profile_master.voice_calibration JSONB
--     2. writing_samples table (the corpus the calibration reads from)
--
-- Engineering principles (§11)
--   2 (cite):  voice_calibration.source_sample_ids tracks which
--              writing_samples produced the block — outcome attribution
--              can credit / blame the inputs.
--   3 (identity-lock): voice attributes are derived ONLY from the user's
--              own uploaded samples (never invented; persist node
--              enforces source_sample_ids is non-empty).
--   4 (RLS):   writing_samples enforces user_id = auth.uid().
--   5 (cost):  agent_call_log.graph='G11' will fill via the g11.*
--              agent_name prefix in llm_router._derive_graph_and_node.
--
-- Live application
--   Applied via Supabase MCP on 2026-05-12 as part of the Tier 4 G11
--   build. This file is the source-of-truth migration so future
--   `bash db/migrations/APPLY.sh` runs reproduce the same shape.
--
-- Apply order
--   021 interview_prep G3 columns → 022 application_answers (G7 inflight)
--   → 023 (this) → 024 proof_points.
--
-- Rollback
--   ALTER TABLE profile_master DROP COLUMN voice_calibration;
--   DROP TABLE writing_samples;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── 1. profile_master.voice_calibration ──────────────────────────────────
ALTER TABLE profile_master
    ADD COLUMN IF NOT EXISTS voice_calibration JSONB DEFAULT '{}'::jsonb;

COMMENT ON COLUMN profile_master.voice_calibration IS
    'G11 Tier 4: extracted voice attributes — {tone, sentence_length_avg, '
    'openings_preferred, punctuation_habits, vocabulary_preferred, '
    'vocabulary_avoid, sample_count, extracted_at, source_sample_ids, '
    'voice_profile_block}. The voice_profile_block sub-field is the '
    'injectable prompt fragment that G2/G6/G7 writer nodes prepend to '
    'their user message. Empty {} on cold-start; writers fall back to '
    'generic style until the first calibration runs.';

-- ── 2. writing_samples table ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS writing_samples (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id               UUID REFERENCES public.users(id) NOT NULL,
    title                 TEXT NOT NULL,
    body                  TEXT NOT NULL,
    kind                  TEXT NOT NULL,
    source                TEXT,
    used_in_calibration   BOOLEAN DEFAULT FALSE,
    created_at            TIMESTAMPTZ DEFAULT NOW(),
    CHECK (kind IN ('cover_letter','linkedin_post','email','essay','other'))
);

CREATE INDEX IF NOT EXISTS idx_writing_samples_user
    ON writing_samples (user_id);

CREATE INDEX IF NOT EXISTS idx_writing_samples_user_unused
    ON writing_samples (user_id)
    WHERE used_in_calibration = FALSE;

ALTER TABLE writing_samples ENABLE ROW LEVEL SECURITY;

-- Per-user isolation: only the owner sees their samples. The
-- `auth.uid() IS NULL` clause lets the server-side service-role client
-- read every row (it bypasses RLS implicitly anyway, but the explicit
-- predicate matches the convention used in earlier migrations).
DROP POLICY IF EXISTS writing_samples_user_isolation ON writing_samples;
CREATE POLICY writing_samples_user_isolation ON writing_samples
    FOR ALL USING (user_id = auth.uid() OR auth.uid() IS NULL);

COMMENT ON TABLE writing_samples IS
    'G11 Tier 4 corpus: user-uploaded writing samples that the voice '
    'calibrator analyses to build profile_master.voice_calibration. '
    'kind ∈ {cover_letter, linkedin_post, email, essay, other}. '
    'source documents provenance (user_upload, g4_linkedin_draft, '
    'imported_from_drive). used_in_calibration flips to TRUE after the '
    'G11 persist node consumes it; future runs only re-read fresh + '
    'previously-used rows together so the voice profile stays stable '
    'rather than swinging on a single new sample.';

-- Reload PostgREST schema cache so the new column + table show up in API reads.
NOTIFY pgrst, 'reload schema';

COMMIT;
