-- Migration 013: comp_cache table (Phase 1.1)
--
-- Backs agents/comp_research.py. Caches Perplexity Sonar compensation-band
-- queries for 30 days per (user, company, role, level, location) tuple.
--
-- Why this matters: career-ops queries Glassdoor / Levels.fyi / Blind on
-- every evaluation — slow + expensive. Our cache amortises one Sonar call
-- (~$0.02) across the 30-day window. Expected steady-state spend after
-- the cache fills: ~$1-5 / month.
--
-- All four name fields have a `_norm` lowercase-stripped twin so case
-- variants ("Stripe"/"STRIPE"/"stripe ") collapse onto the same cache row.
-- The unique index is on the normalised columns; the original-case columns
-- are kept for display.
--
-- RLS: row-level isolation by user_id. Single-user MVP today, multi-tenant
-- ready.

CREATE TABLE IF NOT EXISTS public.comp_cache (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,

  -- Display values (original case, may include whitespace).
  company         TEXT NOT NULL,
  role            TEXT NOT NULL,
  level           TEXT,
  location        TEXT,

  -- Normalised dedup keys. Computed at insert time by the caller
  -- (agents/comp_research.py::_cache_key_tuple). Same row across user_id +
  -- the four normalised columns is enforced by the unique index below.
  company_norm    TEXT NOT NULL,
  role_norm       TEXT NOT NULL,
  level_norm      TEXT NOT NULL DEFAULT '',
  location_norm   TEXT NOT NULL DEFAULT '',

  -- Compensation percentiles in `currency`. NULLable so partial data
  -- ("we found p50 + p75 but not p25 / p90") is still cacheable.
  currency        TEXT NOT NULL DEFAULT 'USD',
  p25             INTEGER,
  p50             INTEGER,
  p75             INTEGER,
  p90             INTEGER,

  -- Provenance.
  source_summary  TEXT,                   -- Sonar's one-sentence summary
  source_citations JSONB NOT NULL DEFAULT '[]'::jsonb,
                                          -- cite:knowledge_id breadcrumbs for outcome attribution
  confidence      TEXT NOT NULL DEFAULT 'low'
                  CHECK (confidence IN ('high', 'medium', 'low')),

  -- Cost telemetry (how much did this cached row cost to acquire).
  cost_usd        NUMERIC(10, 4) NOT NULL DEFAULT 0,

  -- TTL.
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
);

-- Per-user dedup key. Same (company, role, level, location) tuple → one row.
CREATE UNIQUE INDEX IF NOT EXISTS comp_cache_user_norm_unique
  ON public.comp_cache (user_id, company_norm, role_norm, level_norm, location_norm);

-- Lookup path used by _get_cache_hit (filter by user + key + unexpired).
CREATE INDEX IF NOT EXISTS comp_cache_lookup_idx
  ON public.comp_cache (user_id, company_norm, role_norm, level_norm, location_norm, expires_at);

-- Telemetry / observability: scan recent / expensive rows.
CREATE INDEX IF NOT EXISTS comp_cache_created_at_idx
  ON public.comp_cache (created_at DESC);

-- ─── RLS ─────────────────────────────────────────────────────────────────
ALTER TABLE public.comp_cache ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS comp_cache_user_isolation ON public.comp_cache;
CREATE POLICY comp_cache_user_isolation
  ON public.comp_cache
  FOR ALL
  USING (
    -- Multi-tenant guard. Today we run as the single Rizwan user so the
    -- check effectively reduces to `user_id = '00000000-...-0001'`, but
    -- it's written generically so the same policy works in multi-tenant
    -- mode without edits.
    user_id = auth.uid()
    -- Service-role bypass: when called from FastAPI with the service key,
    -- auth.uid() is NULL — allow.
    OR auth.uid() IS NULL
  );

-- ─── Optional housekeeping ───────────────────────────────────────────────
-- Convenience: caller can run `DELETE FROM comp_cache WHERE expires_at < NOW()`
-- as a nightly housekeeping cron. Not enforced as a trigger — Postgres
-- doesn't need active deletion since the lookup query filters on
-- `expires_at >= NOW()` (see _get_cache_hit). Rows just become invisible.

COMMENT ON TABLE public.comp_cache IS
  'Phase 1.1: cached compensation bands keyed by (user, company, role, level, location). '
  'Backs agents/comp_research.py. 30-day TTL. Powers G5 evaluation, G7 application '
  'assistant salary fields, G8 offer negotiation framing.';

COMMENT ON COLUMN public.comp_cache.source_citations IS
  'Perplexity Sonar citations preserved as a JSONB array of {url, title?, ...}. '
  'These travel with downstream G7/G8 outputs as cite:knowledge_id breadcrumbs '
  'so outcome_to_persona can attribute callback / counter-offer outcomes back '
  'to specific comp data sources.';
