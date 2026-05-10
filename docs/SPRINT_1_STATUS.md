# Sprint 1 — P0 Implementation Status

**Date:** 2026-05-10
**Audit reference:** `docs/AUDIT_360_SYNTHESIS.md`
**First user (you):** Rizwan Zafar (`rizwanzaffar.pk@gmail.com`, user_id `00000000-0000-0000-0000-000000000001`, plan: lifetime, is_admin: true)

This document is the canonical handoff for what landed in Sprint 1 from the 6 parallel agent dispatch. **Nothing has been applied to your live Supabase yet.** Migration files were authored, validated, and staged. You decide when to apply.

---

## 1. What shipped (code, not yet wired)

### 1.1 Database migrations + seed (4 files, 644 lines)
| File | Purpose | Risk to apply |
|------|---------|---------------|
| `db/migrations/2026_05_10_001_multi_tenancy.sql` | Adds `users` + `orgs` tables, `user_id`/`org_id` columns to `jobs`/`applications`/`personas`/`company_knowledge`/`target_companies`/`costs`/`outreach`, RLS policies, indexes. Backfill default = your user_id. | **Medium** — alters existing tables. Idempotent + transactional. Skips tables that don't yet exist (target_companies, costs, outreach). |
| `db/migrations/2026_05_10_002_status_enum.sql` | Normalizes `applications.status` (Spanish + English variants) to a Postgres enum. Surfaces unknown values via NOTICE. | **Low** — staged column approach (`status_new` → swap), full audit trail. |
| `db/migrations/2026_05_10_003_jobs_runs.sql` | Creates `jobs_runs` table for the durable queue. Pure additive. | **Low** — additive only. |
| `db/seeds/user_001.sql` | Seeds you as user #1 with the canonical UUID, lifetime plan, admin flag. | **Low** — additive, ON CONFLICT DO NOTHING. |

**Apply order:** 001 → 002 → 003 → seed. The included `db/migrations/APPLY.sh` enforces order via a `schema_migrations` log table and prompts before touching production-shaped URLs.

**Spanish→English status mapping decisions** (worth a glance before applying 002):
- `Evaluada` → `researched`
- `Aplicada` → `applied`
- `qualified` / `evaluated` → `researched`
- `interview` → `interviewing`
- `accepted` → `offered` (collapsed; revisit if hire-vs-offer split is needed)
- `ghosted` → `rejected` (best-effort; flagged via NOTICE)

### 1.2 Auth + multi-tenancy (4 files, 685 lines + AUTH.md)
| File | What it does |
|------|-------------|
| `api/auth.py` | Verifies Supabase JWTs (HS256, audience=`authenticated`). LRU-caches verified tokens (gated by env flag). |
| `api/users.py` | `User` Pydantic model + DB helpers (`get_user_by_id`, `create_user` with race-safe upsert, `update_user_plan`). |
| `api/context.py` | FastAPI deps: `get_current_user`, `get_current_user_id`, `require_admin`. Short-circuits to you when `RIZWAN_SINGLE_USER_MODE=1` (default). |
| `api/AUTH.md` | Full migration cookbook with sed-style search/replace for the 71 endpoints in server.py. |

**Critical:** `RIZWAN_SINGLE_USER_MODE=1` is the default. Self-use keeps working through Sprint 1 without needing real auth. Flip to `0` only after every endpoint is wired and the `users` table exists.

**71 endpoints inventoried** in `api/AUTH.md` and classified:
- 2 public (keep as-is): `/`, `/health`
- 4 admin: `/debug/*`, `/personas/synthesize`, `/personas/backfill-embeddings`, etc. → `Depends(require_admin)`
- 65 per-tenant: need `Depends(get_current_user)` + `.eq("user_id", str(user.id))` filter on queries

### 1.3 Durable queue (5 files, 1,290 lines + QUEUE.md)
| File | What it does |
|------|-------------|
| `api/queue.py` | RQ wrapper. `enqueue_g2_build`, `enqueue_g1_discovery`, `enqueue_g3_interview_prep`. SHA256 idempotency dedup against active rows. |
| `api/worker.py` | Worker entrypoint (`python -m api.worker`). Calls into existing G2/G3/G1 graphs. Retry policy: 60s → 240s → 960s, max 3 attempts. |
| `api/jobs_runs.py` | Pydantic model + DB helpers for the `jobs_runs` table. |
| `api/orphan_reaper.py` | Sweeps `running` rows >15 min stale, requeues if attempts<3. APScheduler embedded mode + standalone CLI for cron. |
| `Dockerfile.worker` | Worker container (mirrors API base). |
| `railway.toml` | Adds `[[services]]` worker service. |
| `api/QUEUE.md` | Full migration cookbook including a unified diff for `/jobs/{job_id}/generate-resume`. |

**16 BackgroundTasks call sites in server.py inventoried** in QUEUE.md — 3 priority migrations (G2 resume, G3 interview, G1 deep-research), the rest classified KEEP/DEFER.

### 1.4 Eval harness (10 files, 1,588 lines + README.md)
| File | What it does |
|------|-------------|
| `evals/judge.py` | LLM-as-judge using existing `llm_router`. Scores resumes 0-10 on 5 axes (ATS coverage, evidence specificity, persona fit, hallucination check, length discipline). |
| `evals/run_golden.py` | CLI to run all golden cases or a specific one; writes JSON reports. |
| `evals/rag_eval.py` | RAG retrieval evaluator: recall@5/10, MRR vs labelled relevant docs. |
| `evals/regression_check.py` | CI gate — fails if mean drops >0.3 or any axis drops >0.5. |
| `evals/golden/case_00{1,2,3}_*.json` + `_golden_resume.md` | 3 placeholder golden cases (Marqeta, Visa, Stripe). Need hand-curated golden resumes before they're meaningful. |
| `evals/rag_queries.json` | 5 placeholder queries with empty `relevant_doc_ids` arrays — need labeling. |
| `.github/workflows/eval-regression.yml` | CI workflow (no-op until baseline lands). |

**Full Opus judge system prompt** in `evals/judge.py`.

### 1.5 Dashboard IA collapse + Today home (24 files, 1,889 lines)
| Change | Result |
|--------|--------|
| **Nav: 7 tabs → 5** | Today, Targets, Applications, Network, Insights. Profile moved to user-menu dropdown (avatar top-right). |
| **`/today` (new home)** | Ranked action cards: resume_ready, score_high_no_resume, score_below_threshold, stale_application, persona_stale, linkedin_post_due. State color bar on left edge (green/amber/blue/gray). Top 5 + "View all". |
| **`/network` placeholder** | "Coming Sprint 2" with email-capture form. |
| **`/insights`** | Tabbed shell consolidating Personas + Costs + System (Boss). `?tab=` query param for deep links. |
| **`/admin`** | Allowlist-gated (currently in-file: `['rizwanzaffar.pk@gmail.com']`). |
| **Legacy redirects** | `/personas`, `/costs`, `/boss`, `/profile`, `/(legacy)/pipeline` redirect to their new homes. |
| **`_pending_endpoints.md`** | Lists 9 backend endpoints the dashboard needs (Today data, sign out, etc.). |

`tsc --noEmit` passes clean.

**Today home is currently rendering MOCK DATA.** To make it real, a `/actions/today` endpoint is needed in `api/server.py` — see "Manual next steps" below.

### 1.6 Secrets hygiene + config check (4 files, ~250 lines)
| File | Purpose |
|------|---------|
| `scripts/check-prod-config.py` | Stdlib-only. Reads `.env.example` → validates current env. Never echoes values. Exit non-zero on any failure. |
| `.env.example` | Rewritten with section headers + `# tier: required/recommended/optional` markers. New vars added: `REDIS_URL`, `EVAL_GOLDEN_DIR`, `ANTHROPIC_PROMPT_CACHE_ENABLED`, `SUPABASE_JWT_SECRET`, `RIZWAN_SINGLE_USER_MODE`, `WORKER_CONCURRENCY`, `ORPHAN_REAPER_INTERVAL_MIN`. |
| `.github/workflows/config-check.yml` | CI gate: asserts script catches placeholders + passes on a synthetic-valid env. |
| `scripts/README.md` | Pattern for adding scripts. |

### 1.7 Dependencies consolidated
`requirements.txt` now includes (added by me, deduped from the 6 agents' pending files):
```
rq>=2.0
redis>=5.0
python-jose[cryptography]>=3.3
```
The four `_pending_deps_*.txt` files have been merged and removed.

---

## 2. Smoke checks (already run)

- ✅ All 13 new Python modules AST-parse cleanly
- ✅ All 4 eval JSON files validate
- ✅ Dashboard `tsc --noEmit` exits 0 (per agent report)
- ✅ Migration files: BEGIN/COMMIT pairs balanced (DO blocks count as PL/pgSQL nested begins, expected)
- ✅ Migration 001 has IF NOT EXISTS guards + skips missing tables via information_schema lookup
- ✅ Migration 002 uses staged-column approach (status_new → swap) for safe transition
- ✅ Migration 003 is purely additive (new table)

---

## 3. What you need to do — operator checklist

### Step A — Local pip install (1 minute)
```bash
cd /Users/rizwanzafar/Desktop/jobHunt
pip install -r requirements.txt
```
Adds `rq`, `redis`, `python-jose[cryptography]`.

### Step B — Stand up Redis (1 minute, local self-use)
For self-use during Sprint 1 you can run Redis locally:
```bash
brew install redis    # if not installed
brew services start redis    # or: redis-server &
```
For Railway production: add the **Redis** plugin to your project; it injects `REDIS_URL` automatically. The new `[[services]]` worker block in `railway.toml` will pick it up on next deploy.

### Step C — Apply migrations (the destructive bit — your call)

**On a Supabase branch first** (recommended):
```bash
# 1. Create a Supabase branch via dashboard or CLI
# 2. Point DATABASE_URL at the branch:
export DATABASE_URL="postgres://...branch...supabase.co:5432/postgres"

# 3. Run the apply script (it asks for confirmation on production-shaped URLs):
cd /Users/rizwanzafar/Desktop/jobHunt
chmod +x db/migrations/APPLY.sh
./db/migrations/APPLY.sh

# 4. Apply the seed:
psql "$DATABASE_URL" -f db/seeds/user_001.sql

# 5. Verify:
psql "$DATABASE_URL" -c "SELECT id, email, plan, is_admin FROM users WHERE id = '00000000-0000-0000-0000-000000000001';"
psql "$DATABASE_URL" -c "SELECT status, count(*) FROM applications GROUP BY status ORDER BY 2 DESC;"
psql "$DATABASE_URL" -c "\dt jobs_runs"
```

If the branch results look correct, merge the branch back to main (Supabase dashboard).

### Step D — Set env flags (≤1 minute)
Locally:
```bash
echo 'REDIS_URL=redis://localhost:6379/0' >> .env
echo 'RIZWAN_SINGLE_USER_MODE=1' >> .env
echo 'WORKER_CONCURRENCY=1' >> .env
echo 'SUPABASE_JWT_SECRET=<paste from Supabase Settings → API → JWT Secret>' >> .env
```
Railway: add the same vars in the project settings.

### Step E — Boot the worker (separate process, locally)
```bash
cd /Users/rizwanzafar/Desktop/jobHunt
python -m api.worker
# Watch for: "Listening on jobhunt..."
```
On Railway it auto-starts via the new worker service.

### Step F — Run config check
```bash
python scripts/check-prod-config.py
# Should exit 0 with all required vars green
```

### Step G — Verify the eval harness runs (no LLM cost yet)
```bash
python -m evals.regression_check
# No baseline yet → exit 0 with "no prior reports" message
```

---

## 4. Follow-up PRs (NOT done in Sprint 1 — explicitly deferred)

These are the wiring changes that depend on migrations being applied. Ship as separate PRs once Step C above is green.

### PR-1: Wire `get_current_user` to the 65 per-tenant endpoints in `api/server.py`
- Recipe: `api/AUTH.md` §"Migration recipe"
- Mechanical sed-friendly pattern. Estimated: 4-6 hours.
- Test plan: with `RIZWAN_SINGLE_USER_MODE=1`, every endpoint should keep returning the same data as before the change (because user_id auto-resolves to you).

### PR-2: Migrate the 3 critical BackgroundTasks → enqueue
- Recipe: `api/QUEUE.md` §"Migration cookbook"
- Endpoints: `/jobs/{id}/generate-resume`, `/personas/deep-research`, `/jobs/{id}/prep-interview`
- Estimated: 2 hours.
- Test plan: kick off a build, redeploy mid-flight, confirm the run completes via the worker.

### PR-3: Add `/actions/today` endpoint to power the new home
- The dashboard's `/today` page is currently rendering mock data from `dashboard/src/lib/mock/today.ts`.
- Endpoint should return `TodayAction[]` matching the type at `dashboard/src/lib/types/today.ts`.
- Logic per kind:
  - `resume_ready` — jobs with `resume_generated_at IS NOT NULL` AND no application yet
  - `score_high_no_resume` — jobs with score ≥ 85 AND `resume_generated_at IS NULL`
  - `score_below_threshold` — jobs with score 70-84 (rare; mostly amber for awareness)
  - `stale_application` — applications with status='applied' AND applied_at < now()-7 days AND no outcome
  - `persona_stale` — personas with `last_news_refresh_at < now()-14 days`
  - `linkedin_post_due` — placeholder until P1.2 ships
- Estimated: 3 hours.

### PR-4: Migrate cookbook for the remaining ~12 BackgroundTasks call sites
- Per `api/QUEUE.md` §"BackgroundTasks call site checklist"
- Most are KEEP (short-running). Worth a one-pass review.

---

## 5. Out-of-scope reminders (Sprint 2 / Sprint 3)

These were intentionally NOT touched in Sprint 1. Reference: `docs/AUDIT_360_SYNTHESIS.md` §4 (P1 differentiators).

| Item | Audit ref | Estimated effort |
|------|-----------|------------------|
| **Referral graph** (people, edges, employments tables + path-finder) | P1.1 | 10 days |
| **LinkedIn presence engine** (G4 graph: news → draft → user-approve → schedule) | P1.2 | 10 days |
| **Outcome-conditioned RAG** (outcome_score on knowledge rows; Bayesian credit assignment) | P1.3 | 5 days |
| **Hybrid retrieval** (BM25 + RRF + Cohere rerank) | P1.4 | 4 days |
| **Persona evolution UI** (timeline + diff vs v1/v2) | P1.5 | 2 days |
| **Anthropic prompt caching** (40% COGS cut) | P2.1 | 0.5 days |
| **Sonnet swaps** (critic + gate nodes; -50% cost) | P2.2 | 0.5 days |
| **HNSW migration** (over ivfflat; 2-5× recall) | P2.3 | 1 day |

---

## 6. Strengths to defend (do NOT regress these)

- 5-LLM router with cost+latency telemetry (`agents/llm_router.py`)
- 12-node G2 graph + critic + persona gate
- pgvector + ivfflat + `search_company_knowledge` RPC
- Apify rag-web-browser deep research (returns 201 — already handled)
- Phase 2.0 design tokens + dark/light + Storybook
- 68 targets × 8 success / 8 failure patterns × ~20 ATS keywords each
- Persona deep-research with success_patterns + failure_patterns persisted to JSONB

---

## 7. Update 2026-05-10 — User decision recorded

**Decision: "Path A and C in parallel — A deferred, C now."**

- **Path C (P1 differentiators)** kicked off via 2 background agents (referral graph + LinkedIn engine). Self-contained schema + code; no DB application.
- **Path A (migrations)** authorized as **Option A** (expanded scope: 22 user-owned tables, not 4) but DEFERRED for later application. When ready: edit migration 001 to extend `target_tables` array, then apply via Supabase MCP.

### Supabase MCP inventory finding (2026-05-10)
Production has **23 tables**, not 7. The original migration 001 covered only 4 of 22 user-owned tables. Live security advisory: **15 tables have RLS DISABLED and are exposed via the anon key.** Full inventory:

| Should be tenant-ized in 001 (extended) | Status |
|---|---|
| jobs, applications, company_personas, company_knowledge | Already in original 001 |
| companies, target_companies, costs, agent_call_log, agent_conversations | **Add to 001** |
| rizwan_profile, story_bank, resume_builds, resume_outcomes, ats_test_results, interview_outcomes, interview_prep | **Add to 001** |
| profile_master, profile_experience, profile_certification, profile_education, profile_keyword, profile_keyword_category, profile_source_document, profile_recommendation | **Add to 001** (RLS already on, policies need rebuild) |
| boss_audit_log | Leave global/admin |
| outreach (future) | Defensive include |

When we resume Path A: edit `db/migrations/2026_05_10_001_multi_tenancy.sql` `target_tables` array to the 22-table list, then apply 001 → 002 → 003 → seed in sequence. The migration's existing `information_schema` guard handles missing tables gracefully.

### Branching not available
Supabase MCP `list_branches`/`create_branch` returned permission errors — branching either disabled on plan or requires paid setup. When Path A resumes, apply must go directly to main (still safe: transactional + idempotent + service-role bypass).

---

## 8. Path C — what's running in background

### Agent C1 — Referral graph (P1.1)
- Schema: `db/migrations/2026_05_10_004_referral_graph.sql` (people, employments, edges, target_company_employees) — multi-tenant from day 1
- Code: `agents/referral_graph.py` (NetworkX path-finder, LinkedIn CSV import), `agents/intro_email_agent.py` (warm-intro email drafts)
- API: `api/network.py` (FastAPI router for /network/paths, /network/people, /network/import/linkedin-csv, /network/edges, /network/target-coverage)
- UI: replaces `/network` placeholder with real path-finder + LinkedIn CSV import + warm-intro draft modal

### Agent C2 — LinkedIn engine (P1.2)
- Schema: `db/migrations/2026_05_10_005_linkedin_drafts.sql` (linkedin_drafts, linkedin_posting_schedule, linkedin_voice_profile)
- Code: `agents/g4_linkedin_graph.py` (LangGraph: pick_angle → draft_v1 → critique → polish), `agents/linkedin_voice_extractor.py`, `agents/linkedin_scheduler.py`
- API: `api/linkedin.py` (FastAPI router for /linkedin/drafts, voice-profile, posting-schedule)
- UI: `/linkedin` content calendar with Drafts/Scheduled/Posted tabs, draft cards with edit/approve/reject

Both agents authoring code only. **No SQL application, no LinkedIn posts.** Manual-paste model in V1 per audit risk #2.

---

## 9. Productive paths still available

You have three productive paths from here. Pick one and tell me which:

**Path A — Apply migrations, then wire (4-6 hours)**
1. Step C above (apply 001/002/003/seed on a Supabase branch, verify, merge)
2. PR-1 (auth wiring) + PR-2 (queue swap) + PR-3 (Today endpoint) executed in parallel
3. End state: real multi-tenant SaaS-ready system, you keep using it as user #1

**Path B — Defer migrations, ship P2.1 + P2.2 (1 day)**
1. Implement Anthropic prompt caching in `agents/llm_router.py`
2. Swap critic + gate nodes to Sonnet
3. Result: 50%+ COGS reduction, no DB risk
4. Migrations + wiring happen later

**Path C — Skip ahead to P1 (~3 weeks)**
1. Start the referral graph schema + path-finder (P1.1)
2. Start the LinkedIn engine MVP (P1.2)
3. Migrations + wiring happen alongside
4. Higher risk, but bigger differentiator delivery

My recommendation: **Path A**. The audit's #1 risk was no eval harness AND no multi-tenancy AND BackgroundTasks fragility. Sprint 1 staged the fixes. Applying + wiring closes the loop. P1 work is much more valuable when shipped on a stable foundation.

If you say "Path A go", I will execute Step C, then dispatch parallel agents for PR-1, PR-2, and PR-3, then run smoke tests against the wired system.
