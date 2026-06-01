-- 2026_05_29_041_vector_hnsw.sql
-- Scale-hardening migration — DB-4 (docs/SCALABILITY_BUILD_PLAN.md, §"DB-4").
--
-- Purpose:
--   Migrate the four user-/company-knowledge vector indexes from ivfflat to
--   HNSW. HNSW gives better recall-at-speed and — critically — needs no
--   `lists` re-tuning as row counts grow (ivfflat lists must be re-sized to
--   ~rows/1000 or recall degrades; see migration 015's lists=10→100 churn).
--   No RPC changes: every caller queries with the same `<=>` cosine
--   operator, so search_story_bank_v2 / search_proof_points_v2 and the
--   company_knowledge / rizwan_profile retrieval paths are untouched.
--
-- Tables + verified embedding columns + existing ivfflat index names
-- (read from db/schema.sql and the source migrations before writing):
--   company_knowledge.embedding   idx_company_knowledge_embedding   (db/schema.sql, ivfflat lists=100)
--   story_bank.embedding          idx_story_bank_embedding          (2026_05_12_015, ivfflat lists=100)
--   proof_points.embedding        idx_proof_points_embedding        (2026_05_12_024, ivfflat lists=100)
--   rizwan_profile.embedding      idx_rizwan_profile_embedding      (db/schema.sql, ivfflat lists=10)
-- All four columns are vector(1536) (text-embedding-3-small).
--
-- NOT TOUCHED: jobs.jd_embedding — it has no vector index today and adding
-- one is a separate, gated decision (SCALABILITY.md §data: "decide: add HNSW
-- if you'll search it, or drop the column"). Out of scope for DB-4.
--
-- pgvector 0.8.0 is ON (verified in migrations 015/024 via
-- pg_extension extname='vector'); HNSW has been supported since 0.5.0, so
-- no extension bump is needed. CREATE EXTENSION IF NOT EXISTS is a no-op
-- guard.
--
-- SAFE TO APPLY AS-IS on the current single-user DB (these tables are tiny —
-- story_bank/proof_points/rizwan_profile hold ~10-100 rows; company_knowledge
-- a few hundred). A plain CREATE INDEX takes a brief lock that is negligible
-- at this size.
--
-- AT SCALE / on a live-serving table, build each HNSW index with
-- CREATE INDEX CONCURRENTLY *outside* a transaction (forms at the bottom) —
-- a plain CREATE INDEX write-locks the table for the build, and CONCURRENTLY
-- cannot run inside BEGIN/COMMIT. Drop the old ivfflat index AFTER the new
-- HNSW index is built so retrieval is never left without an index.
--
-- Idempotent: DROP INDEX IF EXISTS + CREATE INDEX IF NOT EXISTS. Re-running
-- is safe (and a no-op once the HNSW indexes exist).

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- ── 1. company_knowledge: ivfflat → HNSW ────────────────────────────────
DROP INDEX IF EXISTS idx_company_knowledge_embedding;
CREATE INDEX IF NOT EXISTS idx_company_knowledge_embedding
    ON company_knowledge
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── 2. story_bank: ivfflat → HNSW ───────────────────────────────────────
DROP INDEX IF EXISTS idx_story_bank_embedding;
CREATE INDEX IF NOT EXISTS idx_story_bank_embedding
    ON public.story_bank
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── 3. proof_points: ivfflat → HNSW ─────────────────────────────────────
DROP INDEX IF EXISTS idx_proof_points_embedding;
CREATE INDEX IF NOT EXISTS idx_proof_points_embedding
    ON public.proof_points
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── 4. rizwan_profile: ivfflat → HNSW ───────────────────────────────────
-- (Was ivfflat lists=10 — structurally single-user via UNIQUE(section).
-- HNSW removes the need to ever re-tune lists if more sections are added.)
DROP INDEX IF EXISTS idx_rizwan_profile_embedding;
CREATE INDEX IF NOT EXISTS idx_rizwan_profile_embedding
    ON rizwan_profile
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

NOTIFY pgrst, 'reload schema';

COMMIT;

-- ── Production-scale (CONCURRENTLY) variants ────────────────────────────
-- On a large / live-serving table, run these INSTEAD of each
-- DROP + CREATE pair above. Build the new HNSW index FIRST (each on its own,
-- with NO surrounding transaction), confirm it's valid, THEN drop the old
-- ivfflat index — so the table is never without a vector index. Note the
-- HNSW build uses the same index name as the old ivfflat one, so either
-- rename on build or drop-then-build-concurrently during a quiet window.
--
-- Pattern per table (shown for company_knowledge; repeat for the others):
--
--   -- Build HNSW under a temporary name (no txn):
--   CREATE INDEX CONCURRENTLY idx_company_knowledge_embedding_hnsw
--       ON company_knowledge
--       USING hnsw (embedding vector_cosine_ops)
--       WITH (m = 16, ef_construction = 64);
--
--   -- Swap names atomically once the build succeeds:
--   BEGIN;
--     DROP INDEX IF EXISTS idx_company_knowledge_embedding;
--     ALTER INDEX idx_company_knowledge_embedding_hnsw
--         RENAME TO idx_company_knowledge_embedding;
--   COMMIT;
--
--   -- story_bank:
--   CREATE INDEX CONCURRENTLY idx_story_bank_embedding_hnsw
--       ON public.story_bank USING hnsw (embedding vector_cosine_ops)
--       WITH (m = 16, ef_construction = 64);
--   BEGIN;
--     DROP INDEX IF EXISTS public.idx_story_bank_embedding;
--     ALTER INDEX public.idx_story_bank_embedding_hnsw
--         RENAME TO idx_story_bank_embedding;
--   COMMIT;
--
--   -- proof_points:
--   CREATE INDEX CONCURRENTLY idx_proof_points_embedding_hnsw
--       ON public.proof_points USING hnsw (embedding vector_cosine_ops)
--       WITH (m = 16, ef_construction = 64);
--   BEGIN;
--     DROP INDEX IF EXISTS public.idx_proof_points_embedding;
--     ALTER INDEX public.idx_proof_points_embedding_hnsw
--         RENAME TO idx_proof_points_embedding;
--   COMMIT;
--
--   -- rizwan_profile:
--   CREATE INDEX CONCURRENTLY idx_rizwan_profile_embedding_hnsw
--       ON rizwan_profile USING hnsw (embedding vector_cosine_ops)
--       WITH (m = 16, ef_construction = 64);
--   BEGIN;
--     DROP INDEX IF EXISTS idx_rizwan_profile_embedding;
--     ALTER INDEX idx_rizwan_profile_embedding_hnsw
--         RENAME TO idx_rizwan_profile_embedding;
--   COMMIT;
--
-- Optional query-time recall knob (per session, not stored): raise
-- hnsw.ef_search above the default 40 for higher recall, e.g.
--   SET hnsw.ef_search = 100;
