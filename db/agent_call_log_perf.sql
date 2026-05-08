-- ══════════════════════════════════════════════════════════════════════════
-- Phase 1.9 — agent_call_log performance hardening
-- Run AFTER db/multi_llm_schema.sql.
-- Idempotent: safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   Adds composite + partial indexes, two RPC rollup functions (replace
--   Python aggregation in /costs endpoints with DB-side aggregation),
--   a health view, a cleanup function, and documented partition guidance.
--
-- WHY NOW
--   agent_call_log starts empty but every G2 build writes ~12 rows. At
--   1 build/day = 4k rows/year. At 10 builds/day = 40k rows/year. The
--   existing single-column indexes carry simple lookups well, but the
--   compound queries the dashboard does benefit from compound indexes.
--
-- WHAT'S NOT IN HERE (intentional)
--   - Materialized views: deferred until row count justifies the
--     refresh-staleness tradeoff. Composite indexes are sufficient
--     until ~100k rows.
--   - Auto-partitioning by month: see partition setup at the bottom of
--     this file (commented). Apply only when row count > 100k OR daily
--     write volume > 1k rows.
-- ══════════════════════════════════════════════════════════════════════════


-- ── 1. Composite + partial indexes ─────────────────────────────────────
-- These complement the single-column indexes from multi_llm_schema.sql.
-- Optimised for the queries /costs/by-provider, /costs/by-agent, and
-- /costs/by-resume-build do.

-- "by-provider in window" — range scan called_at + group on provider
CREATE INDEX IF NOT EXISTS idx_agent_call_log_called_provider
    ON agent_call_log (called_at DESC, provider);

-- "by-agent in window" — only index rows with a non-null agent_name
CREATE INDEX IF NOT EXISTS idx_agent_call_log_called_agent
    ON agent_call_log (called_at DESC, agent_name)
    WHERE agent_name IS NOT NULL;

-- "by-resume-build in window" — only G2-attributed rows
CREATE INDEX IF NOT EXISTS idx_agent_call_log_called_build
    ON agent_call_log (called_at DESC, resume_build_id)
    WHERE resume_build_id IS NOT NULL;

-- "errors only" — partial index keeps it tiny (only error rows)
CREATE INDEX IF NOT EXISTS idx_agent_call_log_errors
    ON agent_call_log (called_at DESC)
    WHERE error IS NOT NULL;


-- ── 2. RPC rollup functions ────────────────────────────────────────────
-- These let api/server.py call .rpc(...) instead of fetching every row
-- and aggregating in Python. Critical when row count grows past 10k.
--
-- Both functions:
--   - Take days_back as a parameter (default 7)
--   - Aggregate at the DB layer using the new composite indexes
--   - Return one row per provider / agent

CREATE OR REPLACE FUNCTION cost_by_provider_window(days_back INT DEFAULT 7)
RETURNS TABLE (
    provider TEXT,
    calls BIGINT,
    cost_usd NUMERIC,
    input_tokens BIGINT,
    output_tokens BIGINT,
    avg_latency_ms NUMERIC
)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT
        provider,
        COUNT(*)::BIGINT AS calls,
        ROUND(SUM(cost_usd)::numeric, 4) AS cost_usd,
        SUM(input_tokens)::BIGINT AS input_tokens,
        SUM(output_tokens)::BIGINT AS output_tokens,
        ROUND(AVG(latency_ms)::numeric, 0) AS avg_latency_ms
    FROM agent_call_log
    WHERE called_at >= NOW() - (days_back || ' days')::INTERVAL
    GROUP BY provider
    ORDER BY cost_usd DESC;
$$;


CREATE OR REPLACE FUNCTION cost_by_agent_window(days_back INT DEFAULT 7)
RETURNS TABLE (
    agent_name TEXT,
    calls BIGINT,
    cost_usd NUMERIC,
    input_tokens BIGINT,
    output_tokens BIGINT,
    avg_latency_ms NUMERIC,
    providers TEXT[],
    models TEXT[]
)
LANGUAGE sql
STABLE
SET search_path = public, pg_temp
AS $$
    SELECT
        COALESCE(agent_name, '(unknown)') AS agent_name,
        COUNT(*)::BIGINT AS calls,
        ROUND(SUM(cost_usd)::numeric, 4) AS cost_usd,
        SUM(input_tokens)::BIGINT AS input_tokens,
        SUM(output_tokens)::BIGINT AS output_tokens,
        ROUND(AVG(latency_ms)::numeric, 0) AS avg_latency_ms,
        ARRAY_AGG(DISTINCT provider ORDER BY provider) AS providers,
        ARRAY_AGG(DISTINCT model    ORDER BY model)    AS models
    FROM agent_call_log
    WHERE called_at >= NOW() - (days_back || ' days')::INTERVAL
    GROUP BY COALESCE(agent_name, '(unknown)')
    ORDER BY cost_usd DESC;
$$;


-- ── 3. Health view: error rate + latency percentiles by provider ───────
-- Queried by GET /costs/health for the dashboard's health badge strip.
-- Last 7 days only — keep the view focused on "is the system healthy
-- right now", not historical analytics.

CREATE OR REPLACE VIEW v_agent_call_health AS
SELECT
    provider,
    COUNT(*)::BIGINT                                       AS calls_7d,
    COUNT(*) FILTER (WHERE error IS NOT NULL)::BIGINT      AS errors_7d,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE error IS NOT NULL)
            / NULLIF(COUNT(*), 0),
        2
    )                                                      AS error_rate_pct,
    PERCENTILE_DISC(0.5)  WITHIN GROUP (ORDER BY latency_ms)  AS p50_latency_ms,
    PERCENTILE_DISC(0.95) WITHIN GROUP (ORDER BY latency_ms)  AS p95_latency_ms,
    PERCENTILE_DISC(0.99) WITHIN GROUP (ORDER BY latency_ms)  AS p99_latency_ms,
    ROUND(SUM(cost_usd)::numeric, 4)                          AS total_cost_usd_7d,
    MAX(called_at)                                            AS last_call_at
FROM agent_call_log
WHERE called_at >= NOW() - INTERVAL '7 days'
GROUP BY provider
ORDER BY total_cost_usd_7d DESC;


-- ── 4. Cleanup function ────────────────────────────────────────────────
-- Manual / cron-driven retention policy. Defaults to 365 days which is
-- generous (≥1 year of full history). Returns deleted count for logging.
-- Triggered via POST /costs/cleanup or scripts/run.py once we wire it.

CREATE OR REPLACE FUNCTION cleanup_agent_call_log(days_to_keep INT DEFAULT 365)
RETURNS INT
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    deleted_count INT;
BEGIN
    IF days_to_keep < 7 THEN
        -- Defensive: refuse to delete anything younger than a week.
        -- Stops accidental data loss from a misconfigured cron.
        RAISE EXCEPTION 'cleanup_agent_call_log: refusing days_to_keep < 7 (got %)', days_to_keep;
    END IF;

    WITH deleted AS (
        DELETE FROM agent_call_log
        WHERE called_at < NOW() - (days_to_keep || ' days')::INTERVAL
        RETURNING 1
    )
    SELECT COUNT(*) INTO deleted_count FROM deleted;

    RETURN deleted_count;
END;
$$;


-- ── 5. Stats helper view ───────────────────────────────────────────────
-- Surfaces table size + row count for the cost dashboard's "scale-up"
-- guidance (when to apply partitioning, when to reduce retention, etc).

CREATE OR REPLACE VIEW v_agent_call_log_stats AS
SELECT
    (SELECT COUNT(*)         FROM agent_call_log)              AS total_rows,
    (SELECT COUNT(*)         FROM agent_call_log
        WHERE called_at >= NOW() - INTERVAL '24 hours')        AS rows_last_24h,
    (SELECT COUNT(*)         FROM agent_call_log
        WHERE called_at >= NOW() - INTERVAL '7 days')          AS rows_last_7d,
    (SELECT MIN(called_at)   FROM agent_call_log)              AS oldest_row_at,
    (SELECT MAX(called_at)   FROM agent_call_log)              AS newest_row_at,
    pg_size_pretty(
        pg_total_relation_size('public.agent_call_log')
    )                                                          AS total_size,
    pg_size_pretty(
        pg_indexes_size('public.agent_call_log')
    )                                                          AS indexes_size;


-- ══════════════════════════════════════════════════════════════════════════
-- 6. Partition setup (DO NOT APPLY until row count > 100k)
-- ══════════════════════════════════════════════════════════════════════════
-- The Supabase project has pg_partman 5.3.1 available. When agent_call_log
-- volume justifies it, run the block below to convert to monthly
-- range-partitioning.
--
-- Trigger criteria (any of):
--   * SELECT total_rows FROM v_agent_call_log_stats > 100000
--   * SELECT rows_last_24h FROM v_agent_call_log_stats > 5000
--   * SELECT pg_total_relation_size('public.agent_call_log') > 1 GB
--
-- Steps (run in transaction during low-traffic window):
--
-- BEGIN;
--   CREATE EXTENSION IF NOT EXISTS pg_partman;
--
--   -- Rename existing table to "_legacy"
--   ALTER TABLE agent_call_log RENAME TO agent_call_log_legacy;
--
--   -- Recreate as partitioned by called_at
--   CREATE TABLE agent_call_log (
--       LIKE agent_call_log_legacy INCLUDING ALL
--   ) PARTITION BY RANGE (called_at);
--
--   -- Bootstrap pg_partman to manage monthly partitions
--   SELECT partman.create_parent(
--       p_parent_table  => 'public.agent_call_log',
--       p_control       => 'called_at',
--       p_type          => 'range',
--       p_interval      => '1 month',
--       p_premake       => 6
--   );
--
--   -- Move legacy data into the new partitioned table
--   INSERT INTO agent_call_log
--       SELECT * FROM agent_call_log_legacy;
--
--   -- Verify before dropping
--   SELECT COUNT(*) FROM agent_call_log;
--   SELECT COUNT(*) FROM agent_call_log_legacy;
--
--   DROP TABLE agent_call_log_legacy;
-- COMMIT;
--
-- After committing, schedule pg_partman maintenance (monthly):
--   SELECT cron.schedule(
--       'partman-agent-call-log',
--       '0 0 1 * *',  -- 1st of month, 00:00
--       $$ SELECT partman.run_maintenance(
--              p_parent_table => 'public.agent_call_log'
--          ) $$
--   );
-- ══════════════════════════════════════════════════════════════════════════
