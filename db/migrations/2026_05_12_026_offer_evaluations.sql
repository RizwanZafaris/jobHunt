-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_026 — G8 Offer Evaluations (BACKFILL)
-- ──────────────────────────────────────────────────────────────────────────
-- BACKFILL NOTICE — 2026-05-12 (chore/migration-drift-discovery)
--
--   This file was reconstructed from the live DDL recorded in
--   `supabase_migrations.schema_migrations` (version `20260512130838`,
--   name `offer_evaluations_g8_026`). The original Tier-4 G8 ship
--   applied this migration directly via Supabase MCP without ever
--   landing a corresponding source file under `db/migrations/`.
--
--   Re-running this on the live project is a **no-op** thanks to the
--   `CREATE TABLE IF NOT EXISTS` guard. On a fresh database it creates
--   the table at the correct point in the dependency order (after
--   `applications` and `users`).
--
--   See `db/SCHEMA_LIVE_STATE.md` §4 for the full backfill rationale
--   and the broader migration-drift forensic pass.
--
-- Purpose
--   G8 Offer Evaluations — one row per parsed offer + market comp
--   research + risk-adjusted total-comp calculation + HITL negotiation
--   strategy. The outcome attribution loop (final_total_comp +
--   user_decision) feeds outcome_to_persona so successful negotiations
--   credit the underlying market citations.
--
-- Schema (offer_evaluations)
--   id, user_id, application_id, job_id                — keys
--   base_salary_usd ... total_comp_4yr_avg            — comp components
--   market_p25..p90, market_citations                  — market research
--   negotiation_anchor / walkaway / script             — negotiation plan
--   risk_score, risk_factors, recommendation           — risk model
--   user_decision, final_total_comp                    — outcome capture
--   total_cost_usd, total_latency_ms                   — telemetry
--
-- FK types (from db/SCHEMA_LIVE_STATE.md §2)
--   user_id        → users(id) (UUID)
--   application_id → applications(id) (UUID)
--   job_id         → jobs(id) (INTEGER)
--
-- RLS
--   Permissive: user_id = auth.uid() OR auth.uid() IS NULL — matches the
--   pattern in 001_multi_tenancy.sql so the service-role key keeps
--   working from the Railway worker.
--
-- Rollback
--   DROP TABLE offer_evaluations;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE TABLE IF NOT EXISTS offer_evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES public.users(id) NOT NULL,
    application_id UUID REFERENCES public.applications(id) NOT NULL,
    job_id INTEGER REFERENCES public.jobs(id),

    -- Comp components (all USD-normalised at draft time, original
    -- currency preserved in `currency`).
    base_salary_usd NUMERIC(12, 2),
    target_bonus_usd NUMERIC(12, 2),
    equity_grant_usd NUMERIC(12, 2),
    sign_on_bonus_usd NUMERIC(12, 2),
    other_benefits_usd NUMERIC(12, 2),
    total_comp_year_1 NUMERIC(12, 2),
    total_comp_4yr_avg NUMERIC(12, 2),
    currency TEXT NOT NULL DEFAULT 'USD',

    -- Role descriptors used by the market-research agent.
    level TEXT,
    title TEXT,
    location TEXT,
    start_date DATE,

    -- Market research (filled by G8 comp-research subgraph).
    market_p25 NUMERIC(12, 2),
    market_p50 NUMERIC(12, 2),
    market_p75 NUMERIC(12, 2),
    market_p90 NUMERIC(12, 2),
    market_summary TEXT,
    market_citations JSONB DEFAULT '[]'::jsonb,

    -- Negotiation plan (HITL — surfaces in dashboard, user edits).
    negotiation_anchor NUMERIC(12, 2),
    negotiation_walkaway NUMERIC(12, 2),
    negotiation_script TEXT,
    archetype_strategy TEXT,

    -- Risk model — 0..1 sleep-on-it score + qualitative factors.
    risk_score NUMERIC(3, 2) CHECK (risk_score >= 0 AND risk_score <= 1),
    risk_factors JSONB DEFAULT '[]'::jsonb,
    recommendation TEXT CHECK (recommendation IN ('accept', 'negotiate', 'decline', 'consider')),
    confidence NUMERIC(3, 2),
    risk_adjusted_total_comp NUMERIC(12, 2),
    sleep_on_it_score NUMERIC(3, 2),
    rationale_md TEXT,

    -- Cost telemetry (rolled up from per-node agent_call_log entries).
    total_cost_usd NUMERIC(10, 4) DEFAULT 0,
    total_latency_ms INTEGER DEFAULT 0,
    cites JSONB DEFAULT '[]'::jsonb,

    -- Outcome capture (filled post-negotiation; powers attribution).
    user_decision TEXT CHECK (user_decision IN ('accepted','negotiated_accepted','negotiated_declined','declined','expired')),
    final_total_comp NUMERIC(12, 2),
    user_decision_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_offer_eval_user
    ON offer_evaluations (user_id);
CREATE INDEX IF NOT EXISTS idx_offer_eval_application
    ON offer_evaluations (application_id);
CREATE INDEX IF NOT EXISTS idx_offer_eval_decision
    ON offer_evaluations (user_id, user_decision)
    WHERE user_decision IS NOT NULL;

ALTER TABLE offer_evaluations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS offer_eval_user_isolation ON offer_evaluations;
CREATE POLICY offer_eval_user_isolation ON offer_evaluations
    FOR ALL USING (user_id = auth.uid() OR auth.uid() IS NULL);

COMMENT ON TABLE offer_evaluations IS
    'G8 offer-evaluation outputs. One row per parsed-offer run. Capture user_decision + final_total_comp post-negotiation so outcome_to_persona can attribute negotiation lift back to the specific market citations.';

NOTIFY pgrst, 'reload schema';

COMMIT;
