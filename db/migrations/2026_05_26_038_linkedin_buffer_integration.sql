-- 2026_05_26_038_linkedin_buffer_integration.sql
--
-- Buffer integration for LinkedIn auto-posting (opt-in per tenant).
--
-- Design principles:
--   • Auto-post is OPT-IN per tenant — never the default.
--   • A draft must still pass status='approved' before it can be
--     scheduled to Buffer (preserves G4's HITL principle).
--   • OAuth tokens are user-owned, encrypted at rest (Supabase Vault
--     where available; otherwise pgcrypto + KEK).
--   • Buffer is one auto-post channel — Buffer-down must not break
--     manual copy/paste. The linkedin_drafts flow is unchanged when
--     Buffer is not configured.
--
-- Idempotent + transactional.

BEGIN;

-- ─── 1. Buffer OAuth tokens (one row per user, per buffer-profile) ────────
CREATE TABLE IF NOT EXISTS buffer_oauth_tokens (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL,
    buffer_profile_id   text NOT NULL,           -- Buffer's LinkedIn profile id
    buffer_profile_name text,                    -- "Rizwan Zafar — LinkedIn"
    access_token        text NOT NULL,           -- ENCRYPT in production
    refresh_token       text,                    -- ENCRYPT
    expires_at          timestamptz,
    scopes              text[],
    connected_at        timestamptz NOT NULL DEFAULT now(),
    last_used_at        timestamptz,
    revoked_at          timestamptz,
    CONSTRAINT fk_user
        FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE,
    CONSTRAINT uq_user_profile UNIQUE (user_id, buffer_profile_id)
);

CREATE INDEX IF NOT EXISTS idx_buffer_tokens_active
    ON buffer_oauth_tokens (user_id)
    WHERE revoked_at IS NULL;

ALTER TABLE buffer_oauth_tokens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS p_buftok_select ON buffer_oauth_tokens;
CREATE POLICY p_buftok_select ON buffer_oauth_tokens
    FOR SELECT USING (user_id = auth.uid());

DROP POLICY IF EXISTS p_buftok_insert ON buffer_oauth_tokens;
CREATE POLICY p_buftok_insert ON buffer_oauth_tokens
    FOR INSERT WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS p_buftok_update ON buffer_oauth_tokens;
CREATE POLICY p_buftok_update ON buffer_oauth_tokens
    FOR UPDATE USING (user_id = auth.uid())
                 WITH CHECK (user_id = auth.uid());

DROP POLICY IF EXISTS p_buftok_delete ON buffer_oauth_tokens;
CREATE POLICY p_buftok_delete ON buffer_oauth_tokens
    FOR DELETE USING (user_id = auth.uid());


-- ─── 2. Per-draft Buffer scheduling state ─────────────────────────────────
ALTER TABLE linkedin_drafts
    ADD COLUMN IF NOT EXISTS buffer_post_id          text,
    ADD COLUMN IF NOT EXISTS buffer_status           text,
    ADD COLUMN IF NOT EXISTS buffer_scheduled_at     timestamptz,
    ADD COLUMN IF NOT EXISTS buffer_posted_at        timestamptz,
    ADD COLUMN IF NOT EXISTS buffer_error            text,
    ADD COLUMN IF NOT EXISTS buffer_permalink        text;

ALTER TABLE linkedin_drafts
    DROP CONSTRAINT IF EXISTS chk_buffer_status;
ALTER TABLE linkedin_drafts
    ADD CONSTRAINT chk_buffer_status
    CHECK (buffer_status IS NULL OR buffer_status IN
        ('pending', 'scheduled', 'posted', 'failed', 'cancelled'));

CREATE INDEX IF NOT EXISTS idx_drafts_buffer_pending
    ON linkedin_drafts (user_id, buffer_scheduled_at)
    WHERE buffer_status IN ('pending', 'scheduled');

COMMENT ON COLUMN linkedin_drafts.buffer_post_id IS
    'Buffer-side update id (returned from POST /updates/create.json).';
COMMENT ON COLUMN linkedin_drafts.buffer_status IS
    'pending = enqueued locally, scheduled = accepted by Buffer, posted = published to LinkedIn, failed = Buffer rejected, cancelled = user undid before publish.';


-- ─── 3. Tenant-level Buffer toggle on profile_master ──────────────────────
-- profile_master is the single per-user settings table.
ALTER TABLE profile_master
    ADD COLUMN IF NOT EXISTS buffer_autopost_enabled boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS buffer_default_profile_id text;

COMMENT ON COLUMN profile_master.buffer_autopost_enabled IS
    'OPT-IN per-tenant flag. When false, /linkedin/drafts/{id}/schedule-to-buffer returns 403 even if OAuth is connected. Defaults false so multi-tenant rollout cannot accidentally enable auto-post for any existing user.';

COMMIT;
