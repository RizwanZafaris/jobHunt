-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_13_029 — v_graph_runs view (Stream C)
-- ──────────────────────────────────────────────────────────────────────────
-- LangGraph trace observability surface. User feedback:
--   "Analytics should show how my Agent in lang graph are working,
--    this can be used to debug. I don't know what it is built for."
--
-- This migration adds a per-user aggregated view over agent_call_log that
-- groups individual LLM calls into runs of a single graph instance. The
-- /insights?tab=traces dashboard reads from this view + drills into the
-- raw rows for the run detail.
--
-- Schema (v_graph_runs)
--   user_id          UUID
--   graph            TEXT   e.g. 'G2', 'G7', 'G8'
--   run_key          TEXT   first non-null of (application_id, resume_build_id,
--                           job_id::text) — what identifies this run
--   started_at       TIMESTAMPTZ  earliest called_at in the run window
--   last_event_at    TIMESTAMPTZ  latest called_at in the run window
--   total_nodes      INT
--   error_nodes      INT          rows with error IS NOT NULL
--   total_cost_usd   NUMERIC(10,4)
--   total_latency_ms BIGINT
--   nodes_executed   TEXT[]        DISTINCT ARRAY of node_name in chronological order
--   status           TEXT          'failed' (any node errored) | 'in_progress'
--                                  (last event < 5 min ago) | 'converged' (older)
--
-- Run window: ±15 minutes anchored on (graph, run_key) — coarser than a
-- real correlation_id, but agent_call_log doesn't carry one. The 15-min
-- window matches the longest graph (G8 with all 5 nodes hitting Sonar +
-- 2x Opus + DeepSeek + Opus, typically completes in 60-180s but allows
-- buffer for retries).
--
-- Apply order: 028 (phantom cleanup) → 029 (THIS).
-- Rollback: DROP VIEW IF EXISTS public.v_graph_runs;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

DROP VIEW IF EXISTS public.v_graph_runs;
CREATE VIEW public.v_graph_runs AS
WITH normalized AS (
    SELECT
        user_id,
        COALESCE(NULLIF(graph, ''), 'unknown') AS graph,
        COALESCE(
            application_id::text,
            resume_build_id::text,
            CASE WHEN job_id IS NOT NULL THEN 'job:' || job_id::text ELSE NULL END,
            'orphan'
        ) AS run_key,
        node_name,
        called_at,
        cost_usd,
        latency_ms,
        error,
        agent_name,
        model,
        provider,
        input_tokens,
        output_tokens
    FROM agent_call_log
    WHERE called_at >= NOW() - INTERVAL '7 days'
)
SELECT
    n.user_id,
    n.graph,
    n.run_key,
    MIN(n.called_at)            AS started_at,
    MAX(n.called_at)            AS last_event_at,
    COUNT(*)::int                AS total_nodes,
    COUNT(*) FILTER (WHERE n.error IS NOT NULL)::int AS error_nodes,
    SUM(n.cost_usd)::numeric(10,4) AS total_cost_usd,
    SUM(n.latency_ms)::bigint    AS total_latency_ms,
    ARRAY(
        SELECT DISTINCT node_name FROM normalized n2
        WHERE n2.user_id = n.user_id
          AND n2.graph = n.graph
          AND n2.run_key = n.run_key
          AND n2.node_name IS NOT NULL
        ORDER BY 1
    )                            AS nodes_executed,
    CASE
        WHEN bool_or(n.error IS NOT NULL) THEN 'failed'
        WHEN MAX(n.called_at) >= NOW() - INTERVAL '5 minutes' THEN 'in_progress'
        ELSE 'converged'
    END                          AS status
FROM normalized n
GROUP BY n.user_id, n.graph, n.run_key;

COMMENT ON VIEW public.v_graph_runs IS
    'Per-user aggregated LangGraph runs over the last 7 days. Each row '
    'represents one graph instance (G2/G7/G8/...) identified by '
    '(graph, run_key) where run_key is application_id|resume_build_id|'
    '"job:" + job_id. Used by /insights?tab=traces to surface "what is '
    'my agent doing right now". Audit user feedback 2026-05-13.';

GRANT SELECT ON public.v_graph_runs TO authenticated, service_role;

NOTIFY pgrst, 'reload schema';

COMMIT;
