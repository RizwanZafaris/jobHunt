# jobHunt — AI Job Landing System

> **Outcome-conditioned, peer-network-aware, persona-evolved job hunt.**
> Every resume, email, and LinkedIn post improves from real interview
> outcomes propagated back across your peer graph — on top of a
> three-source enrichment pipeline that nobody in the competitive set runs.

Built for **Rizwan Zafar** (single-user, lifetime plan) today; the schema,
auth layer, and rate limiter are multi-tenant-ready for tomorrow.

**Status — 2026-05-31.** The production stack is live: FastAPI on Railway
(API + embedded scheduler + RQ worker), Supabase Postgres + pgvector, and a
Next.js 15 dashboard on Vercel. The system currently tracks ~68 target
companies with several thousand rows of synthesized company knowledge.
Shipping today: the multi-stage **G2** resume graph (~$1 / ~5 min per build,
now with on-the-fly DOCX/PDF export), **G3** interview studio + tutor, **G4**
LinkedIn engine with image briefs, **G5** A–F letter-grade fit scoring, **G6**
follow-up cadence, **G7** application-form assist, **G8** offer evaluation, and
**G9** STAR+R story bank, plus `/insights?tab=analytics` pattern analytics and
`/insights?tab=traces` LangGraph debugging. The embedded APScheduler is now
gated by `SCHEDULER_ENABLED` with a dedicated single-replica `scheduler`
service so the API can scale horizontally without cron double-firing (see
[`docs/SCALABILITY.md`](docs/SCALABILITY.md)). 46 forward-only migrations
(001–045) are in `db/migrations/`.

> Architecture decisions — *why LangGraph, why per-company personas, why
> Anthropic for the writer, why three-source enrichment* — live in
> [`BUILD_RATIONALE.md`](BUILD_RATIONALE.md). A running build log is in
> [`WHAT_WAS_BUILT.md`](WHAT_WAS_BUILT.md).

---

## Table of contents

- [What makes this different](#what-makes-this-different)
- [Architecture](#architecture)
- [Scaling & production readiness](#scaling--production-readiness)
- [The agent layer](#the-agent-layer)
- [LangGraph state machines](#langgraph-state-machines)
- [Multi-LLM router & model tiering](#multi-llm-router--model-tiering)
- [Data model & migrations](#data-model--migrations)
- [API surface](#api-surface)
- [Dashboard](#dashboard)
- [Scheduling & background work](#scheduling--background-work)
- [Local setup](#local-setup)
- [Environment variables](#environment-variables)
- [Deployment](#deployment)
- [Cost model](#cost-model)
- [In progress & parked](#in-progress--parked)
- [Repository layout](#repository-layout)

---

## What makes this different

Three product wedges that no single competitor combines:

1. **Outcome-conditioned RAG.** Every G2 resume build emits
   `cite:knowledge_id=<uuid>` markers tying each claim to the knowledge row
   that justified it. When you log an interview win or loss, the
   `outcome_to_persona` worker propagates Bayesian credit back to the
   specific knowledge rows that drove (or failed to drive) the callback.
   Personas evolve from real outcomes, not vibes.
   *(`agents/outcome_to_persona.py`; migrations 008/009.)*

2. **Peer-network referral graph.** A LinkedIn connections import seeds a
   `people / employments / edges / target_company_employees` schema, and a
   NetworkX Dijkstra path-finder returns warm-intro paths (1–2 hops, scored
   by geometric-mean tie strength). The `/network` UI surfaces the best
   intro path per target company.
   *(`agents/referral_graph.py`; migration 004.)*

3. **Three-source enrichment pipeline.** Each target company's persona is
   fed by three independent signal sources, each writing to
   `company_knowledge` with a distinct `metadata.source` so outcome credit
   can be attributed to the source that actually mattered:
   - **Apify** — depth scrape (long-form pages, blog posts, reviews)
   - **Perplexity** — recency (Sonar, last-30-day news) + strategy (Sonar-pro)
   - **Apollo** — firmographics, open-jobs index, people search

---

## Architecture

```
                       ┌───────────────────────── Vercel ─────────────────────────┐
                       │   Next.js 15 App Router dashboard (job-hunt-dashboard)    │
                       │   /today → /applications/[id]/workspace → interview-studio│
                       └───────────────────────────┬───────────────────────────────┘
                                                    │  HTTPS (REST)
                       ┌────────────────────────────▼──────────── Railway ─────────┐
                       │  FastAPI  (api/server.py)                                  │
                       │   • REST endpoints + per-route rate limiting (slowapi)     │
                       │   • APScheduler embedded in-process (6 cron jobs)          │
                       │   • enqueues long builds → RQ 'jobhunt' queue              │
                       │                                                            │
                       │  RQ Worker  (api/worker.py, Dockerfile.worker)             │
                       │   • consumes 'jobhunt' queue: G2 / G3 / G4 builds          │
                       │   • orphan reaper (APScheduler thread)                     │
                       └───────────┬───────────────────────────────┬───────────────┘
                                   │                                │
                         Redis (Railway plugin)          Supabase Postgres + pgvector
                          queue + dedup locks             companies / jobs / people /
                                                          company_knowledge / stories /
                                                          applications / resume_builds …
```

**Request flow.** The dashboard renders the daily surface from `/today`
(sectioned per kind, with a résumé-scored **Recommended** list). Opening a
job leads to its **workspace** — role overview, the G2 resume tab
(view / download / Quick-edit / Rebuild-section / Full-rebuild), warm-intro
paths from the referral graph, interview prep, and an apply checklist.
**Interview-studio** layers prep material, a concept ladder, a tutor chat,
and the outcome logger that closes the loop back into persona evolution.
LinkedIn posts (G4) ship with an image brief for copy-and-paste.

Deep docs: [`docs/SCALABILITY.md`](docs/SCALABILITY.md) (scalability audit + multi-tenant roadmap) · [`docs/AUDIT_360_SYNTHESIS.md`](docs/AUDIT_360_SYNTHESIS.md) (6-expert audit + P0/P1/P2 roadmap) · [`docs/SPRINT_1_STATUS.md`](docs/SPRINT_1_STATUS.md) (decisions log) · [`docs/AUDIT_2026_05_10.md`](docs/AUDIT_2026_05_10.md) (cost + quality audit) · [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/G2_RESUME_BUILDER_GRAPH.md`](docs/G2_RESUME_BUILDER_GRAPH.md) · [`docs/G3_INTERVIEW_PREP_GRAPH.md`](docs/G3_INTERVIEW_PREP_GRAPH.md) · [`docs/SECURITY.md`](docs/SECURITY.md)

API surface docs: [`api/AUTH.md`](api/AUTH.md) · [`api/QUEUE.md`](api/QUEUE.md) · [`api/WORKSPACE.md`](api/WORKSPACE.md) · [`api/INTERVIEW_STUDIO.md`](api/INTERVIEW_STUDIO.md) · [`api/LINKEDIN.md`](api/LINKEDIN.md) · [`api/NETWORK.md`](api/NETWORK.md)

---

## Scaling & production readiness

jobHunt runs **single-user in production today** and is built to become a
multi-tenant SaaS. A full CTO-level scalability audit — what breaks going from
1 user to thousands of tenants + concurrent agent workloads, graded P0/P1/P2
with a sequenced roadmap — lives in **[`docs/SCALABILITY.md`](docs/SCALABILITY.md)**.

**Hardened (2026-05-29):** the embedded cron scheduler is gated
(`SCHEDULER_ENABLED`) and has a dedicated single-replica `scheduler` service, so
the API can scale out without cron double-firing; the orphan-reaper crash that
silently disabled job recovery is fixed; GZip compression and worker
self-healing (`restartPolicy=ALWAYS`) are in; and a migration captures the
drifted recommendation columns + adds `/today` hot-path indexes.

**Before onboarding a 2nd paying tenant (audit Phase 1):** drop the global
`UNIQUE` constraints that let one tenant overwrite another's rows, add per-user
JWT auth through the dashboard proxy, scope every `server.py` query by
`user_id`, and actually enforce RLS (the API uses the service-role key, which
bypasses it today). **Phase 2 (throughput):** async data layer, queue split +
worker autoscaling, connection pooler, per-tenant spend caps.

---

## The agent layer

The system is a fleet of ~60 single-purpose agents under `agents/`, plus two
LangGraph packages (`resume_agents/` for G2, `interview_agents/` for G3). All
agents share `agents/base_agent.py`, which routes every LLM call through the
hardened multi-provider router (`agents/llm_router.py` + `llm_hardening.py` +
`llm_fallback.py`).

Selected agents by job:

| Area | Agents |
|------|--------|
| **Discovery** | `job_scout_agent` (ATS + Perplexity discovery, mislabel guard), `career_page_scraper`, `agents/sources/firecrawl_source`, `job_validator` / `job_validation`, `legitimacy_agent`, `document_classifier`, `form_scanner` |
| **Company intel** | `company_agent` (per-company persona), `persona_deep_research` (Apify), `persona_news_check` + `perplexity_search` (recency), `apollo_enrich` (firmographics), `persona_synthesizer`, `comp_research`, `salary_research_agent` |
| **Profile & fit** | `rizwan_agent`, `profile_analyzer`, `scoring_agent` (G5 fit grade), `proof_point_agent` / `proof_point_extractor`, `pattern_analyzer` |
| **Resume (G2)** | `resume_builder_agent`, `resume_edit_assistant`, `voice_injector` (`resume_agents/` graph) |
| **Network (P1.1)** | `referral_graph`, `networking_agent`, `intro_email_agent` |
| **LinkedIn (G4 / G11)** | `g4_linkedin_graph`, `linkedin_scheduler`, `linkedin_voice_extractor`, `g11_*` |
| **Outcome loop** | `outcome_to_persona` (Bayesian credit assignment driving persona evolution) |
| **Application & follow-up** | `application_tracker_agent`, `g6_*` (follow-up), `g7_*` (form assist), `g8_*` (offer eval), `g9_*` + `story_bank_agent` (STAR+R stories) |
| **Orchestration & ops** | `boss_agent` (nightly audit + chat), `cost_alerter`, `llm_router`, `llm_hardening`, `llm_fallback` |

> **Note on the people graph.** On `main`, the referral graph is seeded by a
> **LinkedIn connections CSV import** — that is the live mechanism today. A
> Perplexity/Apollo-based `people_finder_agent` that replaces the CSV import is
> **in progress on an unmerged branch** and is not part of `main` yet.

---

## LangGraph state machines

Heavy multi-step flows are LangGraph `StateGraph`s with Postgres checkpointing,
per-graph cost ceilings, and bounded iteration:

| Graph | Purpose | Entrypoint |
|-------|---------|------------|
| **G2** | Resume build: insider-expert → advocate → writer → dual ATS critics → polisher, iterating to a target ATS score | `resume_agents/g2_graph.py` |
| **G3** | Interview prep: behavioral / domain / technical question gen → mock interview → critic → gap analysis | `interview_agents/g3_graph.py` |
| **G4** | LinkedIn post generation with image brief | `agents/g4_linkedin_graph.py` |
| **G6** | Follow-up cadence (post-application nudges) | `agents/g6_graph.py` |
| **G7** | Application-form assist (scan fields → draft answers) | `agents/g7_graph.py` |
| **G8** | Offer evaluation against market + targets | `agents/g8_io.py` |
| **G9** | STAR+R story-bank extraction from the master CV | `agents/g9_graph.py` |
| **G11** | Voice calibration / injection | `agents/g11_graph.py` |
| **Referral** | Warm-intro path-finding over the peer graph | `agents/referral_graph.py` |

The G2 and G3 graphs are gated behind `USE_G2_GRAPH` / `USE_G3_GRAPH` feature
flags and carry their own cost ceilings (`G2_MAX_COST_USD`, `G3_MAX_COST_USD`).

---

## Multi-LLM router & model tiering

`agents/llm_router.py` fronts six providers behind one `ask()` interface, with
per-call cost/latency/token accounting written to `agent_call_log`. Failures
cascade through a hardening layer (schema validation + one structured retry)
to a terminal **OpenRouter** fallback rail so a single provider outage never
hard-fails a graph.

**Providers:** Anthropic · OpenAI · Google (Gemini) · DeepSeek ·
Moonshot (Kimi) · OpenRouter (terminal fallback).

**Per-role model assignments** (`config/settings.py`). A 2026-05 cost audit
re-tiered six roles from Opus → Sonnet; **Boss** and the **G2 polisher** stay
on Opus where final-mile quality justifies the cost:

| Role | Model |
|------|-------|
| Company / Rizwan / Interview agents | `claude-sonnet-4-6` |
| Boss agent | `claude-opus-4-5` *(kept)* |
| Job scout | `gpt-4.1` |
| Embeddings | `text-embedding-3-small` |
| **G2** insider-expert / meta-critic | `gemini-2.5-pro` |
| **G2** advocate / writer / orchestrator | `claude-sonnet-4-6` |
| **G2** ATS critic A / B | `deepseek-reasoner` / `kimi-k2.5` |
| **G2** polisher | `claude-opus-4-5` *(kept)* |
| **G3** behavioral / domain / star-matcher | `claude-haiku-4-5` |
| **G3** technical | `gemini-2.5-pro` |
| **G3** mock-interviewer / gap-analyzer | `claude-sonnet-4-6` |
| **G3** mock-critic | `deepseek-reasoner` |
| **G4 / G6** | `claude-sonnet-4-6` (Opus for select G4 steps) |

**Key thresholds** (`config/settings.py`):

| Setting | Value |
|---------|-------|
| `fit_score_threshold` | 40 |
| `apply_threshold` | 85 |
| `max_jobs_per_run` | 20 |
| `g2_target_ats_score` | 95 |
| `g2_max_iterations` | 3 |
| `g2_max_cost_usd` | 5.0 |
| `g3_max_cost_usd` | 3.0 |
| `g4_max_cost_usd` | 0.15 |

---

## Data model & migrations

Supabase Postgres with the `pgvector` extension for semantic search over
`company_knowledge`, `story_bank`, and the Rizwan profile.

Migrations are **forward-only SQL** files in `db/migrations/`, applied in
lexical order by `db/migrations/APPLY.sh`, which records applied filenames in
a `schema_migrations` table (safe to re-run) and refuses to run against an
obviously-production `DATABASE_URL` without an explicit confirmation.

The tree currently holds **46 numbered migrations**, from
`2026_05_10_001_multi_tenancy.sql` through
`2026_05_31_045_user_onboarding.sql`. Highlights:

- **001–005** — multi-tenancy, status enum, `jobs_runs`, referral graph, LinkedIn drafts
- **008/009** — outcome credits + `search_company_knowledge` v2 (outcome-conditioned RAG)
- **013** — `comp_cache` and `companies.is_phantom` (scraping-artifact guard)
- **015–018** — story bank + `search_story_bank` v2
- **019/020** — `jobs.fit_score_breakdown` + legitimacy v1
- **024/026/027** — proof points, offer evaluations, analytics views
- **028/030/035** — phantom-job cleanup + archival
- **031/032/033** — RLS on `boss_audit_log`, `security_invoker` views, failed-build error constraint
- **036–038** — job-card dismissals, surface tracking, Buffer integration
- **039–045** — recommendation columns + `/today` hot-path indexes, agent cost rollup, pgvector HNSW index, dismissals→job FK, composite-unique indexes, drop of global UNIQUEs, user onboarding

```bash
export DATABASE_URL='postgres://...'
bash db/migrations/APPLY.sh
```

Standalone schema files (`db/schema.sql`, `db/profile_schema.sql`,
`db/g3_schema.sql`, `db/multi_llm_schema.sql`, …) document the canonical shape;
`db/SCHEMA_LIVE_STATE.md` tracks what is actually applied in production.

---

## API surface

FastAPI app in `api/server.py`. Core endpoints authenticate with an
`X-Secret-Key` header; the feature routers use `Depends(get_current_user)`,
which short-circuits to user #1 (Rizwan) when `RIZWAN_SINGLE_USER_MODE=1`.
A global 60/min rate limit applies, with tighter per-route overrides on
LLM-generation and import routes (`/health` is exempt).

**Core (server.py):**

| Method & path | Purpose |
|---------------|---------|
| `GET /` · `GET /health` · `GET /ready` | Service banner · Railway healthcheck · readiness probe |
| `GET /debug/apify-check` · `GET /debug/provider-ping` | Provider/credential diagnostics |
| `POST /pipeline/run` · `POST /pipeline/evaluate` · `GET /pipeline/stats` | Full pipeline · single-JD eval · stats |
| `GET /jobs` · `GET /jobs/{id}` | List (open by default; `letter_grade` filter) · detail |
| `GET /jobs-runs/{run_id}` | Poll async build status |
| `GET/POST /companies` · `/companies/build` · `/companies/targets` · `PUT/DELETE /companies/{id}` | Company + target-list CRUD |
| `POST /interview-prep` | Generate interview prep (G3) |
| `GET /digest/latest` · `POST /boss/audit` · `POST /boss/chat` | Boss digest · audit · grounded chat |
| `POST /networking/strategy` | Outreach strategy + messages |
| `POST /salary/research` · `POST /salary/evaluate-offer` | Comp research · offer eval |
| `POST /applications/review` · `GET /applications/pipeline` | Follow-up surfacing · pipeline report |
| `GET /resumes/{filename}` · `/resume-builds/*` | Download · build view / edit / feedback / download |
| `GET/PUT /profile` · `/profile/keywords` · `/profile/sources` · `/profile/experience/{id}` · `/profile/recommendations` | Master profile + keyword bank + recommendations |
| `GET/POST /me/onboarding` | Per-user onboarding state (Phase 4) |
| `GET /admin/selftest` · `GET /admin/margin` · `POST /admin/personas/rebuild-all` | Dependency self-test · per-tenant gross-margin report · batch persona rebuild |

**Feature routers** (each mounted in `server.py`):

`/network/*` (referral graph) · `/linkedin/*` (engine + drafts) ·
`/actions/*` (`/today`, `/today/recommended`, `/today/trigger-scout`,
`/today/trigger-validator`) · `/workspace/*` (application workspace + resume
tab) · `/interview-studio/*` · `/perplexity/*` (persona recency) ·
`/apollo/*` (firmographic + hiring intel) · `/workspace/stories/*` (G9) ·
`/workspace/follow-ups/*` (G6) · `/workspace/{job_id}/apply` +
`/workspace/applications/*` (G7) · `/profile/proof-points/*` · `/profile/*`
(G11 voice) · `/offers/*` (G8) · `/analytics/*` (pattern analytics) ·
`/traces/*` (LangGraph trace debugging) · `/buffer/*` +
`/linkedin/drafts/*/schedule-to-buffer` (Buffer integration).

---

## Dashboard

`dashboard/` — Next.js 15 (App Router, `job-hunt-dashboard`), deployed to
Vercel. Routes:

| Route | What it shows |
|-------|---------------|
| `/today` | Daily surface — per-kind sections + résumé-scored Recommended list |
| `/applications` · `/applications/[id]` | Pipeline · application detail |
| `/applications/[id]/workspace` | Role overview, resume tab, network, prep, apply checklist |
| `/applications/[id]/assist` · `/interview-studio` · `/offer` | G7 form assist · G3 studio · G8 offer eval |
| `/jobs/[id]` · `/jobs/[id]/resume` | Job detail · resume builder |
| `/companies` · `/companies/[name]` | Target companies · persona detail |
| `/personas` · `/personas/[name]` | Persona library |
| `/network` · `/linkedin` | Warm-intro paths · LinkedIn drafts |
| `/profile` · `/profile/keywords` · `/profile/recommendations` · `/profile/sources` | Profile editor + keyword bank + AI recommendations + source docs |
| `/boss` · `/costs` | Boss chat · cost dashboard |
| `/insights` (+ `/insights/analytics`) | Pattern analytics + LangGraph trace debugging tabs |
| `/login` · `/signup` · `/onboarding` | Google OAuth front door + onboarding flow (Phase 4, flag-gated) |
| `/admin` · `/(legacy)/pipeline` | Admin · legacy pipeline view |

---

## Scheduling & background work

APScheduler runs **inside the API process** (`main.start_scheduler_background`,
wired on FastAPI startup) — so a single Railway service handles both API and
cron. Six jobs are scheduled:

`job_scout` (09:00) · `boss_agent` (21:00) · `persona_synthesis` (weekly) ·
`cost_alert` · `cost_digest` · `g6_followup`.

Times are in `Asia/Dubai` (`config/settings.py`). Long-running LangGraph builds
(G2/G3/G4) are **not** run inline — they are enqueued to the Redis-backed
`jobhunt` RQ queue and processed by the separate worker service; an orphan
reaper sweeps stalled jobs.

---

## Local setup

Requires **Python 3.12** and **Node 18+**.

```bash
# 1. Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env            # fill in keys — see below
python scripts/check-prod-config.py   # validates .env against .env.example

# 3. Database (point DATABASE_URL at your Supabase project)
bash db/migrations/APPLY.sh

# 4. Run the API (also starts the embedded scheduler)
python main.py --api            # http://localhost:8000  (docs at /docs)

# 5. (optional) Run the queue worker for async G2/G3/G4 builds
START_MODE=worker python main.py    # requires REDIS_URL

# 6. Dashboard
cd dashboard && npm install && npm run dev   # http://localhost:3000
```

Tests: `pytest` for the backend (the dev environment uses
`~/crewai-env/bin/pytest`); `npm test` (vitest) in `dashboard/`.

---

## Environment variables

`.env.example` is the **single source of truth** — it is parsed by
`scripts/check-prod-config.py` to verify a deployment is correctly configured.
Grouped, with tiers (✅ required · ➕ recommended · ◦ optional):

| Group | Variables |
|-------|-----------|
| **Database** | ✅ `SUPABASE_URL`, ✅ `SUPABASE_SERVICE_KEY`; ◦ `SUPABASE_ANON_KEY`, `DATABASE_URL`, `SUPABASE_DB_URL`, `POSTGRES_URL` |
| **LLM providers** | ✅ `ANTHROPIC_API_KEY`, ✅ `OPENAI_API_KEY`; ➕ `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`/`KIMI_API_KEY`; ➕ `OPENROUTER_API_KEY` (terminal fallback rail) |
| **Enrichment** | ➕ `APIFY_TOKEN`; ➕ `PERPLEXITY_API_KEY` (+ `PERPLEXITY_MODEL_RECENCY=sonar`, `PERPLEXITY_MODEL_STRATEGIC=sonar-pro`); ◦ `APOLLO_API_KEY` (+ `APOLLO_BASE_URL`, `APOLLO_HTTP_TIMEOUT`); ➕ `SERPER_API_KEY` (+ `USE_SERPER=0`) |
| **Queue / worker** | ◦ `REDIS_URL` (required for the worker), `WORKER_CONCURRENCY=1`, `RQ_QUEUE_NAME=jobhunt`, `ORPHAN_REAPER_INTERVAL_MIN=5`, `JOB_VALIDATOR_STALE_HOURS=6` |
| **Auth / mode** | `SUPABASE_JWT_SECRET`, `RIZWAN_SINGLE_USER_MODE=1` |
| **Graph flags** | `USE_G2_GRAPH=false`, `G2_MAX_COST_USD=5.0`, `G2_MIN_PERSONA_QUALITY=medium` (+ G2 model overrides); `USE_G3_GRAPH=false`, `G3_MAX_COST_USD=3.0` |
| **Notifications** | `SENDGRID_*`, `DIGEST_EMAIL_TO`; `SLACK_WEBHOOK_URL`, `DAILY_COST_ALERT_USD=20` |
| **Scheduling** | `JOB_SCOUT_TIME=09:00`, `BOSS_AGENT_TIME=21:00`, `TIMEZONE=Asia/Dubai`, `START_MODE`; `SCHEDULER_ENABLED=1` (set `0` on the API service when a dedicated `scheduler` service runs cron, so it fires once) |
| **Railway** | ✅ `PORT=8000`, ✅ `ENVIRONMENT`, ✅ `SECRET_KEY` |
| **Buffer** | `BUFFER_CLIENT_ID`/`SECRET`/`REDIRECT_URI`, `BUFFER_API_BASE=https://api.bufferapp.com/1` |
| **Eval / cache** | `EVAL_GOLDEN_DIR`, `ANTHROPIC_PROMPT_CACHE_ENABLED` |

---

## Deployment

**Railway** (`railway.toml`) defines two services backed by a Redis plugin:

- **`api`** — `python main.py --api` (Dockerfile). Public FastAPI; healthcheck
  at `/health`; APScheduler cron embedded in-process; enqueues builds to Redis.
- **`worker`** — headless RQ worker (`Dockerfile.worker`, `START_MODE=worker`,
  `WORKER_CONCURRENCY=1`, `RQ_QUEUE_NAME=jobhunt`). Consumes the `jobhunt`
  queue and runs the long G2/G3/G4 graphs independently of API redeploys.
  Scale horizontally when build throughput becomes the constraint. The orphan
  reaper runs as an APScheduler thread inside this service.

Redis is supplied by Railway's Redis plugin (`REDIS_URL` auto-injected).

> **Cron must run in exactly one place.** By default the 6 cron jobs are
> embedded in the API process (`SCHEDULER_ENABLED` defaults on), so a minimal
> one-service deploy still runs cron. When you scale the API to **>1 replica**,
> deploy the dedicated `scheduler` service (above) **and set
> `SCHEDULER_ENABLED=0` on the API service**, otherwise every replica
> double-fires all 6 jobs (N× LLM spend). See [`docs/SCALABILITY.md`](docs/SCALABILITY.md).

**Vercel** hosts the `dashboard/` Next.js app (`dashboard/vercel.json`),
calling the Railway API over REST.

> CORS is read from `cors_allowed_origins` (never wildcard in production);
> single-user mode falls back to `http://localhost:3000` when no origins are set.

---

## Cost model

Every LLM call is metered (provider, tokens, latency, USD) into
`agent_call_log`. A nightly cost digest and a `DAILY_COST_ALERT_USD` Slack
alert keep spend visible. Indicative steady-state costs:

- **G2 resume build:** ~$1, ceiling `g2_max_cost_usd=5.0`, ≤3 iterations
- **G3 interview prep:** ceiling `g3_max_cost_usd=3.0`
- **G4 LinkedIn post:** ceiling `g4_max_cost_usd=0.15`
- **Boss chat:** ~$0.05–0.15 per turn (Opus, `max_tokens=1500`)

Cost controls in place: P0 model re-tiering (Opus → Sonnet on six roles),
Anthropic prompt caching on large system prompts, OpenAI `prompt_cache_key`
on GPT-4.1 calls, and a cache-aside layer over Perplexity searches.

---

## In progress & parked

- **People-finder (Perplexity/Apollo) — partly merged.** The `/network` UI
  now uses an Apollo people-finder modal (the old LinkedIn CSV upload button is
  gone), but the backend `people_finder_agent` is still on an unmerged feature
  branch; the `POST /network/import/linkedin-csv` endpoint remains as the live
  seeding mechanism on `main`.
- **Multi-tenant cutover — deferred.** The rate limiter keys on remote IP and
  `get_current_user` short-circuits to user #1; both have TODOs for the
  per-user switch once single-user mode is lifted.

---

## Repository layout

```
agents/            ~60 single-purpose agents + LLM router/hardening/fallback
resume_agents/     G2 resume LangGraph (graph / nodes / state / io / run)
interview_agents/  G3 interview LangGraph
api/               FastAPI server + feature routers (network, linkedin, actions,
                   workspace, interview_studio, apollo, stories, follow_ups, g7,
                   offers, analytics, traces, buffer, queue, worker, …)
config/            settings.py (model tiering, thresholds), profile.yml
db/                client, schema files, migrations/ (46 numbered SQL files)
dashboard/         Next.js 15 App Router frontend (Vercel)
evals/             golden-set evaluations
integrations/      external service clients
scripts/           ops scripts (incl. check-prod-config.py)
tests/             pytest suite
pipeline.py        end-to-end pipeline orchestration
main.py            entrypoint (--api | worker | scheduler via START_MODE)
portals.yml        ATS/portal discovery config
cv.md              master CV (source for G9 story extraction)
Dockerfile         api image     Dockerfile.worker   worker image
railway.toml       Railway service topology
```

---

*Single-user build for Rizwan Zafar; multi-tenant-ready underneath.
See [`BUILD_RATIONALE.md`](BUILD_RATIONALE.md) for the “why.”*
