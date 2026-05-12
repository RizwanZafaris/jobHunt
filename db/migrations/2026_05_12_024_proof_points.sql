-- ══════════════════════════════════════════════════════════════════════════
-- Migration 2026_05_12_024 — proof_points + search_proof_points_v2 RPC
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Tier 4 (§6.4 of GAP_CLOSURE_ROADMAP_2026_05_12.md) — the Proof Point
--   agent. Proof points are crisp, tag-able facts pulled from the user's
--   articles / launches / posts / metrics. They're shorter than STAR
--   stories (which the story_bank already handles) and meant to be
--   surfaced via topical match in G3 interview prep + G7 cover-letter
--   generation, so we don't re-derive them from cv.md every time.
--
--   Examples:
--     • "Launched Daraz Marketplace 0→$50M TPV in 18 months
--        (https://daraz.pk/launch)"
--     • "Cut cart-abandonment 18% via 1-step checkout (Q3 2023 H1 review)"
--     • "Wrote LinkedIn thought-leadership post on PSPs
--        (12,000 views, 80 comments)"
--
-- Cost target: $0 per use (CRUD + semantic search, no LLM calls). One-time
--   embedding cost on insert (~$0.0001/proof point via
--   text-embedding-3-small).
--
-- Apply order
--   …021 → 022 (story_bank_not_null sibling) → 023 (profile_master voice
--   calibration + writing_samples, G11) → 024 (this, proof_points) → 025+.
--
-- ──────────────────────────────────────────────────────────────────────────
-- Design choices
--
-- 1. Composite-fact shape, not STAR-shape. Proof points are deliberately
--    crisp; they hold the proof statement (content), optional setup
--    (context), and the originating link (url). The structured fields
--    that ARE present — kind, tags, quantified_metrics — are the dials
--    G3/G7 pull on to filter retrieval.
--
-- 2. Embedding column at row level (vector(1536)) so the RPC can rank by
--    pgvector cosine distance. Mirrors story_bank's pattern exactly so
--    callers can swap retrieval idioms without re-learning.
--
-- 3. outcome_score is a Bayesian credit counter — same pattern as
--    story_bank.outcome_score and company_knowledge.outcome_score.
--    outcome_to_persona increments it when an interview or application
--    that cited this proof_point produces a callback/offer.
--
-- 4. source field is small enum-ish; we lean on TEXT (not an enum) so
--    new sources (g4_linkedin_post, extracted_from_cv, user_manual_add,
--    later imports) don't need schema migrations.
--
-- 5. RLS: every row has user_id NOT NULL. The single policy allows
--    auth.uid() = user_id OR auth.uid() IS NULL (service_role bypass).
--    Same shape as story_bank's policy stack.
--
-- 6. Indexes:
--      idx_proof_points_user            → core RLS-friendly lookup
--      idx_proof_points_kind            → kind pre-filter
--      idx_proof_points_tags (gin)      → tag pre-filter (G3/G7 will
--                                          often filter by tag set first)
--      idx_proof_points_embedding       → ivfflat lists=100 (same
--                                          heuristic as story_bank, sized
--                                          for ~10-50 rows/user)
--
-- pgvector verified ON (extversion=0.8.0). pgcrypto ON (gen_random_uuid()).
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── 1. Table ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.proof_points (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES public.users(id),
    content             TEXT NOT NULL,                  -- the proof statement
    context             TEXT,                           -- optional setup
    url                 TEXT,                           -- optional source link
    kind                TEXT NOT NULL,                  -- 'launch' | 'metric' | 'thought_leadership' | 'milestone' | 'press' | 'other'
    tags                TEXT[] DEFAULT '{}'::TEXT[],
    quantified_metrics  TEXT[] DEFAULT '{}'::TEXT[],
    embedding           VECTOR(1536),
    outcome_score       INTEGER NOT NULL DEFAULT 0,
    source              TEXT,                           -- 'user_manual_add' | 'extracted_from_cv' | 'g4_linkedin_post'
    source_experience_id BIGINT,                        -- FK back to profile_experience for identity-lock breadcrumb
    source_draft_id     UUID,                           -- FK back to linkedin_drafts when source='g4_linkedin_post'
    content_hash        TEXT,                           -- sha256 of normalised content — extractor idempotency
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT proof_points_kind_chk CHECK (kind IN
        ('launch','metric','thought_leadership','milestone','press','other'))
);

-- ── 2. Indexes ──────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_proof_points_user
    ON public.proof_points (user_id);

CREATE INDEX IF NOT EXISTS idx_proof_points_kind
    ON public.proof_points (user_id, kind);

CREATE INDEX IF NOT EXISTS idx_proof_points_tags
    ON public.proof_points USING gin (tags);

CREATE INDEX IF NOT EXISTS idx_proof_points_embedding
    ON public.proof_points USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Idempotency for extractor: dedup by (user_id, content_hash) when set.
CREATE UNIQUE INDEX IF NOT EXISTS proof_points_user_content_hash_unique
    ON public.proof_points (user_id, content_hash)
    WHERE content_hash IS NOT NULL;

-- ── 3. updated_at trigger ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.tg_proof_points_set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS proof_points_set_updated_at ON public.proof_points;
CREATE TRIGGER proof_points_set_updated_at
    BEFORE UPDATE ON public.proof_points
    FOR EACH ROW EXECUTE FUNCTION public.tg_proof_points_set_updated_at();

-- ── 4. RLS ──────────────────────────────────────────────────────────────
ALTER TABLE public.proof_points ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS proof_points_user_isolation ON public.proof_points;
CREATE POLICY proof_points_user_isolation ON public.proof_points
    FOR ALL USING (user_id = auth.uid() OR auth.uid() IS NULL);

-- ── 5. search_proof_points_v2 RPC ───────────────────────────────────────
-- Mirrors search_story_bank_v2 — same call shape minus STAR fields, plus
-- the proof-point-specific content/context/url/kind/tags fields.
-- SECURITY DEFINER + EXECUTE granted to authenticated + service_role only,
-- REVOKED from PUBLIC. Same posture as search_story_bank_v2.

DROP FUNCTION IF EXISTS public.search_proof_points_v2(uuid, vector, integer, text[], text[]);

CREATE OR REPLACE FUNCTION public.search_proof_points_v2(
    p_user_id          UUID,
    p_query_embedding  VECTOR(1536),
    p_k                INTEGER DEFAULT 5,
    p_kind_filter      TEXT[] DEFAULT NULL,
    p_tag_filter       TEXT[] DEFAULT NULL
)
RETURNS TABLE (
    id                   UUID,
    user_id              UUID,
    content              TEXT,
    context              TEXT,
    url                  TEXT,
    kind                 TEXT,
    tags                 TEXT[],
    quantified_metrics   TEXT[],
    outcome_score        INTEGER,
    source               TEXT,
    source_experience_id BIGINT,
    source_draft_id      UUID,
    created_at           TIMESTAMPTZ,
    updated_at           TIMESTAMPTZ,
    similarity           FLOAT
)
LANGUAGE plpgsql STABLE SECURITY DEFINER AS $$
BEGIN
    RETURN QUERY
    SELECT
        pp.id, pp.user_id, pp.content, pp.context, pp.url, pp.kind,
        pp.tags, pp.quantified_metrics, pp.outcome_score, pp.source,
        pp.source_experience_id, pp.source_draft_id,
        pp.created_at, pp.updated_at,
        (1 - (pp.embedding <=> p_query_embedding))::FLOAT AS similarity
    FROM public.proof_points pp
    WHERE pp.user_id = p_user_id
      AND pp.embedding IS NOT NULL
      AND (p_kind_filter IS NULL OR pp.kind = ANY(p_kind_filter))
      AND (p_tag_filter IS NULL OR pp.tags && p_tag_filter)
    ORDER BY
        pp.embedding <=> p_query_embedding,
        pp.outcome_score DESC,
        pp.created_at DESC
    LIMIT p_k;
END;
$$;

REVOKE ALL ON FUNCTION public.search_proof_points_v2(uuid, vector, integer, text[], text[]) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.search_proof_points_v2(uuid, vector, integer, text[], text[]) TO authenticated, service_role;

-- ── 6. Documentation ────────────────────────────────────────────────────
COMMENT ON TABLE public.proof_points IS
    'Tier 4 (2026-05-12, roadmap §6.4): user-owned proof-point library. '
    'Crisp facts (launches, metrics, posts) tagged + embedded so G3/G7 '
    'can pull topical matches during interview prep + cover-letter '
    'generation without re-deriving from cv.md. Semantic search via '
    'search_proof_points_v2 (pgvector cosine, text-embedding-3-small). '
    'outcome_score is a Bayesian credit counter — outcome_to_persona '
    'increments it when a cited proof point leads to a successful outcome.';

COMMENT ON COLUMN public.proof_points.content IS
    'The proof statement itself — short, scannable, ready to drop into an '
    'interview answer or cover letter sentence.';

COMMENT ON COLUMN public.proof_points.context IS
    'Optional longer context / setup. Surfaced when the proof point is '
    'expanded into a full talking point.';

COMMENT ON COLUMN public.proof_points.url IS
    'Optional source link (article, post, launch page). Adds verification '
    'value when surfaced in a cover letter.';

COMMENT ON COLUMN public.proof_points.kind IS
    'launch | metric | thought_leadership | milestone | press | other. '
    'Drives kind_filter on retrieval and shapes UI rendering.';

COMMENT ON COLUMN public.proof_points.tags IS
    'Topical tags ([fintech, product-launch, pricing]). GIN-indexed for '
    'cheap pre-filter ("show me fintech proof points") before vector rank.';

COMMENT ON COLUMN public.proof_points.quantified_metrics IS
    'Concrete numbers in the proof statement (["$50M TPV","18 months"]). '
    'IDENTITY LOCK: extractor must verify each metric appears in '
    'profile_experience.highlights/summary (lowercase) — fabricated '
    'metrics are dropped before insert.';

COMMENT ON COLUMN public.proof_points.outcome_score IS
    'Bayesian credit from G3/G7 outcomes. Higher = more downstream '
    'success. Used as tiebreaker when multiple proof points tie on '
    'cosine similarity.';

COMMENT ON COLUMN public.proof_points.source IS
    'Provenance: user_manual_add | extracted_from_cv | g4_linkedin_post. '
    'Drives identity-lock auditing — every non-manual row must trace back '
    'to a real profile_experience or linkedin_drafts row.';

COMMENT ON COLUMN public.proof_points.source_experience_id IS
    'profile_experience.id FK breadcrumb when source=extracted_from_cv. '
    'Required for cite:knowledge_id walk-back in outcome attribution.';

COMMENT ON COLUMN public.proof_points.source_draft_id IS
    'linkedin_drafts.id FK breadcrumb when source=g4_linkedin_post. '
    'Required for cite:knowledge_id walk-back.';

COMMENT ON COLUMN public.proof_points.content_hash IS
    'sha256 of normalised content text. Extractor uses this to dedupe '
    'across re-runs (same highlight should produce same proof point). '
    'Unique partial index enforces the dedup invariant at the DB layer.';

NOTIFY pgrst, 'reload schema';

COMMIT;
