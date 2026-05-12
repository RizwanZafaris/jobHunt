-- ══════════════════════════════════════════════════════════════════════════
-- Migration: 2026_05_12_027 — Pattern Analytics views
-- ──────────────────────────────────────────────────────────────────────────
-- Audit Gap §4.3 (Pattern Analytics)
--
-- Purpose
--   career-ops has analyze-patterns.mjs (rejection clusters, funnel
--   conversion by archetype, cost-per-outcome). jobHunt has the raw
--   data (applications, jobs, resume_builds, resume_outcomes,
--   agent_call_log) and an existing per-company funnel view (025) but
--   no analysis layer that splits the funnel by job/company attribute.
--
--   This migration adds seven thin, well-commented per-user views that
--   feed `agents/pattern_analyzer.py` and the `/insights/analytics`
--   dashboard. Each view is per-user (multi-tenant safe, scoped via
--   user_id on the underlying tables) and read-only (no DML side
--   effects).
--
-- Conventions
--   * Every view starts with the `user_id` column so the API layer can
--     filter `.eq("user_id", current_user)` cheaply.
--   * Applications is the spine — funnel stages derive from
--     `applications.status` (enum: new | researched | resume_ready |
--     applied | interviewing | offered | rejected | withdrawn) joined
--     to `resume_outcomes` for finer-grained signals.
--   * Closed / phantom filtering follows the same pattern as the 025
--     fix (LEFT JOIN companies + posting_closed_at + validation_failed
--     guards), so analytics never surface dead jobs.
--   * Company size band is derived from `companies.headcount` (TEXT
--     band) since there is no `employee_count` integer column. The
--     band labels mirror what was already there: <50, 50-200, 200-1000,
--     1000+ (a missing headcount is bucketed as "unknown").
--
-- Apply order
--   025 (funnel view filter) → 026 (G8 offer_evaluations) → 027 (THIS).
--
-- Rollback
--   DROP VIEW IF EXISTS each of:
--     v_application_funnel, v_application_funnel_by_grade,
--     v_application_funnel_by_archetype,
--     v_application_funnel_by_company_size,
--     v_rejection_signals, v_resume_build_efficiency,
--     v_cost_per_outcome
--   They are computed; no rows are lost.
-- ══════════════════════════════════════════════════════════════════════════

BEGIN;

-- ── 1. v_application_funnel ────────────────────────────────────────────────
-- Per-user funnel snapshot across the five canonical stages. The numerator
-- of each stage is the count of applications that have reached AT LEAST
-- that stage (so `responded >= interviewed >= offered >= accepted`,
-- monotonically non-increasing). Conversion rates between adjacent stages
-- are computed in the API layer (we keep the view as raw counts so the
-- math is auditable).
--
-- Stage semantics (kept aligned with the applications.status enum):
--   applied     — status IN ('applied','interviewing','offered',
--                            'rejected','withdrawn')
--                 (anything that left "resume_ready"; "rejected" rolls
--                 forward because a rejection presupposes an application)
--   responded   — applied + recruiter responded (resume_outcomes) OR
--                 status IN ('interviewing','offered')
--   interviewed — status IN ('interviewing','offered') OR rounds_reached >= 1
--   offered     — status = 'offered' OR offer_received = TRUE
--   accepted    — offer received AND status NOT IN ('withdrawn','rejected')
--                 (we don't yet capture "candidate accepted" — proxy via
--                 final status that hasn't been withdrawn after offer)
DROP VIEW IF EXISTS public.v_application_funnel;
CREATE VIEW public.v_application_funnel AS
SELECT
    a.user_id,
    count(*) FILTER (
        WHERE a.status IN ('applied','interviewing','offered','rejected','withdrawn')
    )::int AS applied,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.recruiter_responded = TRUE
    )::int AS responded,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.interview_received = TRUE
           OR coalesce(ro.rounds_reached, 0) >= 1
    )::int AS interviewed,
    count(*) FILTER (
        WHERE a.status = 'offered' OR ro.offer_received = TRUE
    )::int AS offered,
    count(*) FILTER (
        WHERE (a.status = 'offered' OR ro.offer_received = TRUE)
          AND a.status NOT IN ('withdrawn','rejected')
    )::int AS accepted,
    count(*)::int AS total
FROM applications a
    LEFT JOIN resume_outcomes ro ON ro.application_id = a.id
GROUP BY a.user_id;

COMMENT ON VIEW public.v_application_funnel IS
    'Per-user funnel counts (applied → responded → interviewed → offered '
    '→ accepted). Conversion rates computed in pattern_analyzer.py from '
    'these counts. Added in migration 027 for audit Gap §4.3.';

GRANT SELECT ON public.v_application_funnel TO authenticated, service_role;


-- ── 2. v_application_funnel_by_grade ───────────────────────────────────────
-- Same funnel partitioned by the G5 letter_grade (A | B | C | D | F) on
-- the underlying job. Reveals whether high-grade applications convert
-- better than low (the audit's headline pattern: "A-grade applications
-- convert 3x better than B-grade").
--
-- Rows with letter_grade IS NULL are emitted as letter_grade='?' so the
-- dashboard can either show or hide them — we never silently drop.
DROP VIEW IF EXISTS public.v_application_funnel_by_grade;
CREATE VIEW public.v_application_funnel_by_grade AS
SELECT
    a.user_id,
    COALESCE(j.letter_grade, '?') AS letter_grade,
    count(*) FILTER (
        WHERE a.status IN ('applied','interviewing','offered','rejected','withdrawn')
    )::int AS applied,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.recruiter_responded = TRUE
    )::int AS responded,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.interview_received = TRUE
           OR coalesce(ro.rounds_reached, 0) >= 1
    )::int AS interviewed,
    count(*) FILTER (
        WHERE a.status = 'offered' OR ro.offer_received = TRUE
    )::int AS offered,
    count(*)::int AS total
FROM applications a
    LEFT JOIN jobs j ON j.id = a.job_id
    LEFT JOIN resume_outcomes ro ON ro.application_id = a.id
GROUP BY a.user_id, COALESCE(j.letter_grade, '?');

COMMENT ON VIEW public.v_application_funnel_by_grade IS
    'Funnel partitioned by jobs.letter_grade (A-F, NULL→"?"). Surfaces '
    'whether high-grade applications convert better. Audit Gap §4.3.';

GRANT SELECT ON public.v_application_funnel_by_grade TO authenticated, service_role;


-- ── 3. v_application_funnel_by_archetype ───────────────────────────────────
-- Funnel partitioned by jobs.archetype (PM | Manager | Staff | IC etc).
-- Surfaces which role types convert. NULL archetype is bucketed as
-- "unclassified" so the dashboard can render a single row for legacy
-- jobs whose archetype wasn't detected.
DROP VIEW IF EXISTS public.v_application_funnel_by_archetype;
CREATE VIEW public.v_application_funnel_by_archetype AS
SELECT
    a.user_id,
    COALESCE(j.archetype, 'unclassified') AS archetype,
    count(*) FILTER (
        WHERE a.status IN ('applied','interviewing','offered','rejected','withdrawn')
    )::int AS applied,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.recruiter_responded = TRUE
    )::int AS responded,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.interview_received = TRUE
           OR coalesce(ro.rounds_reached, 0) >= 1
    )::int AS interviewed,
    count(*) FILTER (
        WHERE a.status = 'offered' OR ro.offer_received = TRUE
    )::int AS offered,
    count(*)::int AS total
FROM applications a
    LEFT JOIN jobs j ON j.id = a.job_id
    LEFT JOIN resume_outcomes ro ON ro.application_id = a.id
GROUP BY a.user_id, COALESCE(j.archetype, 'unclassified');

COMMENT ON VIEW public.v_application_funnel_by_archetype IS
    'Funnel partitioned by jobs.archetype (PM/Manager/Staff/IC, '
    'NULL→"unclassified"). Audit Gap §4.3.';

GRANT SELECT ON public.v_application_funnel_by_archetype TO authenticated, service_role;


-- ── 4. v_application_funnel_by_company_size ────────────────────────────────
-- Funnel partitioned by company size band. `companies.headcount` is the
-- existing TEXT band column (values: <50, 50-200, 200-1000, 1000+). A
-- missing headcount becomes "unknown" so we never silently drop rows.
--
-- Note: the audit asks for `companies.employee_count` if it exists; that
-- column does NOT exist in the live schema (only the headcount band).
-- We use the band directly — it's still a useful partition.
DROP VIEW IF EXISTS public.v_application_funnel_by_company_size;
CREATE VIEW public.v_application_funnel_by_company_size AS
SELECT
    a.user_id,
    COALESCE(c.headcount, 'unknown') AS company_size_band,
    count(*) FILTER (
        WHERE a.status IN ('applied','interviewing','offered','rejected','withdrawn')
    )::int AS applied,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.recruiter_responded = TRUE
    )::int AS responded,
    count(*) FILTER (
        WHERE a.status IN ('interviewing','offered')
           OR ro.interview_received = TRUE
           OR coalesce(ro.rounds_reached, 0) >= 1
    )::int AS interviewed,
    count(*) FILTER (
        WHERE a.status = 'offered' OR ro.offer_received = TRUE
    )::int AS offered,
    count(*)::int AS total
FROM applications a
    LEFT JOIN jobs j ON j.id = a.job_id
    LEFT JOIN companies c ON c.id = j.company_id
        OR lower(c.name) = lower(a.company)
    LEFT JOIN resume_outcomes ro ON ro.application_id = a.id
GROUP BY a.user_id, COALESCE(c.headcount, 'unknown');

COMMENT ON VIEW public.v_application_funnel_by_company_size IS
    'Funnel partitioned by companies.headcount band (<50, 50-200, '
    '200-1000, 1000+, NULL→"unknown"). Audit Gap §4.3.';

GRANT SELECT ON public.v_application_funnel_by_company_size TO authenticated, service_role;


-- ── 5. v_rejection_signals ─────────────────────────────────────────────────
-- For each REJECTED application, surface the signals worth clustering on:
-- time-to-rejection (days), G2 polisher_score, G5 letter_grade, G7
-- legitimacy_tier, company size band, archetype. The pattern analyzer
-- groups on these dimensions to find statistically meaningful clusters
-- ("rejections at <50-person companies are 4x your overall rate").
--
-- We surface one row per rejected application, application_id exposed
-- so the dashboard can cite specific knowledge_ids (engineering
-- principle §11.4).
DROP VIEW IF EXISTS public.v_rejection_signals;
CREATE VIEW public.v_rejection_signals AS
SELECT
    a.user_id,
    a.id                AS application_id,
    a.company,
    a.role,
    a.applied_date,
    a.updated_at        AS rejected_at,
    CASE
        WHEN a.applied_date IS NOT NULL
        THEN GREATEST(0, EXTRACT(DAY FROM (a.updated_at - a.applied_date::timestamp))::int)
        ELSE NULL
    END                 AS days_to_rejection,
    rb.polisher_score,
    j.letter_grade,
    j.legitimacy_tier,
    j.archetype,
    COALESCE(c.headcount, 'unknown') AS company_size_band,
    ro.rejected_reason
FROM applications a
    LEFT JOIN jobs j ON j.id = a.job_id
    LEFT JOIN companies c ON c.id = j.company_id
        OR lower(c.name) = lower(a.company)
    LEFT JOIN resume_builds rb
        ON rb.application_id = a.id
       AND rb.status = 'converged'
    LEFT JOIN resume_outcomes ro ON ro.application_id = a.id
WHERE a.status = 'rejected';

COMMENT ON VIEW public.v_rejection_signals IS
    'One row per rejected application with cluster dimensions: '
    'days_to_rejection, polisher_score, letter_grade, legitimacy_tier, '
    'archetype, company size band. Audit Gap §4.3 rejection patterns.';

GRANT SELECT ON public.v_rejection_signals TO authenticated, service_role;


-- ── 6. v_resume_build_efficiency ───────────────────────────────────────────
-- Per-company resume-build performance: average polisher score, average
-- cost, average latency, success (converged) rate. Highlights which
-- companies are expensive to build resumes for and where the resume
-- pipeline is converging vs exhausting.
--
-- Filter pattern mirrors 025 (no closed/phantom rows) so the table
-- isn't polluted by jobs that have since been closed.
DROP VIEW IF EXISTS public.v_resume_build_efficiency;
CREATE VIEW public.v_resume_build_efficiency AS
SELECT
    rb.user_id,
    rb.company_name,
    count(*)::int AS total_builds,
    count(*) FILTER (WHERE rb.status = 'converged')::int AS converged_builds,
    count(*) FILTER (WHERE rb.status = 'exhausted')::int AS exhausted_builds,
    count(*) FILTER (WHERE rb.status = 'failed')::int AS failed_builds,
    ROUND(
        count(*) FILTER (WHERE rb.status = 'converged')::numeric
        / NULLIF(count(*), 0),
        3
    ) AS converge_rate,
    ROUND(avg(rb.polisher_score) FILTER (WHERE rb.status = 'converged'), 1) AS avg_polisher_score,
    ROUND(avg(rb.cost_usd_total) FILTER (WHERE rb.status = 'converged'), 4) AS avg_cost_usd,
    ROUND(avg(rb.latency_ms_total)::numeric FILTER (WHERE rb.status = 'converged'), 0)::int AS avg_latency_ms
FROM resume_builds rb
    LEFT JOIN jobs j ON j.id = rb.job_id
    LEFT JOIN companies c ON lower(c.name) = lower(rb.company_name)
WHERE (j.id IS NULL OR j.posting_closed_at IS NULL)
  AND (j.id IS NULL OR j.validation_failed IS NULL)
  AND (c.id IS NULL OR c.is_phantom IS NOT TRUE)
GROUP BY rb.user_id, rb.company_name;

COMMENT ON VIEW public.v_resume_build_efficiency IS
    'Per-company resume-build telemetry: avg polisher score, cost, '
    'latency, converge rate. Closed jobs + phantom companies excluded '
    '(mirrors migration 025 filters). Audit Gap §4.3.';

GRANT SELECT ON public.v_resume_build_efficiency TO authenticated, service_role;


-- ── 7. v_cost_per_outcome ──────────────────────────────────────────────────
-- Per-company total LLM spend (sum agent_call_log.cost_usd across all
-- calls tagged with the matching job_id/application_id/resume_build_id)
-- divided by the count of "real" outcomes (interviews + offers).
--
-- Companies with zero interviews/offers are still emitted with
-- outcomes=0 and cost_per_outcome=NULL so the dashboard can show "spent
-- $1.20, no signal yet" rows. The API filters those out at request
-- time when min_sample is set.
DROP VIEW IF EXISTS public.v_cost_per_outcome;
CREATE VIEW public.v_cost_per_outcome AS
WITH per_company_cost AS (
    SELECT
        acl.user_id,
        COALESCE(j.company, rb.company_name) AS company_name,
        sum(acl.cost_usd) AS total_cost_usd,
        count(*) AS total_calls
    FROM agent_call_log acl
        LEFT JOIN jobs j ON j.id = acl.job_id
        LEFT JOIN resume_builds rb ON rb.id = acl.resume_build_id
    WHERE COALESCE(j.company, rb.company_name) IS NOT NULL
    GROUP BY acl.user_id, COALESCE(j.company, rb.company_name)
),
per_company_outcomes AS (
    SELECT
        a.user_id,
        a.company AS company_name,
        count(*) FILTER (
            WHERE a.status IN ('interviewing','offered')
               OR ro.interview_received = TRUE
        )::int AS interviews,
        count(*) FILTER (
            WHERE a.status = 'offered' OR ro.offer_received = TRUE
        )::int AS offers
    FROM applications a
        LEFT JOIN resume_outcomes ro ON ro.application_id = a.id
    GROUP BY a.user_id, a.company
)
SELECT
    COALESCE(c.user_id, o.user_id) AS user_id,
    COALESCE(c.company_name, o.company_name) AS company_name,
    COALESCE(c.total_cost_usd, 0)::numeric(10,4) AS total_cost_usd,
    COALESCE(c.total_calls, 0)::int AS total_calls,
    COALESCE(o.interviews, 0) AS interviews,
    COALESCE(o.offers, 0) AS offers,
    CASE
        WHEN COALESCE(o.interviews, 0) + COALESCE(o.offers, 0) > 0
        THEN ROUND(
            COALESCE(c.total_cost_usd, 0)
            / (COALESCE(o.interviews, 0) + COALESCE(o.offers, 0)),
            4
        )
        ELSE NULL
    END AS cost_per_outcome
FROM per_company_cost c
FULL OUTER JOIN per_company_outcomes o
    ON c.user_id = o.user_id
   AND lower(c.company_name) = lower(o.company_name);

COMMENT ON VIEW public.v_cost_per_outcome IS
    'Per-company total LLM spend ÷ outcomes (interviews + offers). '
    'Surfaces ROI by company. NULL cost_per_outcome when 0 outcomes. '
    'Audit Gap §4.3.';

GRANT SELECT ON public.v_cost_per_outcome TO authenticated, service_role;


-- ── PostgREST schema reload ────────────────────────────────────────────────
NOTIFY pgrst, 'reload schema';

COMMIT;
