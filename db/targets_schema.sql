-- ══════════════════════════════════════════════════════════════════════════
-- Phase D: Target Companies + Applications Board
-- Adds target tracking to companies + makes applications first-class.
-- Safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════

-- ── companies: add target tracking ───────────────────────────────────────
ALTER TABLE companies ADD COLUMN IF NOT EXISTS is_target BOOLEAN DEFAULT FALSE;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS priority TEXT DEFAULT 'medium';
ALTER TABLE companies ADD COLUMN IF NOT EXISTS category TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS target_added_at TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS last_scanned_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_companies_is_target ON companies(is_target) WHERE is_target = TRUE;
CREATE INDEX IF NOT EXISTS idx_companies_category ON companies(category);

-- ── applications: simplify status + add tracking ─────────────────────────
-- The original applications table uses 'Evaluada' as default; switch to 'evaluated'
-- and ensure we have all the kanban columns: new | evaluated | applied |
-- interviewing | offer | rejected | accepted
ALTER TABLE applications ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS resume_path TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS email_path TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS interview_path TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS readiness_score INTEGER;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS readiness_assessment TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES companies(id);

CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id);

-- Reload PostgREST cache
NOTIFY pgrst, 'reload schema';
