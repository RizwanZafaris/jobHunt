-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_020 — Legitimacy agent v1 (Tier 2, §4.3)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Ship the Legitimacy agent v1 — career-ops' 6-signal "ghost posting"
--   detector, with the 4 signals that don't depend on >=50 application
--   outcomes (signals 1, 2, 3, 5). Signals 4 (company hiring velocity) and
--   6 (per-company response rate) defer to v2 once we have outcome data.
--
--   Composite score is a weighted sum of the 4 signals (each weight 0.25,
--   sum 1.0). Score >=0.7 = legitimate, 0.5..0.7 = caution, <0.5 = suspicious.
--
-- New column on `jobs`:
--   legitimacy_score  NUMERIC(3,2)  — 0.00..1.00 composite
--
-- Already exists (from db/workflow_v2_schema.sql, applied earlier):
--   legitimacy_tier    TEXT          — was "High Confidence" / "Proceed
--                                       with Caution" / "Suspicious" from
--                                       JobScout v2 GPT-4.1 classification.
--                                       v1 agent writes lowercase
--                                       "legitimate" / "caution" /
--                                       "suspicious".
--   legitimacy_signals JSONB         — was list[str] from GPT-4.1
--                                       narrative. v1 agent overwrites
--                                       with a dict keyed by signal name:
--                                       {age:{raw,normalised,weight,
--                                       contribution}, url:..., repost:...,
--                                       news:{...,citations:[...]}}.
--
-- Idempotency
--   ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS. Re-running is
--   a no-op. We DO NOT migrate the old `legitimacy_tier` capitalised values
--   here — the agent overwrites them as jobs get re-scored on the next
--   scout pass (cron, ~6h cadence).
--
-- Apply order
--   ... → 016 → 020 (this). The 017/018/019 slots are reserved for
--   pre-Tier-2 ships if needed; we picked 020 to match the §4.3 roadmap
--   numbering and leave gaps for late landings.
--
-- Rollback
--   ALTER TABLE jobs DROP COLUMN legitimacy_score;
--   DROP INDEX idx_jobs_legitimacy_score;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- Defensive: workflow_v2_schema.sql added these but if someone ran a
-- subset of migrations they may be missing. Add idempotently.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS legitimacy_tier TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS legitimacy_signals JSONB DEFAULT '[]'::jsonb;

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS legitimacy_score NUMERIC(3,2);

COMMENT ON COLUMN jobs.legitimacy_score IS
  'Legitimacy agent v1: weighted 4-signal score 0.00-1.00. >=0.7 = legitimate, 0.5..0.7 = caution, <0.5 = suspicious. Signals: posting age (0.25), URL reachability (0.25), repost pattern (0.25), recent news (0.25). NULL = not yet scored.';

COMMENT ON COLUMN jobs.legitimacy_tier IS
  'Legitimacy agent v1 categorical bucket: "legitimate" | "caution" | "suspicious" (lowercase). Pre-v1 rows may carry the legacy capitalised values "High Confidence" | "Proceed with Caution" | "Suspicious" from the JobScout v2 GPT-4.1 classifier; they are overwritten when the v1 agent next scores the job.';

COMMENT ON COLUMN jobs.legitimacy_signals IS
  'Legitimacy agent v1 per-signal breakdown. Shape: {"age":{raw,normalised,weight,contribution}, "url":{...,status,cached_at}, "repost":{...,duplicate_count}, "news":{...,summary,citations,cached_at}}. Pre-v1 rows may carry list[str] from the legacy GPT-4.1 narrative.';

-- Hot path for /today filter (suspicious cards hidden by default).
-- Partial index on open + scored rows; small and selective.
CREATE INDEX IF NOT EXISTS idx_jobs_legitimacy_score
  ON jobs (user_id, legitimacy_score DESC NULLS LAST)
  WHERE posting_closed_at IS NULL
    AND validation_failed IS NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_legitimacy_tier_v1
  ON jobs (legitimacy_tier)
  WHERE legitimacy_tier IS NOT NULL;

NOTIFY pgrst, 'reload schema';

COMMIT;
