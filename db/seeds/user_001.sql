-- ══════════════════════════════════════════════════════════════════════════
-- Seed: user_001 — Rizwan Zafar (founder)
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Insert the single seeded user that backfills every existing per-tenant
--   row created before multi-tenancy. The id is hard-coded (NOT random) so
--   db/migrations/2026_05_10_001_multi_tenancy.sql can use it as the
--   temporary ALTER ... DEFAULT for backfill.
--
-- Idempotency
--   ON CONFLICT (id) DO NOTHING — safe to re-run.
--
-- Source
--   Profile fields lifted from /Users/rizwanzafar/Desktop/jobHunt/cv.md.
--   GitHub URL is not present in cv.md → left NULL with a TODO; update
--   when added to the CV.
-- ══════════════════════════════════════════════════════════════════════════

INSERT INTO users (
    id,
    email,
    full_name,
    linkedin_url,
    github_url,
    plan,
    is_admin,
    created_at,
    updated_at
)
VALUES (
    '00000000-0000-0000-0000-000000000001'::uuid,
    'rizwanzaffar.pk@gmail.com',
    'Rizwan Zafar',
    'https://linkedin.com/in/rizwanzafar',
    NULL,  -- TODO: add github_url to cv.md, then update via UPDATE users SET github_url = '...' WHERE id = '00000000-0000-0000-0000-000000000001';
    'lifetime',
    TRUE,
    NOW(),
    NOW()
)
ON CONFLICT (id) DO NOTHING;
