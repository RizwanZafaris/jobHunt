-- 2026_05_29_040_agent_cost_rollup.sql
-- Scale-hardening migration — DB-3 (docs/SCALABILITY_BUILD_PLAN.md, §"DB-3").
--
-- Purpose:
--   1. HOT-PATH INDEX — back the per-tenant cost queries (and the P2-6
--      budget gate's cached spend SUM) with a (user_id, called_at DESC)
--      composite index. Today every /costs read and every budget check
--      range-scans called_at then filters user_id; at multi-tenant scale
--      that re-reads the whole window for one tenant. (SCALABILITY.md
--      "agent_call_log unbounded … add (user_id, called_at DESC) index".)
--   2. DAILY COST ROLLUP — an `agent_cost_daily` table + an idempotent
--      `refresh_agent_cost_daily(days_back)` function that upserts one row
--      per (day, user_id, provider, model, agent_name) from agent_call_log.
--      This feeds the P2-6 per-tenant budget gate and the P3-8 cost cards
--      so they read a tiny pre-aggregated table instead of full-scanning
--      agent_call_log (the fastest-growing table in the system).
--
-- SAFE TO APPLY AS-IS on the current single-user DB (agent_call_log is
-- small). AT MULTI-TENANT SCALE, build the index with
-- CREATE INDEX CONCURRENTLY *outside* a transaction instead (the
-- CONCURRENTLY form is given at the bottom) — a plain CREATE INDEX takes a
-- write-blocking lock for the build duration, and CONCURRENTLY cannot run
-- inside BEGIN/COMMIT.
--
-- COLUMN SOURCE: every column referenced on agent_call_log below is from
-- the live shape — base DDL in db/multi_llm_schema.sql (id, agent_name,
-- provider NOT NULL, model NOT NULL, input_tokens, output_tokens,
-- cost_usd NUMERIC(10,6), error, called_at) PLUS user_id UUID NOT NULL,
-- added to agent_call_log by db/migrations/2026_05_10_001_multi_tenancy.sql
-- (agent_call_log is in that migration's tenant-ized target_tables list).
-- The rollup deliberately uses only this confirmed subset.
--
-- Idempotent: IF NOT EXISTS guards + CREATE OR REPLACE. Re-running is safe.
-- The one-time backfill at the end is an upsert, so re-running it is a no-op
-- beyond refreshing the most-recent days.

BEGIN;

-- ── 1. Hot-path composite index: per-tenant, newest-first ───────────────
--     WHERE user_id = ? AND called_at >= ?  (per-tenant cost window +
--     budget-gate spend SUM). Mirrors the single-column
--     idx_agent_call_log_called_at from multi_llm_schema.sql but leads with
--     user_id so a tenant's window is one contiguous range.
CREATE INDEX IF NOT EXISTS idx_agent_call_log_user_called
    ON agent_call_log (user_id, called_at DESC);


-- ── 2. Daily rollup table ───────────────────────────────────────────────
-- One row per (day, user_id, provider, model, agent_name). agent_name is
-- nullable on agent_call_log, so we COALESCE it to '(unknown)' in the PK to
-- keep the composite primary key total (NULLs are not comparable in a PK).
CREATE TABLE IF NOT EXISTS agent_cost_daily (
    day            DATE        NOT NULL,
    user_id        UUID        NOT NULL,
    provider       TEXT        NOT NULL,
    model          TEXT        NOT NULL,
    agent_name     TEXT        NOT NULL DEFAULT '(unknown)',
    calls          BIGINT      NOT NULL DEFAULT 0,
    cost_usd       NUMERIC(14, 6) NOT NULL DEFAULT 0,
    input_tokens   BIGINT      NOT NULL DEFAULT 0,
    output_tokens  BIGINT      NOT NULL DEFAULT 0,
    errors         BIGINT      NOT NULL DEFAULT 0,
    refreshed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (day, user_id, provider, model, agent_name)
);

-- Per-tenant time-window scan (P3-8 cost cards: "this tenant, last N days").
CREATE INDEX IF NOT EXISTS idx_agent_cost_daily_user_day
    ON agent_cost_daily (user_id, day DESC);

COMMENT ON TABLE agent_cost_daily IS
    'DB-3 daily rollup of agent_call_log. One row per '
    '(day, user_id, provider, model, agent_name). Refreshed by '
    'refresh_agent_cost_daily(); feeds the P2-6 per-tenant budget gate and '
    'P3-8 cost cards so they read this pre-aggregated table instead of '
    'full-scanning agent_call_log. agent_name=''(unknown)'' rolls up rows '
    'whose agent_name was NULL.';


-- ── 3. refresh_agent_cost_daily(days_back) ──────────────────────────────
-- Idempotent upsert from agent_call_log into agent_cost_daily for the last
-- `days_back` days (default 2 — covers today + yesterday so a nightly run
-- can't miss a late-arriving row from just before midnight). Returns the
-- number of rollup rows touched, for logging.
--
-- Recompute-from-source (not incremental): each (day,user_id,provider,
-- model,agent_name) group is fully re-aggregated and the totals overwritten
-- on conflict, so a re-run can never double-count.
CREATE OR REPLACE FUNCTION refresh_agent_cost_daily(days_back INT DEFAULT 2)
RETURNS INT
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    touched INT;
BEGIN
    IF days_back < 1 THEN
        RAISE EXCEPTION 'refresh_agent_cost_daily: days_back must be >= 1 (got %)', days_back;
    END IF;

    WITH agg AS (
        SELECT
            (called_at)::date              AS day,
            user_id,
            provider,
            model,
            COALESCE(agent_name, '(unknown)') AS agent_name,
            COUNT(*)::bigint               AS calls,
            COALESCE(SUM(cost_usd), 0)     AS cost_usd,
            COALESCE(SUM(input_tokens), 0)::bigint  AS input_tokens,
            COALESCE(SUM(output_tokens), 0)::bigint AS output_tokens,
            COUNT(*) FILTER (WHERE error IS NOT NULL)::bigint AS errors
        FROM agent_call_log
        WHERE called_at >= (NOW()::date - (days_back - 1))  -- inclusive of today
        GROUP BY 1, 2, 3, 4, 5
    ),
    upsert AS (
        INSERT INTO agent_cost_daily AS d
            (day, user_id, provider, model, agent_name,
             calls, cost_usd, input_tokens, output_tokens, errors, refreshed_at)
        SELECT
            day, user_id, provider, model, agent_name,
            calls, cost_usd, input_tokens, output_tokens, errors, NOW()
        FROM agg
        ON CONFLICT (day, user_id, provider, model, agent_name) DO UPDATE SET
            calls         = EXCLUDED.calls,
            cost_usd      = EXCLUDED.cost_usd,
            input_tokens  = EXCLUDED.input_tokens,
            output_tokens = EXCLUDED.output_tokens,
            errors        = EXCLUDED.errors,
            refreshed_at  = NOW()
        RETURNING 1
    )
    SELECT COUNT(*) INTO touched FROM upsert;

    RETURN touched;
END;
$$;

COMMENT ON FUNCTION refresh_agent_cost_daily(INT) IS
    'DB-3: idempotent upsert of the last `days_back` days (default 2) from '
    'agent_call_log into agent_cost_daily. Recompute-from-source so re-runs '
    'cannot double-count. Returns rollup rows touched. Schedule nightly via '
    'cron.schedule (commented at the bottom of this migration).';


-- ── 4. One-time backfill ────────────────────────────────────────────────
-- Populate the rollup from all existing history. 36500 days_back ≈ 100y,
-- i.e. "everything currently in the table". Cheap today (small table); on a
-- large table this is a one-off full aggregation — acceptable for a single
-- backfill. The upsert makes it safe to re-run.
SELECT refresh_agent_cost_daily(36500);

NOTIFY pgrst, 'reload schema';

COMMIT;

-- ── Production-scale (CONCURRENTLY) index variant ───────────────────────
-- On a large agent_call_log, run this INSTEAD of the CREATE INDEX in step 1
-- above — on its own, with NO surrounding transaction:
--
--   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_agent_call_log_user_called
--       ON agent_call_log (user_id, called_at DESC);
--
-- ── Nightly refresh + retention scheduling (commented — you decide) ─────
-- pg_cron is available on Supabase. The user owns scheduling, so these are
-- left commented. Apply once, in the SQL editor / a psql session:
--
--   -- Nightly rollup refresh (00:15 UTC). days_back=2 covers any late
--   -- writes from just before the previous midnight.
--   SELECT cron.schedule(
--       'refresh-agent-cost-daily',
--       '15 0 * * *',
--       $$ SELECT refresh_agent_cost_daily(2) $$
--   );
--
--   -- Rollup retention: keep ~13 months of daily rollups (cheap; this is
--   -- already aggregated). Runs monthly, 1st at 01:00 UTC.
--   SELECT cron.schedule(
--       'prune-agent-cost-daily',
--       '0 1 1 * *',
--       $$ DELETE FROM agent_cost_daily WHERE day < (NOW()::date - 400) $$
--   );
--
--   -- Raw agent_call_log retention (DB-3 plan: 120 days). The richer
--   -- cleanup_agent_call_log(days_to_keep) function from
--   -- db/agent_call_log_perf.sql (refuses < 7 days) is the preferred path;
--   -- schedule it daily at 02:00 UTC once the rollup is trusted as the
--   -- long-term cost-history source:
--   SELECT cron.schedule(
--       'cleanup-agent-call-log',
--       '0 2 * * *',
--       $$ SELECT cleanup_agent_call_log(120) $$
--   );
--
-- To unschedule any of the above: SELECT cron.unschedule('<job-name>');
