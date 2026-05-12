-- ══════════════════════════════════════════════════════════════════════════
-- Migration 2026_05_12_019 — jobs.fit_score_breakdown (G5 evaluation scoring)
-- ──────────────────────────────────────────────────────────────────────────
-- Phase 2 §4.1 of the gap-closure roadmap introduces the G5 scoring agent:
-- score every job across six dimensions (role_fit, growth, comp, culture,
-- remote, trajectory) and assign an A-F letter grade. This drives the
-- /today A-F filter chips and gates downstream G7 application spend.
--
-- Why additive (not a new table)
-- ──────────────────────────────────────────────────────────────────────────
-- The roadmap explicitly says "scoring AGENT extending fit_score, NOT a new
-- graph". `jobs.match_score` (INTEGER) and `jobs.fit_details` (JSONB) already
-- exist. We add TWO columns:
--
--   1. `fit_score_breakdown` JSONB — the structured 6-dimension result plus
--      composite, rationale, cites, scored_at, scorer_version. Read by the
--      dashboard's letter-grade chip and by the (future) G7 application
--      assistant when picking which jobs to prioritise.
--
--   2. `letter_grade` TEXT — the A | B | C | D | F surface column. Indexed
--      so /today and /applications can filter "show me only A/B grades"
--      without a JSONB extraction at query time.
--
-- Why a SEPARATE letter_grade column (vs reading it out of the JSONB)?
-- Because the dashboard's /today filter chip uses it as a hot path on
-- every page render. A B-tree index on a plain TEXT column is roughly
-- 10x faster than a functional index on `(fit_score_breakdown->>'letter_grade')`,
-- and `letter_grade` is the single field the UI surfaces — every other
-- dimension lives inside the JSONB blob.
--
-- Backfill strategy
-- ──────────────────────────────────────────────────────────────────────────
-- No backfill. Existing rows get scored on the next G5 re-score cycle (cron
-- picks them up; auto-trigger fires on the next JobScout insert). The
-- scoring agent treats NULL fit_score_breakdown as "never scored, score
-- now". Pre-migration jobs continue to render via `match_score` only —
-- the dashboard tolerates a missing letter_grade and renders no chip.
--
-- Idempotency
-- ──────────────────────────────────────────────────────────────────────────
-- ALTER ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS. Re-running
-- this migration is a no-op.

BEGIN;

-- ── 1. Add columns ─────────────────────────────────────────────────────
ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS fit_score_breakdown JSONB,
  ADD COLUMN IF NOT EXISTS letter_grade        TEXT;

-- ── 2. Letter-grade index (hot path for /today A-F chips) ──────────────
CREATE INDEX IF NOT EXISTS idx_jobs_letter_grade
  ON public.jobs (letter_grade)
  WHERE letter_grade IS NOT NULL;

-- ── 3. Documentation ───────────────────────────────────────────────────
COMMENT ON COLUMN public.jobs.fit_score_breakdown IS
  'G5 6-dimensional fit breakdown produced by agents.scoring_agent.score_role. '
  'Shape: {role_fit, growth, comp, culture, remote, trajectory, composite, '
  'letter_grade, rationale: {dim: text}, cites: [{knowledge_id, section, '
  'similarity}], scored_at, scorer_version}. All dimension scores are '
  '0-100. composite is the weighted average (role_fit 25, growth 20, '
  'comp 20, culture 15, remote 10, trajectory 10). NULL = never scored.';

COMMENT ON COLUMN public.jobs.letter_grade IS
  'G5 letter grade derived from fit_score_breakdown.composite: A>=85, B>=70, '
  'C>=55, D>=40, F<40. Indexed surface column for the /today A-F filter '
  'chips. Kept in sync with fit_score_breakdown.letter_grade by '
  'agents.scoring_agent.';

NOTIFY pgrst, 'reload schema';

COMMIT;
