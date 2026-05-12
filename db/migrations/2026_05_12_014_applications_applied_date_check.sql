-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_014 — Require applied_date for post-apply statuses
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   BUG-027: 2 applications landed in production with status='rejected' but
--   applied_date IS NULL — causally impossible (a rejection implies the
--   application was sent, which implies an applied_date). Affected rows:
--     • 6469d8cd-5d67-4f1f-8e74-9e08b8ba31cd  (SuperApp, job 395)
--     • 7ce700f3-c61e-464d-9ef1-4438ffd41ec2  (Emirates NBD, job 99)
--   Both have already been backfilled out-of-band (applied_date := created_at).
--
--   This migration adds a CHECK constraint so the database refuses any
--   future row that enters a post-apply state without an applied_date.
--
-- Status semantics (application_status enum, ordered):
--     new, researched, resume_ready     — pre-apply  → applied_date may be NULL
--     applied, interviewing, offered,
--     rejected, withdrawn                — post-apply → applied_date REQUIRED
--
--   Rationale for including 'withdrawn' as post-apply:
--     If a candidate withdraws before applying, the row should never have
--     existed in the first place — there's nothing to withdraw from. In
--     practice the withdraw action is only available after `applied`.
--
-- Idempotency
--   ADD CONSTRAINT … IF NOT EXISTS is not supported in older Postgres, so
--   we wrap in a DO block that checks pg_constraint first.
--
-- Apply order
--   001 → … → 012 → 013(reserved) → 014 (this)
--
-- Rollback
--   ALTER TABLE applications DROP CONSTRAINT applications_applied_date_required;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── 1. defensive backfill — should already be done via MCP, but safe to
--      re-run (UPDATE-WHERE-NULL is a no-op once rows are clean). Keeps the
--      migration runnable from a fresh clone if MCP backfill was skipped.
UPDATE applications
   SET applied_date = created_at::date
 WHERE applied_date IS NULL
   AND status IN ('applied', 'interviewing', 'offered', 'rejected', 'withdrawn');

-- ── 2. add the CHECK constraint (idempotent via pg_constraint guard) ─────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'applications_applied_date_required'
           AND conrelid = 'public.applications'::regclass
    ) THEN
        ALTER TABLE applications
            ADD CONSTRAINT applications_applied_date_required
            CHECK (
                status IN ('new', 'researched', 'resume_ready')
                OR applied_date IS NOT NULL
            );
    END IF;
END $$;

COMMENT ON CONSTRAINT applications_applied_date_required ON applications IS
    'BUG-027 (2026-05-12): a row in any post-apply status (applied, '
    'interviewing, offered, rejected, withdrawn) must have applied_date set. '
    'Pre-apply statuses (new, researched, resume_ready) may have NULL.';

-- ── 3. reload PostgREST schema cache (Supabase) ──────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
