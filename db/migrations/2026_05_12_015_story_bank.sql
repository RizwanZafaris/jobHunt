-- ══════════════════════════════════════════════════════════════════════════
-- Migration 2026_05_12_015 — story_bank Phase 1.2 hardening
-- ──────────────────────────────────────────────────────────────────────────
-- Phase 1.2 of the gap-closure roadmap (docs/GAP_CLOSURE_ROADMAP_2026_05_12.md
-- §3.2) introduces the G9 STAR+R extractor: read the user's master CV, emit
-- 10–15 STAR+R stories with pgvector embeddings, persist them, and expose a
-- semantic-search API. The story_bank table is the shared substrate every
-- downstream graph reads from:
--
--    G3 Interview Prep    — pulls top-k stories per likely question
--    G7 Application       — pulls top-k stories per behavioural form field
--    outcome_to_persona   — credits stories cited in successful outcomes
--
-- ──────────────────────────────────────────────────────────────────────────
-- Why ADDITIVE (not DROP + CREATE)
--
-- A `story_bank` row already lives in production with this shape:
--    id, title, situation, task, action, result, reflection,
--    themes (TEXT[]), companies_used (TEXT[]), embedding (vector),
--    created_at, updated_at, user_id (NOT NULL), org_id (nullable)
--
-- Live row count is 0 today (verified via Supabase MCP `SELECT count(*) FROM
-- story_bank` on 2026-05-12 — pre-migration). Even so, we treat the existing
-- DDL as load-bearing: agents/interview_agent.py, agents/rizwan_agent.py,
-- interview_agents/g3_io.py, and the `search_story_bank` RPC all already
-- reference these columns. DROP + CREATE would silently break those call
-- sites or require coordinated multi-PR rollout.
--
-- Instead this migration:
--   1. Adds the columns Phase 1.2 needs but the legacy DDL lacks
--      (competencies, archetypes, quantified_metrics, outcome_score,
--      source_cv_hash, source_experience_id, source_text).
--   2. Replaces the ivfflat embedding index (legacy was lists=10; we want
--      lists=100 as per the Phase 1.2 spec — closer to the published
--      heuristic of ~rows/1000 for our expected 10k-100k row volume).
--   3. Adds a GIN index on competencies so G3/G7 can pre-filter by
--      competency before the vector search ("leadership" stories first).
--   4. Adds a unique constraint on (user_id, lower(title)) so the G9
--      persist_node can use ON CONFLICT to replace stories on re-run.
--   5. Leaves the existing legacy columns (themes, companies_used) and
--      existing RLS policies (select/insert/update/delete _own) intact —
--      Phase 1.2 surfaces them through the new agent but doesn't require
--      changes.
--
-- ──────────────────────────────────────────────────────────────────────────
-- Compatibility with §3.2 spec
--
-- The roadmap (§3.2) prescribes situation/task/action/result NOT NULL.
-- Live DDL has them nullable. We DON'T retighten to NOT NULL here because:
--   • The legacy table allowed nullable; an unknown writer (e.g. a
--     hand-seeded story with only a result/reflection) might exist in
--     other environments. Backfilling NOT NULL across forks is risky.
--   • The G9 persist_node enforces non-empty values at write time
--     (cleaner: structural enforcement at the producer, not the schema).
-- If we ever decide to harden, a follow-up migration can convert in place.
--
-- pgvector verified ON via `SELECT * FROM pg_extension WHERE extname='vector'`
-- (returns version 0.8.0). No CREATE EXTENSION needed.
--
-- ──────────────────────────────────────────────────────────────────────────
-- Idempotency
-- Every ALTER uses IF NOT EXISTS / DROP IF EXISTS. Re-running is safe.

BEGIN;

-- ── 1. Add Phase 1.2 columns ────────────────────────────────────────────
ALTER TABLE public.story_bank
  ADD COLUMN IF NOT EXISTS competencies         TEXT[],
  ADD COLUMN IF NOT EXISTS archetypes           TEXT[],
  ADD COLUMN IF NOT EXISTS quantified_metrics   TEXT[],
  ADD COLUMN IF NOT EXISTS outcome_score        INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS source_cv_hash       TEXT,
  ADD COLUMN IF NOT EXISTS source_experience_id BIGINT,
  ADD COLUMN IF NOT EXISTS source_text          TEXT;

-- ── 2. Drop legacy ivfflat (lists=10) and rebuild at lists=100 ──────────
-- ivfflat doesn't support ALTER for the lists parameter — we have to
-- drop + recreate. Safe because we're not concurrently serving queries
-- against an empty table.
DROP INDEX IF EXISTS public.idx_story_bank_embedding;
CREATE INDEX idx_story_bank_embedding
  ON public.story_bank
  USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);

-- ── 3. GIN index on competencies for archetype/competency pre-filter ───
-- G3/G7 will often pre-filter ("show me ‘leadership' stories first, then
-- vector-rank") before semantic search. Without this, that's a seq scan.
CREATE INDEX IF NOT EXISTS idx_story_bank_competencies
  ON public.story_bank
  USING gin (competencies);

CREATE INDEX IF NOT EXISTS idx_story_bank_archetypes
  ON public.story_bank
  USING gin (archetypes);

-- ── 4. Unique (user_id, lower(title)) so re-runs UPSERT cleanly ─────────
-- G9.persist_node uses ON CONFLICT (user_id, lower(title)) to replace a
-- story when the same title comes back from the extractor (e.g. the user
-- updates their CV and re-runs G9; the same achievement gets re-emitted
-- with refined wording). Without this, re-runs would duplicate rows.
-- We index on lower(title) to make the dedup case-insensitive.
CREATE UNIQUE INDEX IF NOT EXISTS story_bank_user_title_unique
  ON public.story_bank (user_id, lower(title));

-- ── 5. Documentation ────────────────────────────────────────────────────
COMMENT ON TABLE public.story_bank IS
  'STAR+R stories for behavioural answers. Owned by G9 (extraction) but '
  'consumed by G3 (interview prep) and G7 (application forms). Semantic '
  'retrieval via embedding (text-embedding-3-small). outcome_score is a '
  'Bayesian credit counter — outcome_to_persona increments it when a '
  'cited story leads to a successful outcome (callback / offer).';

COMMENT ON COLUMN public.story_bank.competencies IS
  'Tags this story demonstrates (e.g. ["leadership","technical_depth",'
  '"cross_functional"]). Pre-filterable via GIN.';

COMMENT ON COLUMN public.story_bank.archetypes IS
  'Role archetypes this story is best deployed for (e.g. ["PM","Staff PM"]).';

COMMENT ON COLUMN public.story_bank.quantified_metrics IS
  'Concrete numbers in the story (e.g. ["$50M TPV","3x conversion"]). '
  'IDENTITY LOCK: G9.persist_node verifies each entry appears in '
  'source_text (lowercase) — fabricated metrics are dropped before insert.';

COMMENT ON COLUMN public.story_bank.outcome_score IS
  'Bayesian credit from G3/G7 outcomes. Higher = more downstream success. '
  'Used as a tiebreaker when multiple stories tie on cosine similarity.';

COMMENT ON COLUMN public.story_bank.source_cv_hash IS
  'sha256 of the master_cv_md that produced this story. Lets G9 re-runs '
  'detect which stories belong to which CV version (and prune stories '
  'orphaned by a CV rewrite).';

COMMENT ON COLUMN public.story_bank.source_experience_id IS
  'profile_experience.id FK back to the originating CV role. Drives '
  'outcome_to_persona attribution (cite:knowledge_id breadcrumb).';

COMMENT ON COLUMN public.story_bank.source_text IS
  'The original CV passage this story was extracted from. Audit trail + '
  'identity-lock substrate (quantified_metrics must appear here).';

NOTIFY pgrst, 'reload schema';

COMMIT;
