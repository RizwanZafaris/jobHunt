# jobHunt — AI Job Landing System

> **Outcome-conditioned, peer-network-aware, persona-evolved job hunt.**
> Every resume, email, and LinkedIn post improves based on real interview
> outcomes from your peer graph — and a three-layer RAG pipeline that
> nobody in the competitive set runs.

Built for **Rizwan Zafar** (user #1, lifetime plan) today; multi-tenant
SaaS-ready tomorrow.

**Status: 2026-05-14** — production stack is live on Railway + Supabase
+ Vercel. 68 target companies, ~3,500 rows of company knowledge, 12 G2
nodes shipping resumes at ~$1 / 5 min, G3 interview studio + tutor
ready, G4 LinkedIn engine with image briefs, G5 letter-grade A-F + G6
follow-up cadence + G7 application-form assist + G8 offer-evaluation
(all live), `/insights?tab=analytics` for pattern analytics,
`/insights?tab=traces` for LangGraph debugging, embedded APScheduler in
the API process (no separate worker on Railway needed), 32 migrations
applied (most recent: phantom cleanup + v_graph_runs view).

> See [`BUILD_RATIONALE.md`](BUILD_RATIONALE.md) for the architecture
> decisions (why LangGraph, why per-company personas, why Anthropic
> for the writer, why three-layer RAG, etc.) and how each component
> fits together.

---

## What makes this different

Three product wedges that no competitor combines:

1. **Outcome-conditioned RAG** — every G2 build emits `cite:knowledge_id=<uuid>` markers; when you log an interview win/loss the `outcome_to_persona` worker propagates Bayesian credit back to the specific knowledge rows that drove the callback. Personas evolve from real outcomes, not vibes. (Migration 008/009, `agents/outcome_to_persona.py`.)
2. **Peer-network referral graph** — your LinkedIn CSV import seeds a `people / employments / edges / target_company_employees` schema with a NetworkX Dijkstra path-finder that returns warm-intro paths (1–2 hops, geometric-mean strength scoring). `/network` UI surfaces top intros per target. (Migration 004, `agents/referral_graph.py`.)
3. **Three-layer enrichment pipeline** — the persona for each target company is fed by **three independent sources**:
   - **Apify** (depth scrape) — long-form pages, blog posts, Glassdoor reviews
   - **Perplexity** (recency + strategy) — Sonar for last-30-day news, Sonar-pro for monthly strategic posture
   - **Apollo** (firmographic + hiring intel) — enrichment + open-jobs index + people search

Each layer writes to `company_knowledge` with a distinct `metadata.source` so the credit assignment can attribute outcomes back to which signal source mattered.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            jobHunt System                                    │
│                                                                              │
│   /today  ──►  /applications/[id]/workspace ──► /interview-studio  ──►       │
│       │              │                                  │                    │
│       │              ├── Role overview                  ├── Prep material    │
│       │              ├── Resume (G2 graph)              ├── Concept ladder   │
│       │              │   • view / download              ├── Tutor chat       │
│       │              │   • edit (Quick / Rebuild section/ Full)              │
│       │              ├── Network (warm-intro paths)     └── Outcome logger   │
│       │              ├── Interview prep stub                  │              │
│       │              └── Apply checklist                      │              │
│       │                                                       ▼              │
│       └── LinkedIn post (G4) ── image_brief ── Copy & paste   outcome→       │
│                                                              persona evolve  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

Backend (FastAPI on Railway, port 8080)
  api/server.py            ◄── routers: network, linkedin, actions, workspace,
                                interview_studio, perplexity, apollo
  api/queue.py + worker.py ◄── Redis + RQ durable queue (replaces BackgroundTasks)
  agents/                  ◄── 18 graphs / workers (see Agents table)

Database (Supabase Postgres + pgvector)
  Multi-tenant from day 1: 32 user-owned tables, RLS enforced
  30+ migrations applied (see db/migrations/)

Dashboard (Next.js 15 App Router on Vercel)
  /today /targets /applications /network /insights /admin
  /applications/[id]/workspace + /interview-studio + /offer
  /linkedin + /jobs/[id]
  /insights?tab=analytics + /insights?tab=traces
```

Deep docs: [`docs/AUDIT_360_SYNTHESIS.md`](docs/AUDIT_360_SYNTHESIS.md) (6-expert audit + P0/P1/P2 roadmap) · [`docs/SPRINT_1_STATUS.md`](docs/SPRINT_1_STATUS.md) (decisions log) · [`docs/AUDIT_2026_05_10.md`](docs/AUDIT_2026_05_10.md) (cost + quality audit) · [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/G2_RESUME_BUILDER_GRAPH.md`](docs/G2_RESUME_BUILDER_GRAPH.md) · [`docs/G3_INTERVIEW_PREP_GRAPH.md`](docs/G3_INTERVIEW_PREP_GRAPH.md) · [`docs/SECURITY.md`](docs/SECURITY.md)

API surface docs: [`api/AUTH.md`](api/AUTH.md) · [`api/QUEUE.md`](api/QUEUE.md) · [`api/WORKSPACE.md`](api/WORKSPACE.md) · [`api/INTERVIEW_STUDIO.md`](api/INTERVIEW_STUDIO.md) · [`api/LINKEDIN.md`](api/LINKEDIN.md) · [`api/NETWORK.md`](api/NETWORK.md)

---

## What's in this codebase

### Sprint phases (2026-05-08 → 2026-05-14)

| Phase | What | Status |
|---|---|---|
| 0     | Multi-LLM router (5 providers) · agent_call_log · company_personas · resume_builds · outcomes schema | merged |
| 1.0–1.16 | G2 Resume Builder graph (12 nodes) · PersonaSynthesizer · cost cap · persona-quality gate · cost-budget alerts · `/personas` `/costs` dashboards | merged |
| 2     | G3 Interview Prep graph (7 nodes) behind `USE_G3_GRAPH` flag | merged |
| **G1.5 + Path A** | Deep-research persona builder (Apify + Gemini long-context, success/failure patterns) | merged |
| **Sprint 1 P0** | Multi-tenancy (22 tables, 88 RLS policies) · durable Redis+RQ queue · eval harness · auth scaffold (`RIZWAN_SINGLE_USER_MODE`) · config-drift detector · IA collapse 7→5 tabs + `/today` home | merged |
| **P1.1 Referral graph** | `people / employments / edges / target_company_employees` schema · NetworkX Dijkstra path-finder · LinkedIn CSV import · warm-intro email drafter · `/network` UI | merged |
| **P1.2 LinkedIn engine** | 5-node G4 LangGraph (pick_angle → draft → critique → polish → image_brief → persist) · voice extractor · scheduler · content calendar `/linkedin` | merged |
| **P1.3 Outcome credits + persona versions** | `knowledge_outcome_credits` ledger · `persona_versions` history · `outcome_to_persona.credit_outcome` worker · `cite:knowledge_id` markers in G2 | merged |
| **Phase 1 — `/today`** | Ranked action queue: 6 card kinds (resume_ready, score_high_no_resume, score_below_threshold, stale_application, persona_stale, linkedin_post_due) · job revalidator | merged |
| **Phase 2 — Workspace** | `/applications/[id]/workspace` 5-tab page · Quick tweak / Rebuild section / Full rebuild chat editor · auto-replace on rebuild | merged |
| **Phase 3 — Interview Studio** | `/applications/[id]/interview-studio` 3-pane studio · concept ladder (basics → intermediate → advanced) · tutor chat · outcome logger | merged |
| **Perplexity layer** | `recency_check` (Sonar) · `strategic_posture` (Sonar-pro) · `verify_claim` · persona news-tracking columns · disambiguation prompt for brand-vs-policy ambiguity | merged |
| **Apollo layer** | `enrich_organization` (firmographic) · `get_organization_job_postings` · `search_people` (paid plan) · `search_companies` (paid plan) · DB hook into `company_knowledge` | merged |
| **Tier 2 G5 / Legitimacy v1** | 6-dimension fit scoring with A-F `letter_grade` chips on every /today card · `legitimacy_tier` ghost-posting filter | merged |
| **Tier 2 G6 follow-up cadence** | Daily follow-up draft generator · `follow_up_cadence` table · cite-attribution loop back to persona | merged |
| **Tier 2 Story Bank + G9** | Per-user achievement library · `story_bank` table · G3 cites stories by id for outcome credit | merged |
| **Tier 3 G7 application graph** | Greenhouse form-scanner → classifier → retriever → critic → fill (HITL approve gate) · `application_answers` table · per-question cites | merged |
| **Tier 4 G11 voice calibration** | Per-user writing-sample injection into the USER message · `writing_samples` table | merged |
| **Tier 4 §6.4 Proof points** | Quantified achievement extractor · `proof_points` table · cited by G7 cover letters | merged |
| **Tier 4 G8 Offer Evaluation** | 5-node LangGraph (offer_parser → market_analyzer → negotiation_strategist → risk_detector → synthesizer) · persona-as-critic gate · `offer_evaluations` table (41 cols) · `/applications/[id]/offer` dashboard | merged |
| **Pattern Analytics** | 7 SQL views (funnel, by_grade, by_archetype, by_size, rejection_signals, build_efficiency, cost_per_outcome) · `agents/pattern_analyzer.py` · `/insights?tab=analytics` dashboard | merged |
| **LangGraph Traces** | `v_graph_runs` view aggregates `agent_call_log` into per-run summaries · `api/traces.py` (3 endpoints) · `/insights?tab=traces` debug dashboard | merged |
| **Embedded APScheduler** | Cron jobs (job_scout, persona_synthesis, boss_agent, follow_up_cadence, cost_alert, cost_digest) run inside the FastAPI process · `/admin/scheduler-status` health probe | merged |
| **Firecrawl pilot** | Source adapter for JS-rendered enterprise career pages (Visa/Mastercard/Stripe/Adyen/Marqeta) · `agents/sources/firecrawl_source.py` · $20/mo budget cap | merged |
| **Phantom cleanup + LinkedIn harden** | Migrations 028+030 hard-delete the BUG-013 phantom companies + flag 183 phantom-string jobs · LinkedIn title parser regex broadened · `/linkedin` Generate wired end-to-end with company dropdown + image brief always visible · all 4 DraftCard buttons (Edit/Copy/Approve/Reject) wired to real API with validation | merged |

### Database migrations applied to production Supabase

```
2026_05_10_001  multi_tenancy (32 tables × 4 RLS policies each)
2026_05_10_002  application_status enum normalisation
2026_05_10_003  jobs_runs (durable queue ledger)
2026_05_10_004  referral graph (people, employments, edges, target_company_employees)
2026_05_10_005  linkedin_drafts + posting_schedule + voice_profile
2026_05_10_006  linkedin_drafts.image_brief column
2026_05_10_007  jobs.posting_closed_at + last_validated_at + validation_status
2026_05_10_008  knowledge_outcome_credits + persona_versions + interview_tutor_messages
2026_05_10_009  search_company_knowledge v2 (returns id UUID for citation markers)
2026_05_10_010  company_personas news-tracking columns
2026_05_12_012  linkedin_drafts.source_company_name denormalisation
2026_05_12_013  comp_cache (salary band cache) + companies.is_phantom
2026_05_12_014  applications.applied_date check constraint
2026_05_12_015  story_bank (per-user achievement library)
2026_05_12_016  follow_up_cadence (G6 daily follow-up generator)
2026_05_12_017  story_bank not-null hardening
2026_05_12_018  search_story_bank v2 (semantic match)
2026_05_12_019  jobs.fit_score_breakdown JSONB (G5 6-dimension scoring)
2026_05_12_020  jobs.legitimacy_tier + legitimacy_score (ghost-posting filter)
2026_05_12_021  interview_prep G3 Tier-2 columns
2026_05_12_022  application_answers (G7 application-form assist)
2026_05_12_023  writing_samples (Tier 4 G11 voice calibration)
2026_05_12_024  proof_points (Tier 4 §6.4)
2026_05_12_025  v_company_conversion_funnel (closed + phantom filter fix)
2026_05_12_026  offer_evaluations (Tier 4 G8 — 41 columns)
2026_05_12_027  pattern analytics views (7 views: funnel by grade/archetype/size, rejection signals, …)
2026_05_13_028  phantom company hard-cleanup (deletes is_phantom=TRUE rows)
2026_05_13_029  v_graph_runs (LangGraph trace observability view)
2026_05_14_030  phantom job strings (flags 183 jobs with phantom company names)
```

All 32 production tables have RLS enabled (`boss_audit_log` intentionally excluded — admin/global).

---

## Agents

| Agent | Model | Role | Trigger |
|---|---|---|---|
| **JobScoutAgent** | GPT-4.1 | Scans portals, scores jobs against persona | Daily 09:00 |
| **CompanyAgent** | Claude Opus | Deep company expert; reviews resume gaps | Per company |
| **RizwanAgent** | Claude Opus | Represents the user; fills gaps via dialogue | Per application |
| **InterviewAgent** *(legacy)* | Claude Opus | STAR prep, likely Qs, salary negotiation | Per high-score job |
| **BossAgent** | Claude Opus | Orchestrator; freshness audit; daily digest | Daily 21:00 |
| **PersonaSynthesizer** | Gemini 2.5 Pro (fallback Claude) | Per-company persona refresh from outcomes + transcripts | Sun 03:00 weekly |
| **persona_deep_research** | Gemini 2.5 Pro + Apify | Deep persona build: 6-10 success_patterns + 6-10 failure_patterns + ~20 ATS keywords | One-shot per company; refresh on demand |
| **G2 graph (12 nodes)** | Multi-LLM ensemble | Resume builder; insider_expert emits `cite:knowledge_id=<uuid>` for outcome attribution | `/workspace/{id}/build-resume` ~$1, ~5 min |
| **G3 graph (7 nodes)** | Multi-LLM ensemble | Interview prep pack (likely Qs, STAR, hooks, red flags, salary) | `/workspace/{id}` → Interview Prep tab |
| **G4 LinkedIn graph (5 nodes)** | Opus writer + Sonnet critic + Opus polish | News-anchored post drafter; pick_angle → draft → critique → polish → image_brief → persist; never auto-posts | Manual or scheduled (Mon/Wed/Fri 09:00) |
| **G5 fit scoring** | Deterministic + LLM | 6-dimension scorecard (role, seniority, skills, location, comp, culture) → A-F letter grade + `legitimacy_tier` (ghost-posting filter) | Every job ingest |
| **G6 follow-up cadence** | Claude Opus | Daily follow-up draft generator per stale application; cite-attribution loop back to persona | Daily 18:00 (APScheduler) |
| **G7 application graph** | Multi-LLM | Greenhouse form-scanner → classifier → retriever → critic → fill (HITL approve gate); per-question `cite:knowledge_id` + `cite:story_id` | `/applications/[id]/apply` |
| **G8 offer evaluation (5 nodes)** | Multi-LLM | offer_parser → market_analyzer → negotiation_strategist → risk_detector → synthesizer; persona-as-critic gate | `/applications/[id]/offer` |
| **G9 story bank** | Claude Opus | Per-user achievement library; G3 cites stories by id for outcome credit | Onboarding + on-demand |
| **G11 voice calibration** | Claude Opus | Per-user writing-sample injection into USER message; preserves voice in G4/G7 outputs | Onboarding |
| **pattern_analyzer** | (SQL views) | 7 views over `agent_call_log` + `outcomes` (funnel by grade/archetype/size, rejection signals, build_efficiency, cost_per_outcome) | `/insights?tab=analytics` |
| **interview_tutor** | Claude Opus | 3-level concept ladder tutor; cites prep-pack sections | `/interview-studio/{id}/tutor-chat` |
| **outcome_to_persona** | Claude Opus | Bayesian credit assignment: outcome event → cited knowledge rows → `outcome_score` update → persona evolution | After every outcome log + Sunday cron |
| **perplexity_search** | Sonar / Sonar-pro | Recency check + strategic posture + claim verification; with brand-vs-policy disambiguation | Weekly recency + monthly strategic per persona |
| **persona_news_check** | (orchestrator) | Wraps Perplexity, filters fresh anchors, writes `company_knowledge` rows | `/perplexity/check-news/{company}` or batch |
| **apollo_enrich** | Apollo REST | Firmographic enrichment + open-jobs index + people seed for referral graph | `/apollo/enrich/{company}` |
| **referral_graph** | NetworkX | Dijkstra path-finder; geometric-mean strength scoring; LinkedIn CSV import | On CSV upload + on demand |
| **intro_email_agent** | Claude Opus | Warm-intro email drafts (to introducer, not target) | Per warm-intro path |
| **resume_edit_assistant** | Claude Opus | Quick tweak / Rebuild section / Full rebuild | `/workspace/{id}/edit-resume` |
| **job_validator** | (HTTP HEAD) | Revalidates jobs.url; marks `posting_closed_at`; 6-hour cache | Every 30 min (APScheduler) |
| **linkedin_voice_extractor** | Claude Opus | One-shot bootstrap of `linkedin_voice_profile` from cv.md | Onboarding |
| **linkedin_scheduler** | (sweeper) | Notifies user when scheduled draft is due (V1 manual paste; future Buffer) | Every 30 min |
| **orphan_reaper** | (sweeper) | Marks `jobs_runs` rows running >15 min as failed; retries with backoff | Every 5 min |
| **CostAlerter** | (rule-based) | Daily threshold + Sunday digest (Slack/SendGrid) | Daily 22:00 + Sun 09:00 |

---

## API endpoints (FastAPI, all auth-gated via `X-Secret-Key` header)

| Surface | Endpoints |
|---|---|
| **System** | `GET /` · `GET /health` · `GET /jobs-runs/{run_id}` (polling) |
| **Today** | `GET /actions/today?limit=N` |
| **Workspace (Phase 2)** | `GET /workspace/{job_id}` · `POST /workspace/{job_id}/build-resume` · `POST /workspace/{job_id}/edit-resume` · `POST /workspace/{job_id}/save-resume-edit` · `POST /workspace/{job_id}/rebuild-section` · `POST /workspace/{job_id}/full-rebuild` · `POST /workspace/{job_id}/mark-applied` |
| **Interview Studio (Phase 3)** | `GET /interview-studio/{application_id}` · `POST /interview-studio/{application_id}/tutor-chat` · `POST /interview-studio/{application_id}/log-outcome` · `POST /interview-studio/{application_id}/build-prep-pack` |
| **LinkedIn engine (P1.2)** | `POST /linkedin/drafts/generate` · `GET /linkedin/drafts` · `GET/PATCH /linkedin/drafts/{id}` · `POST /linkedin/drafts/{id}/{approve,copy,reject}` · `GET/PUT /linkedin/voice-profile` · `GET/PUT /linkedin/posting-schedule` |
| **Network / referrals (P1.1)** | `GET /network/paths?target_company_id=` · `GET /network/people` · `POST /network/people` · `POST /network/import/linkedin-csv` (multipart) · `POST /network/edges` · `GET /network/target-coverage` |
| **Perplexity** | `POST /perplexity/check-news/{company_name}` · `POST /perplexity/strategic-posture/{company_name}` · `POST /perplexity/verify-claim` |
| **Apollo** | `POST /apollo/enrich/{company_name}` · `POST /apollo/enrich-raw` · `POST /apollo/job-postings` · `POST /apollo/search-people` *(paid plan)* · `POST /apollo/search-companies` *(paid plan)* |
| **Story Bank (G9)** | `GET /stories` · `POST /stories` · `PATCH /stories/{id}` · `DELETE /stories/{id}` · `POST /stories/search` |
| **Follow-up cadence (G6)** | `GET /follow-ups` · `POST /follow-ups/generate/{application_id}` · `POST /follow-ups/{id}/send` · `POST /follow-ups/{id}/snooze` |
| **Application form assist (G7)** | `POST /g7/scan-form` · `POST /g7/generate-answers/{application_id}` · `POST /g7/approve-answer/{id}` · `GET /g7/answers/{application_id}` |
| **Offer evaluation (G8)** | `POST /offers/evaluate/{application_id}` · `GET /offers/{application_id}` · `POST /offers/{id}/decide` |
| **Proof points (Tier 4)** | `GET /proof-points` · `POST /proof-points/extract` · `PATCH /proof-points/{id}` |
| **Pattern analytics** | `GET /analytics/funnel` · `GET /analytics/by-grade` · `GET /analytics/by-archetype` · `GET /analytics/rejection-signals` · `GET /analytics/cost-per-outcome` |
| **LangGraph traces** | `GET /traces/runs?limit=N` · `GET /traces/runs/{run_id}` · `GET /traces/errors` |
| **Legacy / pipeline** | `POST /pipeline/run` · `POST /pipeline/evaluate` · `GET /jobs` · `GET /companies` · `POST /jobs/{id}/generate-resume` · `POST /personas/deep-research` · `POST /personas/refresh-news` · `POST /boss/audit` · `GET /digest/latest` · `GET /costs/by-resume-build` |

---

## Setup

### 1. Install dependencies
```bash
git clone https://github.com/RizwanZafaris/jobHunt
cd jobHunt
pip install -r requirements.txt
cd dashboard && npm install && cd -
```

### 2. Configure env
```bash
cp .env.example .env
# Fill at minimum:
#   ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY / DEEPSEEK_API_KEY / KIMI_API_KEY
#   SUPABASE_URL / SUPABASE_SERVICE_KEY / SUPABASE_DB_URL
#   APIFY_TOKEN (deep research) · SERPER_API_KEY (search)
#   PERPLEXITY_API_KEY (P1.3 recency layer)
#   APOLLO_API_KEY (firmographic + hiring layer)
#   REDIS_URL=redis://localhost:6379/0 (queue)
#   RIZWAN_SINGLE_USER_MODE=1 (short-circuits auth to user_001 for self-use)
#   API_SECRET_KEY=<choose-a-strong-secret>
```
Then run the config-drift check:
```bash
python scripts/check-prod-config.py
```

### 3. Apply migrations to Supabase
On a Supabase branch first (recommended):
```bash
chmod +x db/migrations/APPLY.sh
DATABASE_URL=postgres://...branch...supabase.co:5432/postgres ./db/migrations/APPLY.sh
psql "$DATABASE_URL" -f db/seeds/user_001.sql
```
All 30+ migrations are idempotent and transactional. Or apply individually via Supabase MCP `apply_migration`.

### 4. Boot services
```bash
# API service
python main.py --api
#   or in production: START_MODE=api railway up

# Queue worker (separate process)
python -m api.worker

# Scheduler (cron daemon — separate service)
python main.py --scheduler
#   or: START_MODE=scheduler railway up

# Dashboard (Next.js)
cd dashboard && npm run dev
```

### 5. First-time setup
```bash
python main.py --onboard                                        # CV → profile_master
python -m agents.linkedin_voice_extractor --user-id 00000000-0000-0000-0000-000000000001 --cv-path cv.md
# Optional: upload your LinkedIn connections CSV via the /network UI
# Optional: trigger persona refresh for all companies
#   POST /perplexity/check-news/all   ~$0.40 for 71 companies
```

---

## Environment variables

| Var | Purpose | Tier |
|---|---|---|
| `ANTHROPIC_API_KEY` · `OPENAI_API_KEY` · `GOOGLE_API_KEY` · `DEEPSEEK_API_KEY` · `KIMI_API_KEY` | 5-LLM router | required |
| `SUPABASE_URL` · `SUPABASE_SERVICE_KEY` · `SUPABASE_DB_URL` | DB + pgvector + langgraph checkpointer | required |
| `API_SECRET_KEY` | Backend secret used by `X-Secret-Key` header | required |
| `RIZWAN_SINGLE_USER_MODE` | `1` short-circuits all auth to user_001 (default during self-use) | required |
| `REDIS_URL` | Queue connection (`redis://localhost:6379/0` for local) | required for queue |
| `APIFY_TOKEN` | Deep-research scraper | recommended |
| `PERPLEXITY_API_KEY` | Recency + strategic posture layer | recommended |
| `APOLLO_API_KEY` | Firmographic enrichment layer | optional |
| `SERPER_API_KEY` | Search fallback | recommended |
| `SUPABASE_JWT_SECRET` | JWT verification when `RIZWAN_SINGLE_USER_MODE=0` | needed for multi-tenant pivot |
| `G2_MAX_COST_USD` | Per-build cost cap (default `5.0`) | optional |
| `G2_MIN_PERSONA_QUALITY` | Quality gate (`low` / `medium` / `high`; default `medium`) | optional |
| `DAILY_COST_ALERT_USD` | Cost-alerter daily threshold (default `20`) | optional |
| `SLACK_WEBHOOK_URL` | Cost-alert dispatch | optional |
| `SENDGRID_API_KEY` · `SENDGRID_FROM_EMAIL` | Email alerts | optional |
| `WORKER_CONCURRENCY` | RQ worker pool size (default `1`) | optional |
| `PERPLEXITY_MODEL_RECENCY` · `PERPLEXITY_MODEL_STRATEGIC` | Override Sonar / Sonar-pro | optional |
| `APOLLO_BASE_URL` · `APOLLO_HTTP_TIMEOUT` | Apollo HTTP defaults | optional |
| `JOB_VALIDATOR_STALE_HOURS` | Validator cache (default `6`) | optional |
| `ORPHAN_REAPER_INTERVAL_MIN` | Reaper sweep cadence (default `5`) | optional |

Full reference: [`.env.example`](.env.example)

---

## Deployment

### Railway — three services off the same image

1. **API** (`START_MODE=api`) — FastAPI on port 8080 (auto-configured by Railway)
2. **Worker** (uses `Dockerfile.worker`) — `python -m api.worker` long-running
3. **Scheduler** (`START_MODE=scheduler`) — cron daemon:
   - Daily 09:00 GST · `JobScoutAgent.run()`
   - Daily 21:00 GST · `BossAgent.run()`
   - Daily 22:00 GST · `CostAlerter.check_daily_spend()`
   - Sun 03:00 GST · `PersonaSynthesizer.run()`
   - Sun 09:00 GST · `CostAlerter.send_weekly_digest()`
   - Every 30 min · `agents/job_validator.py` revalidates JD URLs
   - Every 30 min · `agents/linkedin_scheduler.py` notifies on scheduled drafts
   - Every 5 min · `agents/orphan_reaper.py` reaps stuck `jobs_runs`
4. **Redis plugin** — provides `REDIS_URL`

All four services share the same env vars (set once at the project level).

### Vercel — dashboard

```bash
cd dashboard && vercel deploy --prod
```
**Project Settings:**
- Root Directory: `dashboard`
- Production Branch: `main`
- Environment Variables: `NEXT_PUBLIC_API_URL=https://<your-railway-app>.up.railway.app` and `API_SECRET_KEY=<same as Railway>`
- Deployment Protection: **Disabled** for the public production deploy (the dashboard's `/api/proxy/*` route is the only path that holds the secret; public read access is fine for self-use)

---

## Dashboard routes

| Route | What |
|---|---|
| `/today` | Ranked action queue — what to do right now |
| `/applications` | Application kanban |
| `/applications/[id]/workspace` | 5-tab workspace (Role / Resume / Network / Interview Prep / Apply) |
| `/applications/[id]/interview-studio` | 3-pane studio (Prep / Tutor chat / Outcome logger) |
| `/applications/[id]/offer` | G8 offer evaluation dashboard (market analysis + negotiation strategy + risks) |
| `/linkedin` | Content calendar — drafts, scheduled, posted |
| `/network` | Warm-intro paths + LinkedIn CSV import |
| `/insights` | Personas + Costs + System (Boss agent audit) |
| `/insights?tab=analytics` | Pattern analytics — funnel by grade/archetype, rejection signals, cost-per-outcome |
| `/insights?tab=traces` | LangGraph run traces — per-run cost, latency, error inspection |
| `/companies` · `/companies/[name]` | Target companies + research intel |
| `/jobs/[id]` · `/jobs/[id]/resume` | Per-job detail + outcome logger |
| `/personas/[name]` | Per-company persona (success/failure patterns + ATS bank + version history) |
| `/profile` · `/profile/keywords` · `/profile/recommendations` · `/profile/sources` | Master profile surfaces |
| `/admin` | Allowlist-gated admin panel (Boss, eval reports, queue) |

---

## Cost model

| Operation | Provider | Typical cost | Cadence |
|---|---|---|---|
| G2 resume build | Anthropic + DeepSeek + Kimi + Gemini (12-node ensemble) | ~$1 / build | Per application |
| G2 Quick tweak | Claude Opus (single call) | ~$0.05 | Per edit |
| G2 Rebuild section | 3-node mini-graph (Opus writer + critic + polish) | ~$0.30–0.50 | Per section edit |
| G3 interview prep | Multi-LLM ensemble | ~$0.50 | Per interview round |
| G4 LinkedIn draft | Sonnet + Opus + Sonnet critic + Opus polish + Sonnet image_brief | ~$0.15 / draft | 3× / week |
| Perplexity recency | Sonar | ~$0.005 / company | Weekly × 71 = ~$1.50/mo |
| Perplexity strategic | Sonar-pro | ~$0.012 / company | Monthly × 71 = ~$0.85/mo |
| Apollo enrich | Apollo (1 credit) | ~$0.10 / call | Per target (one-shot) |
| Persona deep-research | Apify + Gemini long-context | ~$0.20 / company | Refresh on demand |

**Steady-state monthly burn:** ~$30–80 (Anthropic dominated; Perplexity ~$2.50 incremental; Apollo gated on credits). Audit identifies 40–60% reduction available from prompt caching, Sonnet-for-critics, and conditional ensemble fan-out — see [`docs/AUDIT_2026_05_10.md`](docs/AUDIT_2026_05_10.md).

---

## What's parked

- **Apollo paid-plan upgrade** — enables `/search-people` (referral-graph seeding) and `/search-companies` (canonical org_id lookup). Free plan returns 402.
- **Hybrid resume edit auto-replace edge case** — currently auto-replaces on Full rebuild success with confirm-if-dirty; mid-edit Quick-tweak doesn't auto-apply yet (component state).
- **Chrome browser scrape integration** — code path possible via `Claude in Chrome` MCP but no extension currently connected. Apify covers the same use cases for now.
- **Multi-tenant pivot endpoint wiring** — 65 endpoints in `api/server.py` still use service-role; `Depends(get_current_user)` ready but not yet wired. Recipe in [`api/AUTH.md`](api/AUTH.md).
- **Audit Tier-1 cost cuts** — prompt caching, Sonnet swaps, Haiku for orchestration. Documented, not implemented.
- **Outcome-driven persona evolution dashboard** — schema in place (`persona_versions` table); UI surface not yet built.

---

## Output files (legacy, kept for back-compat)

| Location | Contents |
|---|---|
| `output/resumes/` | Tailored `.docx` + `.pdf` per job |
| `output/reports/` | Evaluation reports, email drafts |
| `output/interview_prep/` | STAR story banks, likely questions |
| Supabase `jobs` table | All discovered jobs with scores |
| Supabase `company_knowledge` | Multi-source RAG (Apify + Perplexity + Apollo, tagged via `metadata.source`) |
| Supabase `applications` | Application tracking |
| Supabase `resume_builds` | Every G2 build with full transcript + cost telemetry + user edits |
| Supabase `interview_outcomes` · `resume_outcomes` | The data flywheel — drives `outcome_score` on knowledge rows + persona evolution |
| Supabase `linkedin_drafts` · `linkedin_voice_profile` · `linkedin_posting_schedule` | LinkedIn engine state |
| Supabase `people` · `employments` · `edges` · `target_company_employees` | Referral graph |
