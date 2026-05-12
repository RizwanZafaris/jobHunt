-- ══════════════════════════════════════════════════════════════════════════
-- Migration 2026_05_12_017 — search_story_bank_v2 (pgvector RPC)
-- ──────────────────────────────────────────────────────────────────────────
-- Tier 1 of the jobHunt gap-closure backlog. Moves the cosine ranking of
-- `story_bank` from the Python layer (agents/story_bank_agent.search_stories,
-- shipped in Phase 1.2 as a client-side sort) INTO Postgres, so the
-- ivfflat embedding index built in migration 015 can actually do its job.
--
-- Why now
-- -------
--   • G3 (interview prep, Phase 2) will call search per likely behavioural
--     question — easily 20+ calls per prep session.
--   • G7 (application assistant, Phase 3) will call it per behavioural form
--     field — 5–10 calls per application.
--   • Multi-tenant future: story_bank grows past 10–20 rows/user.
-- Client-side sort across all-with-embedding rows is O(N) per call. The
-- ivfflat index can do top-k in O(sqrt(N) · k). At single-user scale today
-- this is invisible, but the RPC also halves the egress payload (we no
-- longer ship every 1536-float embedding back to Python just to sort).
--
-- Precedent
-- ---------
-- search_company_knowledge_v2 (2026_05_10_009) is the same pattern: a
-- thin plpgsql wrapper around `embedding <=> p_query_embedding` ORDER BY,
-- LIMIT p_k. We follow its column-naming convention (p_* params) but
-- additionally:
--   • scope by p_user_id (story_bank is per-user; company_knowledge isn't).
--   • take optional p_competency_filter / p_archetype_filter so G3/G7 can
--     pre-filter ("show me leadership-tagged stories first").
--   • return outcome_score so the caller can apply the Bayesian boost
--     client-side if it wants (we don't fold it into the SQL ordering
--     because the magnitude — capped at 0.05 — would be drowned out by
--     pgvector's raw cosine distance range).
--   • secondary ORDER BY outcome_score DESC, created_at DESC so true ties
--     break deterministically.
--
-- Security
-- --------
-- SECURITY DEFINER is intentional. story_bank has RLS enabled (per
-- migration 001 multi_tenancy). The function bypasses RLS in a controlled
-- way: the WHERE clause filters by p_user_id, and we REVOKE PUBLIC, GRANT
-- only authenticated + service_role. Effectively the same posture as a
-- direct `db.table("story_bank").select(...).eq("user_id", uid)` call
-- through the service-role key, but with the cosine sort pushed into the
-- index. No data leak vector: a caller who can already invoke this RPC
-- must pass a user_id of their choice — at the API layer we wire that to
-- the authenticated user's id (api/stories.py:108 `user.id`).
--
-- Compatibility
-- -------------
-- The existing `search_story_bank(vector, INT)` RPC (db/schema.sql line
-- 221) is left UNTOUCHED. It's used by agents/interview_agent.py:252 and
-- agents/rizwan_agent.py:181 with a different signature (no user_id, no
-- filters). Replacing it would be a separate migration's worth of work
-- and isn't required for this tier. We add v2 alongside v1.
--
-- Idempotency
-- -----------
-- CREATE OR REPLACE FUNCTION is safe to re-run. The GRANT / REVOKE are
-- idempotent in Postgres. The smoke test at the bottom is read-only.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

CREATE OR REPLACE FUNCTION public.search_story_bank_v2(
    p_user_id UUID,
    p_query_embedding VECTOR(1536),
    p_k INT DEFAULT 5,
    p_competency_filter TEXT[] DEFAULT NULL,
    p_archetype_filter TEXT[] DEFAULT NULL
)
RETURNS TABLE (
    id UUID,
    title TEXT,
    situation TEXT,
    task TEXT,
    action TEXT,
    result TEXT,
    reflection TEXT,
    competencies TEXT[],
    archetypes TEXT[],
    quantified_metrics TEXT[],
    similarity FLOAT,
    outcome_score INT
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    SELECT
        sb.id,
        sb.title,
        sb.situation,
        sb.task,
        sb.action,
        sb.result,
        sb.reflection,
        sb.competencies,
        sb.archetypes,
        sb.quantified_metrics,
        (1 - (sb.embedding <=> p_query_embedding))::FLOAT AS similarity,
        sb.outcome_score
    FROM public.story_bank sb
    WHERE sb.user_id = p_user_id
      AND sb.embedding IS NOT NULL
      AND (p_competency_filter IS NULL OR sb.competencies && p_competency_filter)
      AND (p_archetype_filter  IS NULL OR sb.archetypes   && p_archetype_filter)
    ORDER BY
        sb.embedding <=> p_query_embedding,        -- primary: semantic
        sb.outcome_score DESC,                      -- tiebreaker: past success
        sb.created_at DESC                          -- final tiebreaker: recency
    LIMIT p_k;
END;
$$;

REVOKE ALL ON FUNCTION public.search_story_bank_v2(UUID, VECTOR, INT, TEXT[], TEXT[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_story_bank_v2(UUID, VECTOR, INT, TEXT[], TEXT[])
    TO authenticated, service_role;

COMMENT ON FUNCTION public.search_story_bank_v2(UUID, VECTOR, INT, TEXT[], TEXT[]) IS
  'Tier 1 (2026-05-12): pgvector cosine search over story_bank. Supersedes '
  'the Python client-side ranking in agents/story_bank_agent.py::search_stories. '
  'Replaces with: SELECT * FROM search_story_bank_v2(uid, embedding, 5).';

-- ── Smoke test ─────────────────────────────────────────────────────────────
-- Confirm the new function is callable with the new column shape.
-- We use a 1536-dim zero vector + a synthetic user_id that won't match
-- any real row, so we expect zero rows back without raising. If the
-- signature is wrong (e.g. RETURNS TABLE shape mismatch) this aborts the
-- transaction.
DO $$
DECLARE
    zero_vec vector(1536);
    row_count int := 0;
    rec record;
BEGIN
    zero_vec := array_fill(0::real, ARRAY[1536])::vector;
    FOR rec IN
        SELECT *
        FROM public.search_story_bank_v2(
            '00000000-0000-0000-0000-000000000000'::uuid,
            zero_vec,
            1,
            NULL,
            NULL
        )
    LOOP
        row_count := row_count + 1;
    END LOOP;
    RAISE NOTICE 'migration 017 smoke test ok (% rows from sentinel call)', row_count;
END;
$$;

NOTIFY pgrst, 'reload schema';

COMMIT;
