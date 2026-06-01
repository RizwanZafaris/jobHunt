-- 2026_05_29_039_reco_columns_and_today_indexes.sql
-- Scale-hardening migration (CTO scalability audit, 2026-05-29).
--
-- Purpose:
--   1. REPRODUCIBILITY — the user_recommendation_* columns on `jobs` were
--      applied to prod live via MCP (task #59) and never captured as a
--      migration, so a fresh rebuild from db/migrations/ would NOT match
--      production. Capture them here, idempotently. (DB audit finding P5.)
--   2. HOT-PATH INDEXES — back the two most-hit read paths (/today and
--      /today/recommended) with partial indexes aligned to their exact
--      predicate + sort, so they stop scanning all of a tenant's jobs.
--      (DB audit findings P5/P6.)
--
-- SAFE TO APPLY AS-IS on the current single-user DB (tables are small).
-- AT MULTI-TENANT SCALE, build the indexes with CREATE INDEX CONCURRENTLY
-- *outside* a transaction instead (the CONCURRENTLY forms are given at the
-- bottom) — a plain CREATE INDEX takes a write-blocking lock for the build
-- duration, and CONCURRENTLY cannot run inside BEGIN/COMMIT.
--
-- NOTE: column types below are inferred from usage in api/actions.py. The
-- IF NOT EXISTS guards make this a no-op on prod (where the columns already
-- exist), so a slight type difference cannot clobber live data — it only
-- affects a from-scratch rebuild.

BEGIN;

-- 1. Recommendation columns (idempotent; no-op where they already exist) ──
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_recommended_at           timestamptz;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_recommendation_tier      text;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_recommendation_score     double precision;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS user_recommendation_reasoning text;

-- 2a. /today hot path:
--     WHERE user_id = ? AND posting_closed_at IS NULL
--       AND validation_failed IS NULL ORDER BY match_score DESC
--     (api/actions.py::_query)
CREATE INDEX IF NOT EXISTS idx_jobs_today_user_score
    ON jobs (user_id, match_score DESC)
    WHERE posting_closed_at IS NULL AND validation_failed IS NULL;

-- 2b. /today/recommended:
--     WHERE user_recommended_at IS NOT NULL
--     ORDER BY user_recommendation_score DESC
--     (api/actions.py recommended builder)
CREATE INDEX IF NOT EXISTS idx_jobs_user_reco
    ON jobs (user_id, user_recommendation_score DESC)
    WHERE user_recommended_at IS NOT NULL;

COMMIT;

-- ── Production-scale (CONCURRENTLY) variants ────────────────────────────
-- On a large `jobs` table, run these INSTEAD of the two CREATE INDEX
-- statements above — each on its own, with NO surrounding transaction:
--
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_today_user_score
--       ON jobs (user_id, match_score DESC)
--       WHERE posting_closed_at IS NULL AND validation_failed IS NULL;
--
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_user_reco
--       ON jobs (user_id, user_recommendation_score DESC)
--       WHERE user_recommended_at IS NOT NULL;
