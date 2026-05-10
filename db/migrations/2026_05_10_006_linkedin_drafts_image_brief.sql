-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_006 — image_brief column for LinkedIn drafts
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   LinkedIn posts with images get ~2× reach vs text-only. Adds a JSONB
--   column storing the structured image-creation brief produced by the
--   new G4 image_brief node. Schema in agents/g4_linkedin_graph.py:
--
--     {
--       "kind": "data_viz" | "screenshot_quote" | "infographic"
--             | "diagram" | "reference_news_image",
--       "prompt": "<text-to-image prompt; specific subject, composition,
--                   palette, type style; under 100 words>",
--       "composition_notes": "<aspect ratio + composition rule>",
--       "data_anchors": ["<each number/claim the image illustrates>"],
--       "reference_url": "<URL of source image / news article; null if N/A>",
--       "alt_text": "<one-sentence accessibility description>",
--       "recommended_provider": "dall-e-3" | "imagen-3" | "midjourney"
--                             | "stock_photo" | "screenshot_only",
--       "ai_disclosure_recommended": true | false,
--       "rationale": "<one line — why this kind for this post>"
--     }
--
--   The engine BRIEFS, it does NOT generate. User picks the provider and
--   either generates or pulls the reference. Rationale: AI-generated
--   LinkedIn images increasingly hurt credibility; a real source image
--   (when available) lands harder.
--
-- Idempotency
--   ADD COLUMN IF NOT EXISTS — safe to re-run.
--
-- Apply order
--   001 → 002 → 003 → 004 → 005 → 006 (this file)
--
-- Rollback
--   ALTER TABLE linkedin_drafts DROP COLUMN image_brief;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

ALTER TABLE linkedin_drafts
    ADD COLUMN IF NOT EXISTS image_brief jsonb;

COMMENT ON COLUMN linkedin_drafts.image_brief IS
    'G4 image_brief node output: structured creation brief for a LinkedIn post visual. See agents/g4_linkedin_graph.py::IMAGE_BRIEF_SYSTEM for schema.';

NOTIFY pgrst, 'reload schema';

COMMIT;
