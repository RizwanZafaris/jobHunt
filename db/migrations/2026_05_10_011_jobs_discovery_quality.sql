-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_011 — Jobs discovery quality columns
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   JobScoutAgent v2 introduces per-target Perplexity discovery + 7 quality
--   safeguards (URL existence, domain whitelist, JD content fingerprint,
--   expiry phrase scan, cross-source confirmation, freshness window,
--   archetype pre-filter). This migration adds the columns those
--   safeguards write to.
--
--   Per the user's Q1 decision (2026-05-11): jobs with confidence_score
--   below 50 are DROPPED (not inserted). So `confidence_score IS NULL OR
--   confidence_score >= 50` is the production filter for any reader.
--   Legacy v1-discovered jobs have NULL confidence_score so they aren't
--   retroactively filtered out.
--
-- Columns added to `jobs`:
--   discovery_sources  jsonb  — array of source tags this job was found
--                                via, e.g. ["perplexity_sonar",
--                                "ats_greenhouse", "apify_career_page"]
--   confidence_score   real   — 0..100, computed from sources + validation
--   freshness          text   — 'fresh' | 'recent' | 'stale'
--   validation_failed  text   — non-null = candidate dropped from insert;
--                                kept here for audit only when we want
--                                the trail (today we DROP, so this stays
--                                NULL for persisted rows)
--   validated_at       timestamptz — when JobValidator last ran
--
-- Indexes
--   jobs_confidence_idx — covers the /today + /actions/today hot path
--                          (user_id, confidence desc, score desc)
--                          partial on open + validated rows
--
-- Idempotency
--   Every ADD COLUMN guarded by IF NOT EXISTS; CHECK constraints DROP-then-
--   create; index uses IF NOT EXISTS. Re-running is a no-op.
--
-- Apply order
--   001 → 002 → 003 → 004 → 005/006 → 007 → 008 → 009 → 010 → 011 (this)
--
-- Rollback
--   ALTER TABLE jobs DROP COLUMN discovery_sources;
--   ALTER TABLE jobs DROP COLUMN confidence_score;
--   ALTER TABLE jobs DROP COLUMN freshness;
--   ALTER TABLE jobs DROP COLUMN validation_failed;
--   ALTER TABLE jobs DROP COLUMN validated_at;
--   DROP INDEX jobs_confidence_idx;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS discovery_sources  jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS confidence_score   real,
    ADD COLUMN IF NOT EXISTS freshness          text,
    ADD COLUMN IF NOT EXISTS validation_failed  text,
    ADD COLUMN IF NOT EXISTS validated_at       timestamptz;

-- Constrain confidence_score to [0, 100]. Idempotent via pg_constraint check.
DO $cs$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_confidence_score_range'
    ) THEN
        EXECUTE 'ALTER TABLE jobs '
                'ADD CONSTRAINT jobs_confidence_score_range '
                'CHECK (confidence_score IS NULL OR (confidence_score >= 0 AND confidence_score <= 100))';
    END IF;
END
$cs$;

-- Constrain freshness to known values.
DO $fr$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'jobs_freshness_valid'
    ) THEN
        EXECUTE 'ALTER TABLE jobs '
                'ADD CONSTRAINT jobs_freshness_valid '
                'CHECK (freshness IS NULL OR freshness IN (''fresh'', ''recent'', ''stale''))';
    END IF;
END
$fr$;

COMMENT ON COLUMN jobs.discovery_sources IS
    'JSONB array of source tags this job was found via, e.g. ["perplexity_sonar", "ats_greenhouse", "apify_career_page", "linkedin"]. Multiple sources = higher confidence.';
COMMENT ON COLUMN jobs.confidence_score IS
    'JobScout v2 quality score [0,100]. Below 50 = DROPPED (not inserted). NULL = legacy v1 row.';
COMMENT ON COLUMN jobs.freshness IS
    'Computed at validation: fresh (<30 days), recent (30-90 days), stale (>90 days). NULL = not yet validated.';
COMMENT ON COLUMN jobs.validation_failed IS
    'When non-NULL, the candidate was dropped during JobScout v2 validation. Set only on rows kept for audit; today policy is DROP.';
COMMENT ON COLUMN jobs.validated_at IS
    'Last time agents/job_validation.py ran. 6-hour revalidation cache.';

-- Hot path for /today and /actions/today: user's open jobs, ranked by
-- (confidence asc + score desc) so high-confidence high-score surfaces first.
-- Partial index: only jobs that are open AND passed validation (or are
-- legacy v1 with NULL confidence_score).
CREATE INDEX IF NOT EXISTS jobs_confidence_idx
    ON jobs (user_id, confidence_score DESC NULLS LAST, match_score DESC)
    WHERE posting_closed_at IS NULL
      AND validation_failed IS NULL;

NOTIFY pgrst, 'reload schema';

COMMIT;
