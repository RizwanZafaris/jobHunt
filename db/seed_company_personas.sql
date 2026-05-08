-- ══════════════════════════════════════════════════════════════════════════
-- Bootstrap company_personas from existing company_knowledge
-- Run AFTER db/multi_llm_schema.sql.
-- ══════════════════════════════════════════════════════════════════════════
--
-- WHAT THIS DOES
--   For each company that has all 5 recruitment-intel sections in
--   company_knowledge (recruitment_process, resume_dos_donts, ats_signals,
--   interview_format, hiring_signals), assemble a system_prompt_template
--   and an empty ats_keyword_bank skeleton, and insert a row into
--   company_personas.
--
-- WHY THIS APPROACH
--   The ATS keyword bank should be structured (required[], boost[], banned[])
--   but the live company_knowledge.ats_signals rows are unstructured
--   prose. Extracting structure requires an LLM call — that's done at
--   runtime by G1's persona_synthesizer node, not here. This seed just
--   gets the row into existence with the prose already concatenated so
--   G2's Insider Expert has *something* on day one.
--
-- QUALITY GATE
--   Skip sections whose content begins with "Unknown — insufficient data"
--   (a real pattern in the live data — Mastercard's interview_format is
--   exactly that string). If ANY of the 5 recruitment sections is Unknown,
--   we still create the persona but flag it via metadata.persona_quality.
--
-- IDEMPOTENCE
--   ON CONFLICT (company_name) DO UPDATE — safe to re-run.
-- ══════════════════════════════════════════════════════════════════════════

WITH eligible_companies AS (
    -- Companies with all 5 recruitment-intel sections present
    SELECT company_name
    FROM company_knowledge
    WHERE section IN (
        'recruitment_process',
        'resume_dos_donts',
        'ats_signals',
        'interview_format',
        'hiring_signals'
    )
    GROUP BY company_name
    HAVING COUNT(DISTINCT section) = 5
),
sections_pivoted AS (
    SELECT
        ck.company_name,
        c.id AS company_id,
        MAX(CASE WHEN ck.section = 'recruitment_process' THEN ck.content END) AS recruitment_process,
        MAX(CASE WHEN ck.section = 'resume_dos_donts'    THEN ck.content END) AS resume_dos_donts,
        MAX(CASE WHEN ck.section = 'ats_signals'         THEN ck.content END) AS ats_signals,
        MAX(CASE WHEN ck.section = 'interview_format'    THEN ck.content END) AS interview_format,
        MAX(CASE WHEN ck.section = 'hiring_signals'      THEN ck.content END) AS hiring_signals,
        -- Quality grade: count of "Unknown — insufficient data" sections
        COUNT(*) FILTER (
            WHERE ck.content ILIKE 'Unknown%insufficient data%'
        ) AS unknown_sections
    FROM company_knowledge ck
    JOIN eligible_companies ec ON ec.company_name = ck.company_name
    LEFT JOIN companies c ON c.name = ck.company_name
    WHERE ck.section IN (
        'recruitment_process',
        'resume_dos_donts',
        'ats_signals',
        'interview_format',
        'hiring_signals'
    )
    GROUP BY ck.company_name, c.id
)
INSERT INTO company_personas (
    company_id,
    company_name,
    system_prompt_template,
    ats_keyword_bank,
    success_patterns,
    failure_patterns,
    n_examples_used,
    persona_version,
    metadata
)
SELECT
    sp.company_id,
    sp.company_name,
    -- system_prompt_template: assemble from recruitment-intel sections.
    -- Each section is wrapped with a header so the Insider Expert node
    -- can scan-read it without LLM-side parsing.
    format(
        E'You are the Head of an Expert Recruitment Agency that has placed senior product leaders at %s and similar fintechs.\n\nYou know %s''s recruitment process, ATS, and hiring bar in detail. Use the briefing below as ground truth when advising the candidate on positioning.\n\n═══ %s — RECRUITMENT PROCESS ═══\n%s\n\n═══ %s — RESUME DO''S AND DON''TS ═══\n%s\n\n═══ %s — ATS SIGNALS (KEYWORDS & PHRASES) ═══\n%s\n\n═══ %s — INTERVIEW FORMAT ═══\n%s\n\n═══ %s — HIRING SIGNALS (WHAT THEY VALUE) ═══\n%s\n\nWhen any of these sections says "Unknown — insufficient data", apply best-practice fintech hiring norms instead. Never invent specific facts about %s that are not in the briefing above.\n\nBe direct, concrete, no fluff. Recommend only changes that are 100%% truthful to the candidate''s actual experience.',
        sp.company_name, sp.company_name,
        sp.company_name, COALESCE(sp.recruitment_process, '(not yet researched)'),
        sp.company_name, COALESCE(sp.resume_dos_donts,    '(not yet researched)'),
        sp.company_name, COALESCE(sp.ats_signals,         '(not yet researched)'),
        sp.company_name, COALESCE(sp.interview_format,    '(not yet researched)'),
        sp.company_name, COALESCE(sp.hiring_signals,      '(not yet researched)'),
        sp.company_name
    ),
    -- ats_keyword_bank: skeleton — populated structurally by the
    -- persona_synthesizer LLM node later. Storing the raw signals here
    -- so it's available even before the synthesizer runs.
    jsonb_build_object(
        'required', '[]'::jsonb,
        'boost',    '[]'::jsonb,
        'banned',   '[]'::jsonb,
        'raw_signals', sp.ats_signals
    ),
    '[]'::jsonb,                      -- success_patterns: empty until outcomes flow in
    '[]'::jsonb,                      -- failure_patterns: empty until outcomes flow in
    0,                                 -- n_examples_used: zero outcomes seeded
    1,                                 -- persona_version: v1
    jsonb_build_object(
        'persona_quality',
        CASE
            WHEN sp.unknown_sections = 0 THEN 'high'
            WHEN sp.unknown_sections <= 2 THEN 'medium'
            ELSE 'low'
        END,
        'unknown_sections', sp.unknown_sections,
        'seeded_from',      'company_knowledge',
        'seeded_at',        NOW()
    )
FROM sections_pivoted sp
ON CONFLICT (company_name) DO UPDATE SET
    system_prompt_template = EXCLUDED.system_prompt_template,
    ats_keyword_bank       = EXCLUDED.ats_keyword_bank,
    metadata               = EXCLUDED.metadata,
    last_synthesized_at    = NOW(),
    persona_version        = company_personas.persona_version + 1;

-- ── Sanity output ───────────────────────────────────────────────────────
-- After running, check the result:
--   SELECT company_name,
--          persona_version,
--          metadata->>'persona_quality' AS quality,
--          (metadata->>'unknown_sections')::int AS unknowns,
--          LENGTH(system_prompt_template) AS prompt_chars
--   FROM company_personas
--   ORDER BY metadata->>'persona_quality' DESC, company_name;
