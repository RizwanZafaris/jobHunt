-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_10_008 — Outcome credits + persona versions + tutor chat
-- ──────────────────────────────────────────────────────────────────────────
-- Purpose
--   Closes the audit's #1 unfilled wedge (docs/AUDIT_360_SYNTHESIS.md
--   §4 P1.3 outcome-conditioned RAG; §3 P1.5 persona evolution measured).
--
--   Three tables + two columns on company_knowledge:
--
--     1. knowledge_outcome_credits  — every interview / resume outcome
--        propagates Bayesian credit back to the company_knowledge rows
--        that were cited in the resume's agent_transcript. Read by the
--        outcome-conditioned re-ranker; written by
--        agents/outcome_to_persona.credit_outcome.
--
--     2. persona_versions            — append-only history of every
--        company_personas snapshot. Drives the persona-evolution timeline
--        UI (P1.5) and lets us roll back a bad evolution.
--
--     3. interview_tutor_messages    — chat tutor history per interview_prep.
--        Lets us reconstruct conversations across page reloads + audit
--        what the tutor said.
--
--   Plus two columns on company_knowledge:
--     - outcome_score        real    DEFAULT 0.5  (clamped [0, 1])
--     - outcome_credit_count int     DEFAULT 0    (denormalised count)
--
--   These are the materialised aggregates used by search_company_knowledge_v2
--   when it eventually ships. For now they're written by credit_outcome and
--   readable by anything that wants to see the current outcome bias.
--
-- Multi-tenancy
--   Every row carries `user_id uuid not null references users(id)`.
--   RLS policies follow the audit pattern (auth.uid() = user_id).
--   Service-role bypasses RLS — api/server.py uses service role today.
--
-- Idempotency
--   Every CREATE / ALTER guarded by IF NOT EXISTS / pg_constraint check.
--   Re-running is a no-op.
--
-- Apply order
--   001 (multi-tenancy) → 002 (status enum) → 003 (jobs_runs) →
--   004 (referral graph) → 005/006 (linkedin) → 007 (jobs.posting_closed_at) →
--   008 (this).
--
--   Hard requirement: 001 must have populated user_id on
--     interview_outcomes, resume_outcomes, company_knowledge,
--     company_personas, interview_prep
--   before this runs (it does — the migration backfills user_001 on every
--   per-tenant table).
--
-- DO NOT apply directly. The user reviews + applies via apply_migration.
--
-- Rollback (destructive — only safe before any outcome credit has been
-- written to a paying user's data):
--   DROP TABLE interview_tutor_messages;
--   DROP TABLE persona_versions;
--   DROP TABLE knowledge_outcome_credits;
--   ALTER TABLE company_knowledge
--     DROP COLUMN IF EXISTS outcome_score,
--     DROP COLUMN IF EXISTS outcome_credit_count;
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- pgcrypto is the source of gen_random_uuid() — already enabled in 001 but
-- safe to assert.
CREATE EXTENSION IF NOT EXISTS pgcrypto;


-- ══════════════════════════════════════════════════════════════════════════
-- knowledge_outcome_credits — Bayesian credit-assignment ledger
-- ══════════════════════════════════════════════════════════════════════════
-- One row per (outcome event × cited knowledge row). Ledger semantics: we
-- never UPDATE an existing row, we INSERT a new one and recompute
-- company_knowledge.outcome_score as the weighted mean across this user's
-- credits for that knowledge_id.
--
-- The XOR check enforces "exactly one of (interview_outcome_id, resume_outcome_id)
-- is set" so the ledger is unambiguously sourced.
--
-- delta range [-0.5, +0.5] is the audit's hard cap on per-event drift. The
-- aggregate outcome_score on company_knowledge is also clamped [0, 1]
-- in agents/outcome_to_persona.

CREATE TABLE IF NOT EXISTS knowledge_outcome_credits (
    id                       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    knowledge_id             UUID        NOT NULL REFERENCES company_knowledge(id) ON DELETE CASCADE,

    -- Outcome event source: exactly one of these is set.
    interview_outcome_id     UUID        REFERENCES interview_outcomes(id) ON DELETE CASCADE,
    resume_outcome_id        UUID        REFERENCES resume_outcomes(id)    ON DELETE CASCADE,

    -- Optional FK back to the resume_build whose retrieval cited this row
    -- (nullable because some credits could come from manual user feedback).
    resume_build_id          UUID        REFERENCES resume_builds(id) ON DELETE SET NULL,

    -- Credit assignment.
    delta                    REAL        NOT NULL CHECK (delta >= -0.5 AND delta <= 0.5),
    reason                   TEXT,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT knowledge_outcome_credits_xor_source
        CHECK (
            (interview_outcome_id IS NOT NULL AND resume_outcome_id IS NULL)
            OR (interview_outcome_id IS NULL AND resume_outcome_id IS NOT NULL)
        )
);

CREATE INDEX IF NOT EXISTS knowledge_outcome_credits_user_id_idx
    ON knowledge_outcome_credits (user_id);
CREATE INDEX IF NOT EXISTS knowledge_outcome_credits_knowledge_id_idx
    ON knowledge_outcome_credits (knowledge_id);
CREATE INDEX IF NOT EXISTS knowledge_outcome_credits_user_knowledge_idx
    ON knowledge_outcome_credits (user_id, knowledge_id);
-- Hot path: "all credits for this interview outcome event" used by the
-- worker if a credit_outcome run needs to be re-played.
CREATE INDEX IF NOT EXISTS knowledge_outcome_credits_interview_outcome_idx
    ON knowledge_outcome_credits (interview_outcome_id)
    WHERE interview_outcome_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS knowledge_outcome_credits_resume_outcome_idx
    ON knowledge_outcome_credits (resume_outcome_id)
    WHERE resume_outcome_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS knowledge_outcome_credits_created_at_idx
    ON knowledge_outcome_credits (created_at DESC);

-- ── RLS: knowledge_outcome_credits ────────────────────────────────────────
ALTER TABLE knowledge_outcome_credits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS knowledge_outcome_credits_select_own ON knowledge_outcome_credits;
DROP POLICY IF EXISTS knowledge_outcome_credits_insert_own ON knowledge_outcome_credits;
DROP POLICY IF EXISTS knowledge_outcome_credits_update_own ON knowledge_outcome_credits;
DROP POLICY IF EXISTS knowledge_outcome_credits_delete_own ON knowledge_outcome_credits;

CREATE POLICY knowledge_outcome_credits_select_own ON knowledge_outcome_credits
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY knowledge_outcome_credits_insert_own ON knowledge_outcome_credits
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY knowledge_outcome_credits_update_own ON knowledge_outcome_credits
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY knowledge_outcome_credits_delete_own ON knowledge_outcome_credits
    FOR DELETE USING (auth.uid() = user_id);


-- ══════════════════════════════════════════════════════════════════════════
-- company_knowledge.outcome_score + outcome_credit_count
-- ══════════════════════════════════════════════════════════════════════════
-- Materialised aggregate over knowledge_outcome_credits. Updated
-- transactionally inside agents/outcome_to_persona.credit_outcome.
-- 0.5 is "no signal yet" (Beta(α=1, β=1) prior expectation).
--
-- search_company_knowledge_v2 (separate migration when it ships) re-ranks
-- with `score = 0.7 * cosine + 0.3 * (outcome_score - 0.5)` per the audit.

ALTER TABLE company_knowledge
    ADD COLUMN IF NOT EXISTS outcome_score REAL NOT NULL DEFAULT 0.5;

ALTER TABLE company_knowledge
    ADD COLUMN IF NOT EXISTS outcome_credit_count INT NOT NULL DEFAULT 0;

-- Constrain outcome_score to [0, 1]. Idempotent via pg_constraint check.
DO $oc$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'company_knowledge_outcome_score_range'
    ) THEN
        EXECUTE 'ALTER TABLE company_knowledge '
                'ADD CONSTRAINT company_knowledge_outcome_score_range '
                'CHECK (outcome_score >= 0 AND outcome_score <= 1)';
    END IF;
END
$oc$;

-- Index for the eventual v2 retrieval RPC (descending so "best" is first).
CREATE INDEX IF NOT EXISTS company_knowledge_outcome_score_idx
    ON company_knowledge (company_name, outcome_score DESC);


-- ══════════════════════════════════════════════════════════════════════════
-- persona_versions — append-only history of company_personas snapshots
-- ══════════════════════════════════════════════════════════════════════════
-- One row per evolution event. company_personas.persona_version is the
-- pointer to the current head; persona_versions stores the full snapshot
-- of EVERY prior version so the persona-evolution dashboard can render
-- "what changed and why".
--
-- triggered_by:
--   'manual'              — user clicked "Refresh persona"
--   'cron'                — Sunday cron after enough outcomes accumulated
--   'outcome_threshold'   — auto-triggered by credit_outcome when N
--                           credits accumulate for a company
--   'deep_research'       — initial synthesis from persona_deep_research
--
-- diff_summary is a one-line "Added 'TC40' and 'Reg E disputes'; dropped
-- 'payments fraud' (generic, no callback signal)" — produced by Claude Opus
-- inside evolve_persona.

CREATE TABLE IF NOT EXISTS persona_versions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    persona_id          UUID        NOT NULL REFERENCES company_personas(id) ON DELETE CASCADE,
    version             INT         NOT NULL,

    -- Snapshot at this version.
    snapshot_md         TEXT,
    system_prompt_template TEXT,
    ats_keyword_bank    JSONB       DEFAULT '{}'::jsonb,
    success_patterns    JSONB       DEFAULT '[]'::jsonb,
    failure_patterns    JSONB       DEFAULT '[]'::jsonb,

    triggered_by        TEXT        CHECK (triggered_by IN (
                                        'manual',
                                        'cron',
                                        'outcome_threshold',
                                        'deep_research'
                                    )),
    diff_summary        TEXT,

    -- Audit: the outcome ids that fed the evolution (so we can show
    -- "this version was triggered by these outcomes" on the timeline).
    triggered_by_outcomes JSONB     DEFAULT '[]'::jsonb,
    cost_usd            NUMERIC(10, 6) DEFAULT 0,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (persona_id, version)
);

CREATE INDEX IF NOT EXISTS persona_versions_user_id_idx
    ON persona_versions (user_id);
CREATE INDEX IF NOT EXISTS persona_versions_persona_id_idx
    ON persona_versions (persona_id);
CREATE INDEX IF NOT EXISTS persona_versions_persona_version_idx
    ON persona_versions (persona_id, version DESC);
CREATE INDEX IF NOT EXISTS persona_versions_created_at_idx
    ON persona_versions (created_at DESC);

-- ── RLS: persona_versions ─────────────────────────────────────────────────
ALTER TABLE persona_versions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS persona_versions_select_own ON persona_versions;
DROP POLICY IF EXISTS persona_versions_insert_own ON persona_versions;
DROP POLICY IF EXISTS persona_versions_update_own ON persona_versions;
DROP POLICY IF EXISTS persona_versions_delete_own ON persona_versions;

CREATE POLICY persona_versions_select_own ON persona_versions
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY persona_versions_insert_own ON persona_versions
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY persona_versions_update_own ON persona_versions
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY persona_versions_delete_own ON persona_versions
    FOR DELETE USING (auth.uid() = user_id);


-- ══════════════════════════════════════════════════════════════════════════
-- interview_tutor_messages — per-prep chat history
-- ══════════════════════════════════════════════════════════════════════════
-- One row per chat message between the user and the tutor. Stored
-- per-interview_prep so the tutor's chat is anchored to a concrete prep
-- pack (and dies with the application_id when the application is deleted).
--
-- concept_level captures the depth the message was anchored at — lets the
-- UI render the "ladder" indicator (basics → intermediate → advanced)
-- next to messages and lets the tutor remember where the conversation
-- has ramped to.
--
-- cost_usd is per-message cost (only set for tutor responses).

CREATE TABLE IF NOT EXISTS interview_tutor_messages (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    interview_prep_id   UUID        NOT NULL REFERENCES interview_prep(id) ON DELETE CASCADE,

    role                TEXT        NOT NULL CHECK (role IN ('user', 'tutor', 'system')),
    content             TEXT        NOT NULL,
    concept_level       TEXT        CHECK (concept_level IN ('basics', 'intermediate', 'advanced')),

    -- Optional: which prep section this message references (likely_questions,
    -- star_stories, company_hooks, red_flag_questions, salary_negotiation).
    cited_section       TEXT,
    suggested_next      TEXT,

    cost_usd            NUMERIC(10, 6) DEFAULT 0,
    latency_ms          INT            DEFAULT 0,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS interview_tutor_messages_user_id_idx
    ON interview_tutor_messages (user_id);
CREATE INDEX IF NOT EXISTS interview_tutor_messages_prep_id_idx
    ON interview_tutor_messages (interview_prep_id);
-- Hot path: "render last 50 messages for this prep, oldest first".
CREATE INDEX IF NOT EXISTS interview_tutor_messages_prep_created_idx
    ON interview_tutor_messages (interview_prep_id, created_at);
CREATE INDEX IF NOT EXISTS interview_tutor_messages_user_created_idx
    ON interview_tutor_messages (user_id, created_at DESC);

-- ── RLS: interview_tutor_messages ─────────────────────────────────────────
ALTER TABLE interview_tutor_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS interview_tutor_messages_select_own ON interview_tutor_messages;
DROP POLICY IF EXISTS interview_tutor_messages_insert_own ON interview_tutor_messages;
DROP POLICY IF EXISTS interview_tutor_messages_update_own ON interview_tutor_messages;
DROP POLICY IF EXISTS interview_tutor_messages_delete_own ON interview_tutor_messages;

CREATE POLICY interview_tutor_messages_select_own ON interview_tutor_messages
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY interview_tutor_messages_insert_own ON interview_tutor_messages
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY interview_tutor_messages_update_own ON interview_tutor_messages
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY interview_tutor_messages_delete_own ON interview_tutor_messages
    FOR DELETE USING (auth.uid() = user_id);


-- ── reload PostgREST schema cache (Supabase) ──────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
