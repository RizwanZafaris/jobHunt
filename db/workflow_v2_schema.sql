-- ══════════════════════════════════════════════════════════════════════════
-- Workflow v2: archetype detection + posting legitimacy + manual resume gen
-- Inspired by career-ops architecture. Safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════

-- ── jobs: archetype + legitimacy ─────────────────────────────────────────
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS archetype TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS legitimacy_tier TEXT;
  -- High Confidence | Proceed with Caution | Suspicious
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS legitimacy_signals JSONB DEFAULT '[]'::jsonb;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS resume_generated_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS evaluation_blocks JSONB DEFAULT '{}'::jsonb;
  -- Stores A-G evaluation blocks once "Generate Resume" is triggered

CREATE INDEX IF NOT EXISTS idx_jobs_archetype ON jobs(archetype);
CREATE INDEX IF NOT EXISTS idx_jobs_legitimacy ON jobs(legitimacy_tier);

-- ── story_bank: ensure index ─────────────────────────────────────────────
-- Already created in main schema; just confirm.

NOTIFY pgrst, 'reload schema';
