-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_010 — persona news + strategic-posture tracking
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Adds the bookkeeping columns the P1.3 Perplexity recency layer needs
--   (agents/perplexity_search.py + agents/persona_news_check.py).
--
--   * last_news_check_at     — when we last asked "what's new at $COMPANY?"
--   * news_check_count       — how many times we've run a recency check
--                              for this persona (cheap drift telemetry).
--   * last_strategic_posture_md — the last sonar-pro strategic-posture
--                              summary, stored verbatim. Read by the G2
--                              insider-expert before resume builds and by
--                              the dashboard "Last 30 days at $COMPANY" tile.
--   * last_strategic_posture_at — when that summary was generated.
--
--   The "what's overdue?" dashboard query and the weekly cron both want
--   to find personas with the oldest last_news_check_at first; we add a
--   per-(user_id, last_news_check_at) index with NULLS FIRST so brand-new
--   personas surface at the top of the queue.
--
-- Idempotency
--   ADD COLUMN IF NOT EXISTS + CREATE INDEX IF NOT EXISTS make this safe
--   to re-run; no-op on the second pass.
--
-- Apply order
--   001 → … → 009 → 010 (this). No dependency on 008/009 internals.
--
-- DO NOT apply directly. The user reviews + applies via apply_migration.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE company_personas
    ADD COLUMN IF NOT EXISTS last_news_check_at        TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS news_check_count          INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_strategic_posture_md TEXT,
    ADD COLUMN IF NOT EXISTS last_strategic_posture_at TIMESTAMPTZ;

COMMENT ON COLUMN company_personas.last_news_check_at IS
    'P1.3: last time agents.persona_news_check ran a recency_check for this persona. NULL = never checked.';
COMMENT ON COLUMN company_personas.news_check_count IS
    'P1.3: cumulative count of recency_check runs against this persona.';
COMMENT ON COLUMN company_personas.last_strategic_posture_md IS
    'P1.3: most recent Perplexity sonar-pro strategic-posture summary (markdown).';
COMMENT ON COLUMN company_personas.last_strategic_posture_at IS
    'P1.3: when last_strategic_posture_md was generated.';

-- "What's overdue for this user?" — orders nulls (never-checked) first.
CREATE INDEX IF NOT EXISTS company_personas_overdue_idx
    ON company_personas (user_id, last_news_check_at NULLS FIRST);

NOTIFY pgrst, 'reload schema';

COMMIT;
