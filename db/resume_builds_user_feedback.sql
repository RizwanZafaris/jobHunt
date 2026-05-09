-- ─────────────────────────────────────────────────────────────────────────
-- db/resume_builds_user_feedback.sql — User-facing resume controls
--
-- Adds the columns the dashboard needs to:
--   1. View resume markdown (rendered on /jobs/[id]/resume)
--   2. Save user-edited markdown over the agent draft
--   3. Capture a 1-5 star rating + free-text feedback per build
--   4. Track when the user last touched the build
--
-- Idempotent: safe to re-apply via apply_migration. The user-facing
-- columns layer on top of the existing G2-graph fields without changing
-- any agent code path (graph still writes resume_pdf_url / cover_email_md
-- as before; user edits live in user_edited_md and override on display).
-- ─────────────────────────────────────────────────────────────────────────

ALTER TABLE resume_builds
    ADD COLUMN IF NOT EXISTS user_rating       SMALLINT,
    ADD COLUMN IF NOT EXISTS user_feedback     TEXT,
    ADD COLUMN IF NOT EXISTS user_edited_md    TEXT,
    ADD COLUMN IF NOT EXISTS user_edited_at    TIMESTAMPTZ;

-- 1-5 stars only (NULL = unrated)
ALTER TABLE resume_builds
    DROP CONSTRAINT IF EXISTS resume_builds_user_rating_range;
ALTER TABLE resume_builds
    ADD CONSTRAINT resume_builds_user_rating_range
    CHECK (user_rating IS NULL OR (user_rating BETWEEN 1 AND 5));

-- Index for the dashboard's "show me builds I haven't reviewed yet" query
CREATE INDEX IF NOT EXISTS idx_resume_builds_user_rating_null
    ON resume_builds (job_id, created_at DESC)
    WHERE user_rating IS NULL AND status = 'converged';
