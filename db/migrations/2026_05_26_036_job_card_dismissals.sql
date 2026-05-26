-- 2026_05_26_036_job_card_dismissals.sql
--
-- User-initiated dismissal/snooze of /today job cards.
--
-- Root cause being fixed: the Adyen stale-card bug. Before this migration
-- the only way a job card disappeared from /today was (a) the user applied,
-- (b) the URL closed (validator stamps posting_closed_at), (c) validation
-- failed. None of these fire for evergreen postings that the user has
-- already seen and consciously skipped.
--
-- This table lets the user say "not interested" (snoozed_until = NULL =
-- permanent dismissal) or "ask again in N days" (snoozed_until = now() + N).
--
-- The /today builder filters out rows in this table where
-- snoozed_until IS NULL OR snoozed_until > now().
--
-- Idempotent + transactional.

BEGIN;

CREATE TABLE IF NOT EXISTS job_card_dismissals (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL,
    job_id          bigint NOT NULL,
    dismissed_at    timestamptz NOT NULL DEFAULT now(),
    snoozed_until   timestamptz,
    reason          text,           -- 'not_interested' | 'maybe_later' | 'wrong_seniority' | 'wrong_location' | 'other'
    notes           text,           -- free-form, optional
    CONSTRAINT fk_user
        FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE,
    CONSTRAINT uq_user_job UNIQUE (user_id, job_id),
    CONSTRAINT chk_reason
        CHECK (reason IS NULL OR reason IN
            ('not_interested', 'maybe_later', 'wrong_seniority',
             'wrong_location', 'wrong_comp', 'closed_already', 'other'))
);

-- Hot query: filter active dismissals for a user. Partial index keeps
-- it tiny (only active dismissals indexed).
CREATE INDEX IF NOT EXISTS idx_dismissals_active
    ON job_card_dismissals (user_id, job_id)
    WHERE snoozed_until IS NULL OR snoozed_until > now();

-- For analytics — surface which reasons dominate per user.
CREATE INDEX IF NOT EXISTS idx_dismissals_user_reason
    ON job_card_dismissals (user_id, reason)
    WHERE reason IS NOT NULL;

-- Row Level Security — multi-tenant from day 1.
ALTER TABLE job_card_dismissals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_dismissals_select ON job_card_dismissals;
CREATE POLICY p_dismissals_select ON job_card_dismissals
    FOR SELECT USING (user_id = auth.uid());

DROP POLICY IF EXISTS p_dismissals_insert ON job_card_dismissals;
CREATE POLICY p_dismissals_insert ON job_card_dismissals
    FOR INSERT WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS p_dismissals_update ON job_card_dismissals;
CREATE POLICY p_dismissals_update ON job_card_dismissals
    FOR UPDATE USING (user_id = auth.uid())
                 WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS p_dismissals_delete ON job_card_dismissals;
CREATE POLICY p_dismissals_delete ON job_card_dismissals
    FOR DELETE USING (user_id = auth.uid());

COMMENT ON TABLE job_card_dismissals IS
    'User dismissal/snooze state for /today job cards. snoozed_until NULL = permanent dismissal; snoozed_until > now() = active snooze; snoozed_until <= now() = snooze expired (re-surface).';

COMMIT;
