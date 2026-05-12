-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_021 — interview_prep G3 Tier 2 JSONB columns
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Tier 2 §4.2 (G3 story_bank integration) ships three new G3 nodes
--   (story_retriever, gap_analyzer, prep_pack_persona_critic) that
--   write structured output to interview_prep:
--     • retrieved_stories     — {question_id → StoryBankMatch}
--     • story_gaps            — list of {question, missing_competency,
--                                       reframe_suggestion}
--     • persona_critic_drops  — fabricated competencies pruned before display
--
--   These were originally flagged as BUG-037 by the G3 build agent —
--   the writer in interview_agents/g3_io::finalize_interview_prep is
--   defensive (only adds the fields to the UPDATE payload when present)
--   so it doesn't error on UPDATE before the columns exist, but the
--   data wouldn't actually persist. This migration unblocks the writer.
--
-- Live application
--   Applied via Supabase MCP on 2026-05-12 as part of the autonomous
--   Tier 2 sweep. This file is the source-of-truth migration so future
--   `bash db/migrations/APPLY.sh` runs against fresh clones / staging
--   re-create the same shape.
--
-- Apply order
--   015 story_bank → 017 story_bank_not_null → 018 search_story_bank_v2
--   → 019 jobs_fit_score_breakdown (G5) → 020 jobs_legitimacy_v1
--   → 021 (this) → 022+ (future).
--
-- Rollback
--   ALTER TABLE interview_prep
--     DROP COLUMN retrieved_stories,
--     DROP COLUMN story_gaps,
--     DROP COLUMN persona_critic_drops;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE interview_prep
    ADD COLUMN IF NOT EXISTS retrieved_stories JSONB DEFAULT '{}'::jsonb;

ALTER TABLE interview_prep
    ADD COLUMN IF NOT EXISTS story_gaps JSONB DEFAULT '[]'::jsonb;

ALTER TABLE interview_prep
    ADD COLUMN IF NOT EXISTS persona_critic_drops JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN interview_prep.retrieved_stories IS
    'G3 Tier 2 (2026-05-12): {question_id → StoryBankMatch}. '
    'story_retriever_node populates via search_story_bank_v2 RPC; '
    'each match carries the source story id + similarity for outcome '
    'attribution back to story_bank.outcome_score.';

COMMENT ON COLUMN interview_prep.story_gaps IS
    'G3 Tier 2: list of {question, missing_competency, reframe_suggestion}. '
    'gap_analyzer_node populates for questions whose best story match has '
    'similarity < 0.5 — surfaces "you have no story for X" + how to reframe.';

COMMENT ON COLUMN interview_prep.persona_critic_drops IS
    'G3 Tier 2: competencies the persona_critic dropped because they were '
    'fabricated (not present in profile_master.core_competencies). Audit '
    'trail so outcome_to_persona can avoid crediting fabricated claims.';

-- Reload PostgREST schema cache so the new columns show up in dashboard reads.
NOTIFY pgrst, 'reload schema';

COMMIT;
