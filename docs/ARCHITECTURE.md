# AI Job Hunt — System Architecture

**Status**: Phase 0 (multi-LLM router) merging · Phase 1+ (LangGraphs) designed
**Last updated**: 2026-05-09

This is the canonical architecture doc. See `docs/G2_RESUME_BUILDER_GRAPH.md`
for the deep dive on the resume builder graph.

---

## 1. What this system is

A single-tenant (today) → multi-tenant (planned) autonomous job hunt
platform. End-to-end:

```
   target companies + master profile
              │
              ▼
   discover jobs at those companies (G1)
              │
              ▼
   score + classify + assess legitimacy
              │
              ▼  (manual gate at score ≥ 85)
   build ATS-passing tailored resume + cover email (G2)
              │
              ▼
   submit application (manual)
              │
              ▼
   networking outreach + interview prep + negotiation (G3)
              │
              ▼
   log outcome → personas learn → next build is smarter
```

Three **LangGraphs** (G1, G2, G3) plus shared **Supabase** state plus a
five-provider **LLM router**. Each graph is independently deployable.

---

## 2. Layered view

```
┌───────────────────────────────────────────────────────────────────────┐
│  PRESENTATION                                                         │
│  Next.js 14 dashboard (Vercel)                                        │
│  - Jobs table · Applications kanban · Profile editor                  │
│  - "Generate Resume" / "Prep Interview" / "Log Outcome" actions       │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ /api/proxy/* (server-side secret)
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  API                                                                  │
│  FastAPI on Railway · ~30 endpoints                                   │
│  Triggers graph runs as background tasks                              │
└─────┬───────────────────┬────────────────────────────┬────────────────┘
      ▼                   ▼                            ▼
  ┌────────┐         ┌────────┐                  ┌────────────┐
  │  G1    │         │  G2    │                  │     G3     │
  │ Disco- │         │ Resume │                  │ Conversion │
  │ very   │         │Builder │                  │ (3 stages) │
  └───┬────┘         └────┬───┘                  └─────┬──────┘
      └──────────┬────────┴────────────────────────────┘
                 ▼
┌───────────────────────────────────────────────────────────────────────┐
│  AGENT LAYER                                                          │
│  BaseAgent (ABC) → LLMRouter → 5 providers                            │
│  Per-company personas (system prompt + ATS bank + success patterns)   │
└───────────────────────────────┬───────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LLM ROUTER (agents/llm_router.py)                                    │
│  ┌──────────┬─────────┬────────┬──────────┬──────────────┐            │
│  │Anthropic │ OpenAI  │ Google │ DeepSeek │   Moonshot   │            │
│  │ Claude   │ GPT     │ Gemini │  R1+V3   │     K2       │            │
│  │ Opus 4.5 │         │ 2.5 Pro│          │              │            │
│  └──────────┴─────────┴────────┴──────────┴──────────────┘            │
│  Cost + latency tracking → agent_call_log table                       │
└───────────────────────────────┬───────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  DATA  (Supabase: Postgres + pgvector + Storage)                      │
│                                                                       │
│  Existing                            New (multi_llm_schema.sql)       │
│  ─────────                           ───────────────────────────      │
│  companies                           agent_call_log                   │
│  company_knowledge (13 sections)     company_personas                 │
│  jobs                                resume_builds                    │
│  applications                        resume_outcomes ◄── learning loop│
│  rizwan_profile / profile_*          ats_test_results                 │
│  story_bank                          interview_outcomes               │
│  agent_conversations                 graph_checkpoints (LangGraph)    │
│  boss_audit_log                                                       │
│                                                                       │
│  Storage: resumes/ · emails/ · interview-packs/                       │
└───────────────────────────────┬───────────────────────────────────────┘
                                ▼
┌───────────────────────────────────────────────────────────────────────┐
│  EXTERNAL                                                             │
│  Serper · Greenhouse · Lever · Ashby · Workday · SmartRecruiters      │
│  Playwright (fallback) · SendGrid · (future) Common Room · LinkedIn   │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 3. The three LangGraphs at a glance

| | G1 Discovery | G2 Resume Builder | G3 Conversion |
|---|---|---|---|
| **Cadence** | Cron 09:00 GST + on-add | On-demand (user click) | State-driven (status change) |
| **Trigger** | APScheduler / `POST /companies/research` | `POST /jobs/{id}/generate-resume` | `applications.status` transitions |
| **Gate** | None | Job score ≥ 85 | None |
| **State key** | `pipeline_run_id` | `resume_build_id` | `application_id` |
| **Lifetime** | Minutes (per run) | Minutes (per build, max 3 loops) | Days–weeks |
| **Output** | Scored jobs + company knowledge + persona | Resume + cover email + transcript | Outreach drafts + interview pack + negotiation brief |
| **Replaces today** | `JobScoutAgent` + `CompanyAgent.build_or_refresh()` | `_process_single_job()` resume path | `InterviewAgent`, `NetworkingAgent`, `SalaryResearchAgent`, `ApplicationTrackerAgent` |

Each graph has its own `docs/<graph>_DESIGN.md`. G2 is fully spec'd today.

---

## 4. Model assignment policy

**Quality-first.** Each node's model is chosen for what that model is uniquely good at, not for cost or diversity per se.

| Strength | Best model | Used for |
|---|---|---|
| Long context (1M+) | Gemini 2.5 Pro | Insider Expert (full company_knowledge), Meta-Critic (5 prior transcripts) |
| Native search grounding | Gemini 2.5 Pro | Live company news, hiring manager research |
| Executive prose / voice | Claude Opus 4.5 | Writer, Polisher, Cover Email, Outreach drafts |
| Narrative reasoning / judgment | Claude Opus 4.5 | Advocate, Orchestrator, Leverage Analyzer |
| Reasoning at low cost | DeepSeek R1 | ATS Critic A, Mock Interview Critic, Counter Risk Red Team |
| Cheap classification at volume | DeepSeek V3 | Job batch scoring (replaces GPT-4.1) |
| Adversarial second opinion | Kimi K2 | ATS Critic B, Red-flag verification |

**Concentration shifts** vs. today (95% Claude):

| Model family | Today | Phase 1 |
|---|---|---|
| Claude Opus 4.5 | 95% | ~50% |
| Gemini 2.5 Pro | 0% | ~25% |
| DeepSeek (R1+V3) | 0% | ~15% |
| Kimi K2 | 0% | ~5% |
| OpenAI (GPT, embeddings) | 5% | ~5% |

---

## 5. Data layer

### 5.1 Existing tables (from `db/schema.sql`)

| Table | Purpose |
|---|---|
| `companies` | Company registry + ATS slugs |
| `company_knowledge` | 13-section pgvector intel store (built by CompanyAgent) |
| `jobs` | Discovered postings + scores + status |
| `applications` | Application tracker (kanban) |
| `rizwan_profile` + `profile_*` | Master profile, experience, certs, keywords |
| `story_bank` | STAR+R interview stories |
| `agent_conversations` | Gap-filling dialogue history |
| `boss_audit_log` | Nightly digest history |

### 5.2 New tables (from `db/multi_llm_schema.sql`)

| Table | Purpose |
|---|---|
| `agent_call_log` | Per-LLM-call cost + latency telemetry |
| `company_personas` | Auto-synthesized per-company prompts + ATS keyword bank + success/failure patterns |
| `resume_builds` | One row per G2 invocation, full transcript + scores |
| `resume_outcomes` | The learning loop — user-logged outcomes |
| `ats_test_results` | Pre-flight ATS scores from external APIs |
| `interview_outcomes` | Per-round interview results |
| `graph_checkpoints` | Managed by `langgraph-checkpoint-postgres` |

Two views ship with the migration:
- `v_daily_llm_cost` — daily rollup by provider/model
- `v_company_conversion_funnel` — resumes → responses → interviews → offers per company

---

## 6. The learning loop (the moat)

Without this, the system never improves. Every other piece serves it.

```
   USER LOGS OUTCOME (5-second dashboard form)
       │
       ▼
   resume_outcomes + interview_outcomes
       │
       ▼
   WEEKLY persona_synthesizer (cron, Sunday)
   for each company:
     - read 30 days of outcomes + transcripts
     - identify success patterns (bullets that got interviews)
     - identify failure patterns (bullets in rejected resumes)
     - regenerate company_personas row
       │
       ▼
   G2 next build for THIS company
   loads richer persona →
   Insider Expert system prompt has
   concrete success/failure examples
```

When `n_examples_used ≥ 50` per company → LoRA fine-tuning becomes viable
(Phase 6, far future). Until then, retrieval-augmented agent specialization
on a shared base model is the right approach.

---

## 7. Deployment topology

```
┌─────────────────────┐    ┌──────────────────────┐
│  Vercel             │    │  Railway              │
│                     │    │                       │
│  Next.js 14         │◄──►│  FastAPI service      │
│  - SSR pages        │    │  - api/server.py      │
│  - SWR client       │    │                       │
│  - /api/proxy       │    │  Scheduler service    │
│                     │    │  - cron 09:00 (G1)    │
│                     │    │  - cron 21:00 (boss)  │
│                     │    │  - weekly (personas)  │
│                     │    │  - state poll (G3)    │
└──────────┬──────────┘    └──────────┬────────────┘
           │                          │
           └──────────────┬───────────┘
                          ▼
                 ┌─────────────────────┐
                 │  Supabase            │
                 │  - Postgres+pgvector │
                 │  - Storage buckets   │
                 │  - Auth (when SaaS)  │
                 └─────────────────────┘
                          │
              External: Serper · ATS APIs ·
              LLM providers · SendGrid
```

Future LangGraph hosting:
- **LangGraph Cloud** (managed) — easiest scaling
- **Self-host** — split each graph as its own Railway service when load justifies, sharing Supabase Postgres for checkpointing

---

## 8. Build phases

| Phase | Status | What lands |
|---|---|---|
| **0** | 🔄 in progress | LLMRouter, settings extension, env keys, base_agent refactor, multi_llm_schema, design docs |
| **1** | designed | G2 resume builder graph (the highest-value, highest-token-spend thing) |
| **2** | designed | `resume_outcomes` UI + meta-critic learning loop (no code without outcome data) |
| **3** | sketched | G3 Stage B (interview prep graph) |
| **4** | sketched | G1 refactor → LangGraph form + persona auto-synthesis |
| **5** | sketched | G3 Stages A + C (networking, negotiation) |
| **6** | future | SaaS prep — multi-tenant, billing, tiered model routing |
| **7** | far future | LoRA per top-3 companies (only after 50+ outcomes per company) |

---

## 9. Files added in Phase 0

| File | Purpose |
|---|---|
| `agents/llm_router.py` | 5-provider unified router with cost + latency tracking |
| `db/multi_llm_schema.sql` | Migration: `agent_call_log`, `company_personas`, `resume_builds`, `resume_outcomes`, `ats_test_results`, `interview_outcomes` + 2 views |
| `docs/ARCHITECTURE.md` | This document |
| `docs/G2_RESUME_BUILDER_GRAPH.md` | G2 deep-dive design |
| `tests/test_llm_router.py` | Router unit tests (parse + cost estimation, no live API calls) |

Modified:
- `agents/base_agent.py` — delegates to router; back-compat shims preserved
- `config/settings.py` — new keys + G2 model slots
- `.env.example` — documents new keys
- `requirements.txt` — adds `google-genai`, `langgraph`, `langgraph-checkpoint-postgres`, `langchain-core`

Zero behavior changes for the existing 9 agents — they all use the deprecated shims which now route through the new infrastructure transparently.
