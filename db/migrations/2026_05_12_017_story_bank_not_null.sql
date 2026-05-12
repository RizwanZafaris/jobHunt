-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_017 — story_bank STAR fields NOT NULL retighten
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Tier 1 follow-up for Phase 1.2 (BUG-029 internal — distinct from the
--   open BUG-029 RLS item). The Phase 1.2 agent shipped story_bank via an
--   additive ALTER (013_*_015_story_bank.sql) rather than CREATE TABLE
--   because a legacy story_bank row existed in production with 0 rows.
--   The Phase 1.2 spec asked for NOT NULL on situation/task/action/result,
--   but the agent kept them nullable to avoid breaking the legacy DDL.
--
--   With story_bank currently at 0 rows (verified at migration time),
--   tightening these columns to NOT NULL is safe and aligns the DDL with
--   the producer-side invariant in agents/g9_nodes.py::persist_node which
--   already drops stories missing any STAR field.
--
-- Idempotency
--   ALTER COLUMN ... SET NOT NULL fails if any row has a NULL. We check
--   counts in a DO block and skip the ALTER if any NULLs exist — this
--   keeps the migration replay-safe in environments that have already
--   accumulated data.
--
-- Apply order
--   015_story_bank.sql (already applied) → 017 (this).
--   016_follow_up_cadence.sql is independent and may apply in any order.
--
-- Rollback
--   ALTER TABLE story_bank ALTER COLUMN situation DROP NOT NULL;
--   (and same for task, action, result)
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

DO $$
DECLARE
    null_count INT;
BEGIN
    SELECT COUNT(*) INTO null_count
      FROM story_bank
     WHERE situation IS NULL OR situation = ''
        OR task IS NULL OR task = ''
        OR action IS NULL OR action = ''
        OR result IS NULL OR result = '';

    IF null_count > 0 THEN
        RAISE NOTICE 'story_bank has % rows with NULL STAR fields — skipping NOT NULL retighten. Run the producer (persist_node) cleanup pass first, then re-apply this migration.', null_count;
    ELSE
        ALTER TABLE story_bank ALTER COLUMN situation SET NOT NULL;
        ALTER TABLE story_bank ALTER COLUMN task SET NOT NULL;
        ALTER TABLE story_bank ALTER COLUMN action SET NOT NULL;
        ALTER TABLE story_bank ALTER COLUMN result SET NOT NULL;
        RAISE NOTICE 'story_bank STAR fields tightened to NOT NULL.';
    END IF;
END $$;

-- reflection is intentionally LEFT nullable. G9 generates reflections but
-- some hand-curated stories (career-ops migration path, manual entry) may
-- legitimately ship without one — the +R is the most-often-missing field
-- in user-curated banks.

COMMENT ON COLUMN story_bank.situation IS
    'STAR field S: NOT NULL since 017. G9 persist_node drops incomplete stories before insert.';
COMMENT ON COLUMN story_bank.task IS
    'STAR field T: NOT NULL since 017.';
COMMENT ON COLUMN story_bank.action IS
    'STAR field A: NOT NULL since 017.';
COMMENT ON COLUMN story_bank.result IS
    'STAR field R: NOT NULL since 017.';

NOTIFY pgrst, 'reload schema';

COMMIT;
