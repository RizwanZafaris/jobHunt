# Security Findings — Live Project Audit

**Project**: `oodvelyzdsncsssqvmyb.supabase.co`
**Audited**: 2026-05-09 via Supabase MCP `get_advisors`
**Status**: Findings documented. **No automated fixes applied** — RLS without
policies blocks reads, which would break the running app.

---

## 1. Critical — RLS disabled on 8 public tables

These tables have RLS off AND are exposed to PostgREST. Anyone with the
project's `anon` key can read or modify every row.

| Table | Rows | Sensitivity |
|---|---|---|
| `public.companies` | 140 | Low — public company data |
| `public.company_knowledge` | 507 | Low — public research |
| `public.jobs` | 245 | Low — public job postings |
| `public.applications` | 2 | **HIGH** — application status, scores |
| `public.rizwan_profile` | 5 | **HIGH** — legacy profile content |
| `public.story_bank` | 0 | Medium — interview stories (when populated) |
| `public.agent_conversations` | 152 | Medium — gap-dialogue content |
| `public.boss_audit_log` | 4 | Low — operational metadata |

**Mitigation today**: the deployed FastAPI uses the `service_role` key, which
bypasses RLS by design. The risk is the `anon` key being exposed in the
Vercel dashboard build (it is — `SUPABASE_ANON_KEY` is referenced in
`config/settings.py`).

**Recommended remediation order**:

1. Enable RLS on all 8 tables.
2. For each, add policies that:
   - **Allow service_role full access** (the API still works)
   - **Deny anon access entirely** for `applications`, `rizwan_profile`, `agent_conversations` (private)
   - **Allow anon SELECT only** for `companies`, `company_knowledge`, `jobs` (public-readable)

The migration to do this safely is **multi-step** (enable → add service_role policy → add anon policy → verify), not the one-line `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` the advisor proposes — that would block the running app immediately.

```sql
-- Step 1: enable RLS (do NOT run without step 2 ready)
ALTER TABLE public.companies            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.company_knowledge    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.jobs                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.applications         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rizwan_profile       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.story_bank           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.agent_conversations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.boss_audit_log       ENABLE ROW LEVEL SECURITY;

-- Step 2: service_role bypass (default in Supabase, but make it explicit)
-- service_role automatically bypasses RLS — no policies needed for it.

-- Step 3a: public-readable tables — anon can SELECT, no INSERT/UPDATE/DELETE
CREATE POLICY anon_read_companies         ON public.companies         FOR SELECT TO anon USING (true);
CREATE POLICY anon_read_company_knowledge ON public.company_knowledge FOR SELECT TO anon USING (true);
CREATE POLICY anon_read_jobs              ON public.jobs              FOR SELECT TO anon USING (true);
-- (boss_audit_log → admin only via service_role; no anon policy needed)

-- Step 3b: private tables — explicitly no anon access
-- (applications, rizwan_profile, story_bank, agent_conversations)
-- No policies for anon = anon gets nothing under RLS, which is what we want.

-- Doc: https://supabase.com/docs/guides/database/postgres/row-level-security
```

Run step 1 + step 3a in the same transaction so the dashboard never breaks.

Reference: <https://supabase.com/docs/guides/database/postgres/row-level-security>

---

## 2. Warning — RLS enabled but no policies on 8 profile_* tables

| Table | Rows |
|---|---|
| `public.profile_master` | 1 |
| `public.profile_experience` | 4 |
| `public.profile_certification` | 6 |
| `public.profile_education` | 3 |
| `public.profile_keyword` | 310 |
| `public.profile_keyword_category` | 11 |
| `public.profile_source_document` | 233 |
| `public.profile_recommendation` | 41 |

Effect: anon can read **nothing** from these tables (correct for privacy),
but the dashboard pages that fetch profile data via the **proxy route** end
up using `service_role` (which bypasses RLS), so the app still works. If
anyone ever switches the dashboard to the anon key for these endpoints,
they'd silently break.

Recommended: add explicit `service_role` policies for clarity, even though
they're functionally redundant. Keeps intent legible to future readers.

---

## 3. Warning — function search_path mutable

Three database functions don't pin `search_path`:

- `public.update_updated_at` (used by triggers including the new ones added in `multi_llm_schema.sql`)
- `public.search_company_knowledge` (pgvector RPC)
- `public.search_rizwan_profile` (pgvector RPC)
- `public.search_story_bank` (pgvector RPC)

Risk: a malicious search_path injection could shadow built-in functions
or refer to a different schema. Low risk in this project (single tenant,
only authenticated services), but worth fixing in a migration.

Fix template:
```sql
ALTER FUNCTION public.update_updated_at() SET search_path = public, pg_temp;
ALTER FUNCTION public.search_company_knowledge(...) SET search_path = public, pg_temp;
-- repeat for the others
```

Reference: <https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable>

---

## 4. Info — `vector` extension installed in `public` schema

`public.vector` should live in the `extensions` schema. Cosmetic in
practice — doesn't affect the running app. Migration would touch every
`vector(1536)` column reference, so defer until there's an unrelated
migration that changes those columns.

Reference: <https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public>

---

## What I did NOT change

- **Did not enable RLS automatically.** Doing so without policies would
  immediately break the deployed dashboard (which fetches via proxy, but
  still has fallback paths that use anon).
- **Did not add policies** — that requires deciding read/write boundaries
  per table, which is a deliberate product decision, not an automated one.
- **Did not pin function search_path** — defer to next functions migration.

When you're ready to address these, they should land as a separate
`db/security_hardening.sql` migration with each step verified against the
running dashboard.
