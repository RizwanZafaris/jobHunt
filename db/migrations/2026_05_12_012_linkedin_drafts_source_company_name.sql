-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_012 — Denormalised linkedin_drafts.source_company_name
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   PR #60 patched `api/actions.py::_build_linkedin_post_due` because the
--   code originally selected `linkedin_drafts.source_company_name`, which
--   doesn't exist (the column is `source_company_id`, a uuid FK to
--   `companies.id`). The workaround there does a second round-trip to
--   look up the company name from `companies.name` using the FK — this
--   works but adds 1 extra query per /today render.
--
--   This migration denormalises the company name onto `linkedin_drafts`
--   so the /today builder can SELECT it directly in a single round-trip.
--   The writer (`agents/g4_linkedin_graph.py::persist_node`) already has
--   `chosen_company_name` in state, so populating it at insert-time is
--   trivial.
--
--   Trade-off: denormalisation means the cached name can drift if a row
--   in `companies` is later renamed. We're explicitly accepting that
--   trade — LinkedIn drafts are short-lived (draft → approved → posted
--   within ~days), and the angle/hook text already bakes in the company
--   name at generation time. A trigger to keep it in sync would add
--   write-path complexity for negligible benefit; we rely on the writer
--   instead. If drift becomes a real problem, add a trigger here later.
--
-- Schema change
--   ALTER TABLE linkedin_drafts ADD COLUMN IF NOT EXISTS
--       source_company_name TEXT;
--
-- Backfill
--   Per `docs/SYSTEM_AUDIT_2026_05_12.md` §1, linkedin_drafts has 0 rows
--   in production today, so the backfill is a no-op. The UPDATE statement
--   is still included for correctness (and any non-prod environments that
--   happen to have rows).
--
-- Idempotency
--   ADD COLUMN guarded by IF NOT EXISTS; UPDATE is safe to re-run (only
--   writes rows where source_company_name IS NULL). Re-running is a no-op.
--
-- Apply order
--   001 → 002 → 003 → 004 → 005/006 → 007 → 008 → 009 → 010 → 011 → 012 (this)
--
-- Rollback
--   ALTER TABLE linkedin_drafts DROP COLUMN IF EXISTS source_company_name;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── 1. add the denormalised column ───────────────────────────────────────
ALTER TABLE linkedin_drafts
    ADD COLUMN IF NOT EXISTS source_company_name TEXT;

COMMENT ON COLUMN linkedin_drafts.source_company_name IS
    'Denormalised copy of companies.name for source_company_id. Populated by '
    'agents/g4_linkedin_graph.py::persist_node at insert time. Read directly '
    'by api/actions.py::_build_linkedin_post_due to avoid a second round-trip. '
    'May drift if the company is renamed; accepted trade-off since drafts are '
    'short-lived. See migration 2026_05_12_012 for rationale.';

-- ── 2. backfill existing rows (no-op today: 0 rows in prod) ──────────────
-- Only touches rows where source_company_name IS NULL so re-runs are safe.
UPDATE linkedin_drafts d
   SET source_company_name = c.name
  FROM companies c
 WHERE d.source_company_id = c.id
   AND d.source_company_name IS NULL;

-- ── 3. reload PostgREST schema cache (Supabase) ──────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
