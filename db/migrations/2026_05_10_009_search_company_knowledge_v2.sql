-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_009 — search_company_knowledge v2 (return id)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Phase 3 outcome-conditioned RAG (agents/outcome_to_persona.credit_outcome)
--   needs to walk an outcome event back to the company_knowledge rows that
--   the G2 resume builder actually cited. Today the search RPC returns the
--   row's section/content/similarity but DROPS its primary-key `id`. The G2
--   insider_expert node has no id to embed in agent_transcript, so credit
--   assignment falls back to "top-5 most-recent for company" — diffuse, not
--   targeted.
--
--   This migration replaces search_company_knowledge with a v2 that includes
--   `ck.id uuid` in both the RETURNS TABLE definition and the inner SELECT.
--   No other behavior changes — same WHERE filter (company_name match,
--   non-null embedding), same cosine ordering, same LIMIT. Old call sites
--   continue to work because we only ADD a column to the result row.
--
-- Compatibility
--   The old function had this signature:
--     search_company_knowledge(vector(1536), TEXT, INT)
--       RETURNS TABLE (section TEXT, content TEXT, similarity FLOAT, scraped_at TIMESTAMPTZ)
--
--   Postgres treats RETURNS TABLE as part of the function signature, so a
--   plain CREATE OR REPLACE will fail with "cannot change return type".
--   We therefore DROP FUNCTION IF EXISTS first, then CREATE.
--
-- Security / RLS
--   The RPC runs as the caller. company_knowledge has RLS enabled (per
--   migration 001). We preserve the same posture: no SECURITY DEFINER, no
--   special grants — clients hit it through PostgREST and rely on RLS for
--   row visibility. Service-role keys bypass RLS for backend writes.
--
-- Smoke test (in-migration)
--   At the bottom we run the function once with a dummy zero-vector and a
--   non-existent company name. We expect zero rows back without raising —
--   that confirms the new function is callable with the new column shape.
--   If it raises, the transaction aborts.
--
-- Idempotency
--   Re-running this migration is safe: DROP FUNCTION IF EXISTS + CREATE
--   produces the same end state every time.
--
-- Apply order
--   001 → 002 → 003 → 004 → 005/006 → 007 → 008 → 009 (this).
--   No dependency on 008 — these are independent — but ordering by date
--   keeps the timeline tidy.
--
-- DO NOT apply directly. The user reviews + applies via apply_migration.
--
-- Rollback
--   DROP FUNCTION IF EXISTS search_company_knowledge(vector, TEXT, INT);
--   CREATE OR REPLACE FUNCTION search_company_knowledge(...)
--     RETURNS TABLE (section TEXT, content TEXT, similarity FLOAT, scraped_at TIMESTAMPTZ)
--     ... (the v1 body — see db/schema.sql ~line 165 in the pre-009 commit)
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── Drop the old definition ────────────────────────────────────────────────
-- Required because the RETURNS TABLE shape is part of the signature and
-- CREATE OR REPLACE FUNCTION can't change it.
DROP FUNCTION IF EXISTS search_company_knowledge(vector, TEXT, INT);
DROP FUNCTION IF EXISTS search_company_knowledge(vector(1536), TEXT, INT);

-- ── v2: includes ck.id so callers can cite the exact row ──────────────────
CREATE OR REPLACE FUNCTION search_company_knowledge(
    query_embedding vector(1536),
    target_company  TEXT,
    match_count     INT DEFAULT 5
)
RETURNS TABLE (
    id          UUID,
    section     TEXT,
    content     TEXT,
    similarity  FLOAT,
    scraped_at  TIMESTAMPTZ
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        ck.id,
        ck.section,
        ck.content,
        1 - (ck.embedding <=> query_embedding) AS similarity,
        ck.scraped_at
    FROM company_knowledge ck
    WHERE ck.company_name = target_company
      AND ck.embedding IS NOT NULL
    ORDER BY ck.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- ── Smoke test ─────────────────────────────────────────────────────────────
-- Build a 1536-dim zero vector at runtime (literal would be 6 KB of '0,'),
-- pass a non-existent company so we expect zero rows, and confirm the call
-- itself doesn't raise. This catches signature / column-shape mismatches.
DO $$
DECLARE
    zero_vec vector(1536);
    rec record;
    row_count int := 0;
BEGIN
    -- array_fill produces a real[] of zeroes; cast straight to vector.
    zero_vec := array_fill(0::real, ARRAY[1536])::vector;
    FOR rec IN
        SELECT id, company_name
        FROM company_knowledge ck
        WHERE FALSE  -- belt-and-braces: don't actually scan
        UNION ALL
        SELECT id, NULL::TEXT AS company_name
        FROM search_company_knowledge(
            zero_vec,
            '__migration_009_smoke_test_no_match__',
            1
        )
    LOOP
        row_count := row_count + 1;
    END LOOP;
    RAISE NOTICE 'migration 009 smoke test ok (% rows from sentinel call)', row_count;
END;
$$;

NOTIFY pgrst, 'reload schema';

COMMIT;
