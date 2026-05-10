-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_002 — Canonical applications.status enum
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Replace the free-text applications.status column with a Postgres enum
--   so the kanban board and pipeline can rely on a fixed set of states.
--   Also normalises legacy Spanish values (the schema's default was
--   'Evaluada') and the English-but-inconsistent values found across the
--   codebase ('evaluated', 'qualified', 'interview', 'ghosted', etc.).
--
-- ──────────────────────────────────────────────────────────────────────────
-- Spanish → English mapping (and rationale)
-- ──────────────────────────────────────────────────────────────────────────
--   'Evaluada'        → 'researched'
--       The original default; in career-ops parlance it meant "we have
--       looked at this and scored it" but no resume has been generated and
--       nothing has been sent. 'researched' is the closest canonical state
--       in the new enum and matches docs/AUDIT_360_SYNTHESIS.md note.
--   'Aplicada'        → 'applied'
--   'Entrevistando'   → 'interviewing'
--   'Rechazada'       → 'rejected'
--   'Ofrecida'        → 'offered'
--   'Retirada'        → 'withdrawn'
--
-- ──────────────────────────────────────────────────────────────────────────
-- English-variant normalisation
-- ──────────────────────────────────────────────────────────────────────────
--   'evaluated'       → 'researched'   -- same intent as 'Evaluada'
--   'qualified'       → 'researched'   -- meta-critic emitted this; same gate
--   'new'             → 'new'          -- canonical
--   'interview'       → 'interviewing' -- application_tracker_agent uses singular
--   'offer'           → 'offered'      -- ditto
--   'accepted'        → 'offered'      -- collapsed; we can split later if needed
--   'ghosted'         → 'rejected'     -- best-effort; manual review may
--                                          re-tag rare cases.
--   NULL / ''         → 'new'
--
-- Anything outside this set is mapped to 'new' with a NOTICE so a human can
-- review the row by id afterwards.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── 1. Create the enum ───────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'application_status') THEN
        CREATE TYPE application_status AS ENUM (
            'new',
            'researched',
            'resume_ready',
            'applied',
            'interviewing',
            'offered',
            'rejected',
            'withdrawn'
        );
    END IF;
END
$$;

-- ── 2. Stage a normalised text column ────────────────────────────────────
-- We don't ALTER ... USING in one shot because (a) the old column has a
-- DEFAULT of 'Evaluada' that prevents direct casting, and (b) we want a
-- clear paper trail for unknown values. Instead: backfill into a new text
-- column, validate, then swap.
ALTER TABLE applications
    ADD COLUMN IF NOT EXISTS status_new TEXT;

-- Backfill status_new with the canonical mapping.
UPDATE applications
SET status_new = CASE
    -- Spanish → English
    WHEN status = 'Evaluada'      THEN 'researched'   -- Evaluada → researched (Spanish default; pre-resume gate)
    WHEN status = 'Aplicada'      THEN 'applied'      -- Aplicada → applied
    WHEN status = 'Entrevistando' THEN 'interviewing' -- Entrevistando → interviewing
    WHEN status = 'Rechazada'     THEN 'rejected'     -- Rechazada → rejected
    WHEN status = 'Ofrecida'      THEN 'offered'      -- Ofrecida → offered
    WHEN status = 'Retirada'      THEN 'withdrawn'    -- Retirada → withdrawn
    -- English-variant normalisation
    WHEN status = 'evaluated'     THEN 'researched'   -- evaluated → researched (same gate as Evaluada)
    WHEN status = 'qualified'     THEN 'researched'   -- qualified  → researched (meta-critic synonym)
    WHEN status = 'new'           THEN 'new'
    WHEN status = 'researched'    THEN 'researched'
    WHEN status = 'resume_ready'  THEN 'resume_ready'
    WHEN status = 'applied'       THEN 'applied'
    WHEN status = 'interview'     THEN 'interviewing' -- interview → interviewing (codebase used both)
    WHEN status = 'interviewing'  THEN 'interviewing'
    WHEN status = 'offer'         THEN 'offered'      -- offer → offered
    WHEN status = 'offered'       THEN 'offered'
    WHEN status = 'accepted'      THEN 'offered'      -- accepted → offered (collapsed; revisit if hire data needed)
    WHEN status = 'rejected'      THEN 'rejected'
    WHEN status = 'ghosted'       THEN 'rejected'     -- ghosted → rejected (best guess; manual review if needed)
    WHEN status = 'withdrawn'     THEN 'withdrawn'
    WHEN status IS NULL OR status = '' THEN 'new'
    ELSE 'new'  -- unknown legacy value → 'new'; see NOTICE below
END
WHERE status_new IS NULL;

-- Surface any rows that fell through the ELSE arm so a human can audit.
DO $$
DECLARE
    n_unknown INT;
BEGIN
    SELECT count(*) INTO n_unknown
    FROM applications a
    WHERE a.status IS NOT NULL
      AND a.status NOT IN (
        'Evaluada','Aplicada','Entrevistando','Rechazada','Ofrecida','Retirada',
        'evaluated','qualified','new','researched','resume_ready','applied',
        'interview','interviewing','offer','offered','accepted','rejected',
        'ghosted','withdrawn',''
      );
    IF n_unknown > 0 THEN
        RAISE NOTICE
            '[002_status_enum] % application(s) had an unrecognised status; mapped to ''new''. Run: SELECT id, status FROM applications WHERE status NOT IN (... mapped values ...);',
            n_unknown;
    END IF;
END
$$;

-- ── 3. Swap the columns ──────────────────────────────────────────────────
-- Drop the old default first so the type swap can proceed cleanly.
ALTER TABLE applications ALTER COLUMN status DROP DEFAULT;

-- Convert old `status` column to the enum, using status_new as the source.
ALTER TABLE applications
    ALTER COLUMN status TYPE application_status
    USING status_new::application_status;

-- New default: 'new'.
ALTER TABLE applications
    ALTER COLUMN status SET DEFAULT 'new'::application_status;
ALTER TABLE applications
    ALTER COLUMN status SET NOT NULL;

-- Drop the staging column.
ALTER TABLE applications DROP COLUMN IF EXISTS status_new;

-- ── 4. Belt-and-braces CHECK ─────────────────────────────────────────────
-- The enum already enforces validity, but adding a redundant CHECK lets
-- ORMs and inspection tooling discover the constraint without enum
-- introspection. Drop-then-add for idempotency.
ALTER TABLE applications
    DROP CONSTRAINT IF EXISTS applications_status_valid;
ALTER TABLE applications
    ADD CONSTRAINT applications_status_valid
    CHECK (status IN (
        'new','researched','resume_ready','applied',
        'interviewing','offered','rejected','withdrawn'
    ));

-- ── 5. PostgREST cache reload ────────────────────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
