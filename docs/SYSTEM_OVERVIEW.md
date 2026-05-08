# jobHunt — System Overview (15 phases on `main`)

**Version**: post Phase 1.15 · merged `main` head ≥ `125bdba`
**Last updated**: 2026-05-09
**Audience**: anyone deploying, debugging, or extending this system

This is the canonical "what's been built" document. For deeper specs see
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md), [`docs/G2_RESUME_BUILDER_GRAPH.md`](G2_RESUME_BUILDER_GRAPH.md),
[`docs/PERF.md`](PERF.md), [`docs/LIVE_DB_AUDIT.md`](LIVE_DB_AUDIT.md), and [`docs/SECURITY.md`](SECURITY.md).

---

## 1. Executive summary

A **multi-LLM, multi-graph, autonomous job-hunt platform** for senior PM roles in fintech / payments. Single-user today (Rizwan), designed to evolve into a SaaS for similar candidates.

Three operational pillars:

```
   Discovery (G1)    →    Resume Builder (G2)    →    Conversion (G3)
   ─────────────         ────────────────────         ────────────────
   Daily cron            On-demand per job           State-driven
   100+ ATS portals      12-node LangGraph           (planned, Phase 2)
   GPT-4.1 scoring       5 LLM providers
   stored in Supabase    persona-aware
                         cost-capped, gated
```

**Current state**: G1 in production, G2 implemented + dormant (awaiting `USE_G2_GRAPH=true` flip), G3 designed but not built. Six dashboard pages live on Vercel. Two Railway services (api + scheduler) handle ~5 cron jobs.

---

## 2. What's been built — 15 phases on `main`

| Phase | Title | What it added | PR |
|---|---|---|---|
| **0** | Multi-LLM router foundation | `agents/llm_router.py` (5 providers) · 6 new tables (`agent_call_log`, `company_personas`, `resume_builds`, `resume_outcomes`, `ats_test_results`, `interview_outcomes`) · 2 views · seed for 33 personas | #1 |
| **1** | G2 Resume Builder LangGraph | `resume_agents/g2_*.py` (state, IO, 12 nodes, graph, run) · feature-flagged via `USE_G2_GRAPH` · pipeline + API integration | #2, #3 |
| **1.5** | Outcome-logging UI | `OutcomeLogger.tsx` on `/jobs/[id]` · auto-saving tristate form · closes the learning loop | #3 |
| **1.6** | PersonaSynthesizer weekly cron | `agents/persona_synthesizer.py` · Sun 03:00 GST cron · Gemini 2.5 Pro long-context with Claude fallback | #3 |
| **1.7** | `/personas` dashboard page | quality table · conversion funnel · per-row regenerate · detail page with full system_prompt | #4 |
| **1.8** | `/costs` dashboard page | summary cards · daily-cost stacked area · provider donut · agent table · top builds · recent calls | #5 |
| **1.9** | `agent_call_log` perf hardening | composite indexes · RPC rollup functions · `v_agent_call_health` view · cleanup function · `ProviderHealthBadges` strip | #6 |
| **1.10** | Cost-budget alerts | `agents/cost_alerter.py` · daily threshold check + Sunday digest · Slack/SendGrid/stdout dispatch · 3 API endpoints · 2 cron jobs | #8 |
| **1.11** | Per-build cost cap | `g2_max_cost_usd` setting · orchestrator pre/post-call cap checks · `cost_capped` status · `?max_cost_usd=10` API override | #7 |
| **1.12** | Persona quality gate | `check_persona_quality_gate()` · API returns 400 with structured `code='persona_quality_too_low'` · `GenerateResumeButton` confirm dialog · `?force=true` override | #9 |
| **1.13** | Persona bulk-regenerate | `quality_filter` query param on `POST /personas/synthesize` · "Regenerate Low (5)" button · component supports tier-bulk mode | #11 |
| **1.14** | Cost alert history table | `AlertHistoryTable.tsx` on `/costs` · parses `[cost-alerter:KIND] fired=BOOL channel=CHANNEL` · empty-state handled | #10 |
| **1.15** | env + README completeness | 4 missing env keys added · README "Activating G2" runbook · two-Railway-service deployment note | #12 |
| **1.16** | This doc + validation script | `docs/SYSTEM_OVERVIEW.md` · `scripts/validate_g2.py` one-command G2 validation | (this PR) |

**Tests**: 78 passing, 1 skipped (langgraph optional local install). Repo audit script runs on every commit.

---

## 3. High-level architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│  PRESENTATION  ·  Vercel + Next.js 14                                 │
│  /  /companies  /applications  /personas  /costs  /profile  /jobs/[id]│
│  Server Components for SSR · Client Components for interactivity      │
│  Calls API via /api/proxy/* (server-side X-Secret-Key header)         │
└────────────────────────────┬──────────────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────────────┐
│  API  ·  FastAPI on Railway · 30+ endpoints                           │
│  /pipeline/run · /jobs/{id}/generate-resume · /personas · /costs/*    │
│  /alerts/* · /resumes/outcomes/* · /companies/research                │
│  Triggers graphs as background tasks · auth via X-Secret-Key          │
└────┬─────────────────┬─────────────────┬──────────────────────────────┘
     │                 │                 │
     ▼                 ▼                 ▼
┌─────────┐     ┌──────────────┐   ┌──────────────────┐
│   G1    │     │     G2       │   │    G3            │
│ Disco-  │     │   Resume     │   │  Conversion      │
│ very    │     │   Builder    │   │  (planned —      │
│ daily   │     │ on-demand    │   │   Phase 2)       │
│ cron    │     │ ≥ score 85   │   │                  │
└────┬────┘     └──────┬───────┘   └──────────────────┘
     │                 │
     └─────────┬───────┘
               ▼
┌───────────────────────────────────────────────────────────────────────┐
│  AGENT LAYER  ·  10 agents inheriting BaseAgent                       │
│  JobScout · Company · Rizwan · ResumeBuilder · Interview · Boss       │
│  Networking · SalaryResearch · ApplicationTracker                     │
│  PersonaSynthesizer (1.6) · CostAlerter (1.10)                        │
│  All route through agents/llm_router.py for cost/latency telemetry    │
└────────────────────────────┬──────────────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LLM ROUTER  (agents/llm_router.py)                                   │
│  ┌──────────┬─────────┬──────────┬──────────┬──────────────┐          │
│  │ Anthropic│ OpenAI  │  Google  │ DeepSeek │   Moonshot   │          │
│  │ Claude   │ GPT-4.1 │ Gemini   │  R1+V3   │     K2       │          │
│  │ Opus 4.5 │         │ 2.5 Pro  │          │              │          │
│  └──────────┴─────────┴──────────┴──────────┴──────────────┘          │
│  Cost + latency tracking → agent_call_log on every successful call    │
└────────────────────────────┬──────────────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────────────┐
│  DATA  ·  Supabase (Postgres + pgvector + Storage)                    │
│                                                                       │
│  Discovery        companies · jobs · job_scores                       │
│                   company_knowledge (13 sections, pgvector)           │
│                                                                       │
│  Profile          profile_master · profile_experience                 │
│                   profile_certification · profile_education           │
│                   profile_keyword (310 rows, 11 categories)           │
│                   rizwan_profile (legacy embedding cache)             │
│                                                                       │
│  Multi-LLM        agent_call_log · company_personas (33 seeded)       │
│   (Phase 0)       resume_builds · resume_outcomes                     │
│                   ats_test_results · interview_outcomes               │
│                                                                       │
│  Views (Phase 1.9)  v_daily_llm_cost · v_company_conversion_funnel    │
│                     v_agent_call_health · v_agent_call_log_stats      │
│                                                                       │
│  Storage          resumes/  emails/  interview-packs/                 │
└────────────────────────────┬──────────────────────────────────────────┘
                             ▼
┌───────────────────────────────────────────────────────────────────────┐
│  EXTERNAL                                                             │
│  Serper · Greenhouse · Lever · Ashby · Workday · SmartRecruiters      │
│  Playwright fallback · SendGrid · Slack webhook · LLM provider APIs   │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 4. Multi-LLM router (Phase 0)

**File**: `agents/llm_router.py`
**Purpose**: single async entry point for 5 providers. Lazy client construction. Per-call cost + latency telemetry.

### Provider matrix

| Provider | Default model | What it's used for |
|---|---|---|
| Anthropic | `claude-opus-4-5-20251101` | Voice/judgment-critical: Writer, Polisher, Advocate, Orchestrator, BossAgent, all legacy agents |
| OpenAI | `gpt-4.1` | JobScout batch scoring · `text-embedding-3-small` for pgvector |
| Google | `gemini-2.5-pro` | Long-context + grounding: Insider Expert, Meta-Critic, PersonaSynthesizer, G3 Technical Predictor (planned) |
| DeepSeek | `deepseek-reasoner` (R1), `deepseek-chat` (V3) | ATS Critic A · G3 Mock Critic (planned) · cheap classification |
| Moonshot | `kimi-k2` | ATS Critic B (ensemble second opinion) |

### Key abstractions

- **`LLMResult`** dataclass — uniform shape across providers (`text`, `provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `tool_calls`)
- **`infer_provider(model_str)`** — heuristic provider lookup from model name (e.g. `claude-opus-...` → `anthropic`)
- **`PRICING_PER_1M`** table — best-effort cost estimation; update when providers change rates
- **Default log callback** — every call writes a row to `agent_call_log` (silent on failure to avoid breaking the LLM call)

### Provider concentration

Today (USE_G2_GRAPH=false): ~95% Claude · 5% GPT-4.1 + embeddings.
After G2 activation, expected: ~50% Claude · 25% Gemini · 15% DeepSeek · 5% Kimi · 5% OpenAI.

---

## 5. G2 Resume Builder Graph (Phase 1, the centerpiece)

**Files**: `resume_agents/g2_state.py · g2_io.py · g2_nodes.py · g2_graph.py · g2_run.py`
**Trigger**: `POST /jobs/{id}/generate-resume` (manually from dashboard, score ≥ 85 gated)
**Worst-case cost**: ~$5.66 per build (Phase 1.11 cap)
**Dormant by default**: requires `USE_G2_GRAPH=true` env flag

### 12-node topology

```
            entry: load job + master_resume + company_persona + past 5 transcripts
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
       ┌──────────────┐               ┌──────────────────┐
       │   Insider    │               │     Advocate     │
       │   Expert     │               │   (career-arc    │
       │  Gemini Pro  │               │    framing)      │
       │ + grounding  │               │  Claude Opus     │
       │ + persona    │               │                  │
       └──────┬───────┘               └────────┬─────────┘
              └──────────────┬─────────────────┘
                             ▼
                  ┌──────────────────┐
                  │   Meta-Critic    │ Gemini 2.5 Pro
                  │  past 5 trans-   │ (long-context
                  │  cripts for THIS │ pattern miner)
                  │  company         │
                  └────────┬─────────┘
                           ▼
                  ┌──────────────────┐  ◄────────┐
                  │      Writer      │           │
                  │ Claude Opus 4.5  │           │
                  └────────┬─────────┘           │
                           ▼                     │ loop
            ┌──────────────────────────┐         │ (max 3,
            │  ENSEMBLE ATS Critic     │         │  cost-capped
            │  (parallel)              │         │  in 1.11)
            ├──────────────┬───────────┤         │
            │ DeepSeek-R1  │  Kimi K2  │         │
            │ (reasoning)  │ (2nd op)  │         │
            └──────┬───────┴─────┬─────┘         │
                   └──────┬──────┘               │
                          ▼                      │
                 ┌────────────────┐              │
                 │ merge_critique │ pure code,   │
                 │ strictest      │ no LLM       │
                 │ score, union   │              │
                 │ keywords       │              │
                 └────────┬───────┘              │
                          ▼                      │
                 ┌────────────────┐              │
                 │  Orchestrator  │ ─────────────┘
                 │ Claude Opus 4.5│  converged?
                 │ + cost-cap     │  (Phase 1.11)
                 └────────┬───────┘
                  yes →   ▼
                 ┌────────────────┐
                 │   Polisher     │ Claude Opus 4.5
                 │ final voice +  │
                 │  ATS gate ≥95  │
                 └────────┬───────┘
                          ▼
                 ┌────────────────┐
                 │  cover_email   │ Claude Opus 4.5
                 └────────┬───────┘
                          ▼
                 ┌────────────────┐
                 │ docx_export +  │ pandoc
                 │ supabase upload│ signed URL
                 └────────────────┘
```

### Status hierarchy (Phase 1.11)

`resume_builds.status` takes one of four values:

| Status | When |
|---|---|
| `running` | At entry, before any agent has run |
| `converged` | Polisher ran, ATS score met threshold or orchestrator agreed |
| `exhausted` | Hit max iterations without converging on quality |
| `cost_capped` | Hit `g2_max_cost_usd` mid-build (Phase 1.11) |

Hierarchy in `export_node`: cost_capped beats exhausted beats converged.

### Pre-flight gates (Phases 1.11 + 1.12)

Before G2 fires:

1. **Score gate** — job must have `match_score ≥ 85`
2. **Persona quality gate** — `check_persona_quality_gate(company_name, force=False)`:
   - `force=True` → pass with override note
   - No persona row → cold_start (allowed; Insider Expert uses fallback prompt)
   - `quality < g2_min_persona_quality` → blocked (HTTP 400 with `code='persona_quality_too_low'` + `retry_with_force=true`)
3. **Cost cap** — `g2_max_cost_usd` (default $5) seeded into ResumeState; orchestrator force-converges if exceeded

---

## 6. The learning loop (the moat)

**Files**: `agents/persona_synthesizer.py · OutcomeLogger.tsx · resume_agents/g2_io.py`
**Cron**: Sundays 03:00 GST (`run_persona_synthesis` in `main.py`)

```
   USER LOGS OUTCOME (5-second dashboard form on /jobs/[id])
       │
       ▼
   resume_outcomes + interview_outcomes  ──────┐
       │                                       │
       ▼                                       │
   WEEKLY persona_synthesizer (Sunday cron)   │
   for each company:                          │
     - read 90d outcomes + transcripts        │
     - identify success_patterns               │
     - identify failure_patterns               │
     - regenerate company_personas row         │
       │                                       │
       ▼                                       │
   G2 next build for THIS company              │
   loads richer persona →                      │
   Insider Expert system prompt has            │
   concrete success/failure examples ◄─────────┘
```

### Persona quality tiers (Phase 1.12)

| Tier | unknown_sections | Companies (live as of 2026-05-09) |
|---|---|---|
| `high` | 0 | PayPal · Plaid · Revolut · Square (Block) · Standard Chartered (5) |
| `medium` | 1–2 | Adyen · Mastercard · Stripe · Wise · Tabby · 18 more (23) |
| `low` | 3+ | Visa · Thunes · Wio Bank · Payoneer · "Merchant Acquiring …" (5) |

`unknown_sections` counts how many of the five recruitment-intel sections (`recruitment_process`, `resume_dos_donts`, `ats_signals`, `interview_format`, `hiring_signals`) start with `"Unknown — insufficient data"` at synthesis.

The **default `g2_min_persona_quality=medium`** blocks the 5 low-quality builds without explicit `force=true`.

### What outcomes drive

Once `n_examples_used ≥ 50` for a company, LoRA fine-tuning becomes viable (Phase 6, far future). Until then: retrieval-augmented agent specialization on shared base models.

---

## 7. Cost safety triple-layer (Phases 1.10 + 1.11 + 1.12)

Three independent layers prevent cost runaway:

```
   PRE-FLIGHT          IN-FLIGHT             POST-FLIGHT
   ──────────          ─────────             ───────────
   Persona quality     Cost cap              Daily threshold
   gate (1.12)         per build (1.11)      alert (1.10)
        │                   │                     │
        │                   │                     │
   refuses obvious     forces converge       Slack DM if today's
   low-yield builds    if iterations         cumulative spend
   before any token    blow past $5          exceeds $20 default
   is spent            budget
```

### Layer 1 — Persona quality gate (Phase 1.12)

API: `POST /jobs/{id}/generate-resume` returns HTTP 400 with structured `code='persona_quality_too_low'` if persona quality < `g2_min_persona_quality`. Dashboard shows amber confirm dialog with "Force build anyway" / "Cancel".

Override per-build: `?force=true`.

### Layer 2 — Per-build cost cap (Phase 1.11)

`g2_max_cost_usd=5.0` enforced by orchestrator's two cost checks:

1. Pre-call short-circuit: `cost_so_far ≥ cap` → skip LLM, force converge
2. Post-call check: `cost_so_far + this_call ≥ cap` → force converge

Worst-case bound: `$5 cap + $0.66 polisher/email overshoot = $5.66 per build`.

Override per-build: `?max_cost_usd=10`.

### Layer 3 — Daily cost alert (Phase 1.10)

Cron 22:00 GST: `CostAlerter.check_daily_spend()` reads today's spend from `agent_call_log`, compares to `daily_cost_alert_usd` (default $20). If exceeded, dispatches via:

1. **Slack webhook** (preferred — set `SLACK_WEBHOOK_URL`)
2. **SendGrid email** (fallback)
3. **stdout** (last resort, visible in Railway logs)

Idempotent (boss_audit_log dedup) — won't double-fire same day.

### Layer 3b — Weekly digest

Cron Sunday 09:00 GST: `CostAlerter.send_weekly_digest()` aggregates last 7 days:
- Per-provider cost + error rate (with severity markers ⚠ / 🐢)
- Top 5 most expensive resume_builds
- `cost_capped` count
- Conversion funnel (resumes → responses → interviews → offers)

---

## 8. Dashboard tour (`/dashboard` on Vercel)

| Route | Purpose | Phase |
|---|---|---|
| `/` | Pipeline stats · jobs table · score chart | 0 |
| `/companies` | Target companies list with category filters | (pre-Phase 0) |
| `/companies/[name]` | Deep research intel + recruitment process for one company | (pre-Phase 0) |
| `/applications` | Kanban board (Evaluated → Applied → Interview → Offer) | (pre-Phase 0) |
| `/profile` | Master profile editor (35 competencies, 17 tech, 4 AI solutions, 4 experience entries, 6 certs, 3 edu) | (pre-Phase 0) |
| `/profile/keywords` | 310-keyword bank across 11 categories | (pre-Phase 0) |
| `/profile/recommendations` | 41 AI-generated profile improvements | (pre-Phase 0) |
| `/profile/sources` | 233 parsed source documents | (pre-Phase 0) |
| **`/personas`** | **33 personas with quality tiers + bulk regenerate + conversion funnel** | **1.7 + 1.13** |
| **`/personas/[name]`** | **Full persona detail: ATS keyword bank, success/failure patterns, system prompt, history** | **1.7** |
| **`/costs`** | **LLM telemetry: summary cards, daily area chart, provider donut, agent table, top builds, recent calls, alert history** | **1.8 + 1.9 + 1.14** |
| `/jobs/[id]` | Full job detail + artifacts + **OutcomeLogger** (auto-saving Y/N/? form) | (pre-Phase 0) + **1.5** |

---

## 9. Database (live numbers as of 2026-05-09)

**Project**: `oodvelyzdsncsssqvmyb.supabase.co`

| Table | Rows | Purpose |
|---|---|---|
| `companies` | 140 | 67 marked `is_target=true` |
| `company_knowledge` | 507 | 13-section pgvector intel; 33 companies fully complete |
| `jobs` | 245 | 11 at score ≥ 85; archetype + legitimacy_tier |
| `applications` | 2 | both `status='evaluated'` |
| **`agent_call_log`** | **0** | **awaiting USE_G2_GRAPH=true** |
| **`company_personas`** | **33** | **5 high · 23 medium · 5 low** |
| **`resume_builds`** | **0** | **awaiting first G2 run** |
| **`resume_outcomes`** | **0** | **awaiting outcome logs** |
| **`ats_test_results`** | **0** | **awaiting first G2 run** |
| **`interview_outcomes`** | **0** | **awaiting first interview** |
| `profile_master` | 1 | structured profile (replaces legacy rizwan_profile) |
| `profile_experience` | 4 | Simpaisa CPO → Daraz → TapmadTV → Infinity/Wing Logic |
| `profile_certification` | 6 | PMP, PMI-ACP, CSPO, CSM, etc. |
| `profile_education` | 3 | |
| `profile_keyword` | 310 | across 11 categories |
| `rizwan_profile` | 5 | **legacy** pgvector cache (don't read as canonical) |
| `agent_conversations` | 152 | gap-dialogue history across 35 jobs (cold-start signal for G2 meta-critic) |
| `boss_audit_log` | 4+ | nightly digest + cost-alerter audit |
| `story_bank` | 0 | empty — needed for G3 Stage B |

### Migrations applied to live Supabase

| Version | Name |
|---|---|
| `20260508210734` | `multi_llm_phase_0_schema` (Phase 0 — 6 tables + 2 views) |
| `20260508221243` | `agent_call_log_perf` (Phase 1.9 — composite indexes + RPC functions + 2 views) |

---

## 10. Deployment topology (two Railway services)

```
┌─────────────────────┐    ┌──────────────────────┐
│  Vercel             │    │  Railway              │
│                     │    │                       │
│  Next.js 14         │◄──►│  ┌────────────────┐   │
│  Dashboard          │    │  │ API service    │   │
│  - 7 routes         │    │  │ START_MODE=api │   │
│  - SWR for client   │    │  │ FastAPI :8000  │   │
│  - /api/proxy/*     │    │  └────────────────┘   │
│                     │    │                       │
│                     │    │  ┌────────────────┐   │
│                     │    │  │ Scheduler svc  │   │
│                     │    │  │ START_MODE=    │   │
│                     │    │  │   scheduler    │   │
│                     │    │  │ APScheduler    │   │
│                     │    │  └────────────────┘   │
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
```

### Cron schedule (Scheduler service)

| Time (GST) | Job | Phase |
|---|---|---|
| 09:00 daily | `JobScoutAgent.run()` — discovery | 0 |
| 21:00 daily | `BossAgent.run()` — nightly audit + digest | 0 |
| 22:00 daily | `CostAlerter.check_daily_spend()` | **1.10** |
| Sun 03:00 | `PersonaSynthesizer.run()` | **1.6** |
| Sun 09:00 | `CostAlerter.send_weekly_digest()` | **1.10** |

If only the API service is running, none of these fire — features needing them (persona evolution, cost alerts) silently no-op.

### Environment variables (15+ on Railway)

**Required**:
```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
SUPABASE_URL=https://oodvelyzdsncsssqvmyb.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
SECRET_KEY=...                          (X-Secret-Key for API auth)
```

**Required for G2 activation**:
```
USE_G2_GRAPH=true                       (master switch)
GOOGLE_API_KEY=...                      (Gemini 2.5 Pro)
DEEPSEEK_API_KEY=...                    (R1 ATS Critic A)
KIMI_API_KEY=...                        (K2 ATS Critic B)
```

**Recommended**:
```
SUPABASE_DB_URL=postgresql://...        (langgraph checkpointer, crash recovery)
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
SERPER_API_KEY=...                      (live company news + intel)
SENDGRID_API_KEY=...                    (alert/digest fallback)
```

**Optional knobs (sane defaults)**:
```
G2_MAX_COST_USD=5.0
G2_MIN_PERSONA_QUALITY=medium
DAILY_COST_ALERT_USD=20
WEEKLY_COST_DIGEST=true
```

Per-node model overrides via `G2_INSIDER_EXPERT_MODEL`, `G2_WRITER_MODEL`, etc. — leave unset to use defaults.

---

## 11. Activation runbook (validating G2 once)

**Goal**: flip `USE_G2_GRAPH=true`, run one resume build, verify the dashboards populate.

### Step 1 — Get API keys

| Provider | Where to get key | Purpose |
|---|---|---|
| Google AI Studio | https://aistudio.google.com/apikey | Gemini 2.5 Pro (Insider Expert + Meta-Critic + Synthesizer) |
| DeepSeek Platform | https://platform.deepseek.com/api_keys | R1 (ATS Critic A) |
| Moonshot Platform | https://platform.moonshot.ai/console/api-keys | Kimi K2 (ATS Critic B) |

Optional: Slack incoming webhook at https://api.slack.com/messaging/webhooks for cost alerts.

### Step 2 — Set Railway env vars (both services)

In Railway dashboard, **Settings → Variables** for **both** the API service and Scheduler service:

```
USE_G2_GRAPH=true
GOOGLE_API_KEY=<paste>
DEEPSEEK_API_KEY=<paste>
KIMI_API_KEY=<paste>
SLACK_WEBHOOK_URL=<paste>          # optional
SUPABASE_DB_URL=<from Supabase Dashboard → Connection string → URI>
```

### Step 3 — Redeploy

```bash
railway up --service api
railway up --service scheduler
```

This picks up the new `requirements.txt` entries (`langgraph`, `google-genai`, `langgraph-checkpoint-postgres`, `langchain-core`).

### Step 4 — Run validation script

```bash
# From your local jobHunt clone:
export API_URL=https://your-railway-app.up.railway.app
export SECRET_KEY=<your X-Secret-Key>
python scripts/validate_g2.py
```

The script:
1. Lists all jobs with `score ≥ 85` and a high-quality persona
2. Picks one (recommend Plaid for first run — it's a high-quality persona with active hiring)
3. Triggers `POST /jobs/{id}/generate-resume`
4. Polls `resume_builds` until status changes from `running`
5. Reports final score, cost, iterations, status

Or do it manually via the dashboard:
- Navigate to `/jobs/<id>` for a Plaid job
- Click "🎯 Generate Tailored Resume"
- Watch the page refresh after ~60s

### Step 5 — Verify

- Open `/costs` — should now show real numbers in summary cards, daily chart, provider donut, agent table, top builds
- Open `/personas/Plaid` — `n_examples_used` should still be 0 (synth hasn't run yet) but `last_synthesized_at` should be untouched
- Open `/jobs/<id>` — should show DOCX download link + cover email + outcome logger

### Step 6 — Log an outcome

After submitting the resume to Plaid:
- On `/jobs/<id>`, click "Start Tracking Outcome"
- Fill in fields as they happen over coming weeks (recruiter responded? interview? offer?)

The Sunday-following synthesizer cron (`Sun 03:00 GST`) will read the new outcome and update Plaid's persona to v2.

---

## 12. Honest gaps (what's NOT built)

| Gap | Status | Effort to close |
|---|---|---|
| **Phase 2 — G3 Interview Prep Graph** | designed, not built | starting now (Phase 2 PR) |
| `story_bank` is empty | 0 rows live | needs seeding before G3 ships |
| **G2 has never actually run** | code complete, awaiting `USE_G2_GRAPH=true` | step 1-5 above (operator action) |
| RLS on 8 public tables | disabled (per Phase 0 advisor) | `docs/SECURITY.md` has the safe migration; deferred |
| `function_search_path_mutable` warnings | 4 functions | Phase 1.9 fixed 2; remaining 4 are pre-existing |
| Multi-tenant SaaS | architecturally compatible, not built | separate ~2-month project when revenue justifies |
| LoRA fine-tuning per company | viable when ≥50 outcomes/company | Phase 6, far future |
| Phase 1.10 alert rate-limit beyond cost | currently fires at any cost > threshold | could add error_rate + p95 latency triggers |

---

## 13. Roadmap

### Near-term (next 2–4 weeks)

| | |
|---|---|
| **Phase 2** | G3 Interview Prep Graph — same multi-LLM pattern, triggered when `applications.status` → `interviewing`. 7 nodes (parallel question predictors + STAR matcher + mock-interview-and-self-critique loop). Requires `story_bank` seeded first. |
| | Validation: run G2 once against Plaid + log 1 outcome to populate the learning loop |
| | Apply RLS hardening (`docs/SECURITY.md` migration) |

### Mid-term (1–3 months)

| | |
|---|---|
| **Phase 3** | G3 Stage A: Networking + warm-path discovery (Common Room MCP integration) |
| **Phase 4** | G3 Stage C: Negotiation graph (salary research + counter drafter + risk red team) |
| | Per-resume A/B testing infrastructure (variant builds, outcome attribution) |
| | Auto-submit via career portal where ToS allows |

### Long-term (3–12 months)

| | |
|---|---|
| **SaaS pivot** | Multi-tenant rewrite, billing, tiered model routing (free=DeepSeek/Kimi, paid=Opus ensemble) |
| **LoRA fine-tuning** | Per-top-3-company adapters once ≥50 outcomes available |
| Browser extension | One-click capture from LinkedIn/jobs.greenhouse → Discovery |

---

## 14. Operational metrics to watch after activation

After `USE_G2_GRAPH=true` is flipped, these are the canonical signals:

### Cost (`/costs`)

- **Today's spend** — should track ~$2/build × N builds you trigger
- **Per-provider %** — should approach 50/25/15/5/5 (Claude/Gemini/DeepSeek/Kimi/OpenAI) once a few builds run
- **Top resume_builds by cost** — flag any > $5 (cap edge case) or < $0.50 (failed early)
- **Provider error rates** — should stay < 1% for established providers; investigate if any > 5%
- **Provider p95 latency** — Gemini grounding can be slow (~15-30s); Claude usually ~5-10s

### Quality (`/personas`)

- **Persona quality distribution** — currently 5/23/5 (high/medium/low); should improve as outcomes flow
- **Persona versions** — v1 today; should hit v2+ on companies with ≥3 outcomes after first Sunday cron
- **Conversion funnel** — empty today; should populate ~7 days after first G2 build + outcome log

### Operations

- **Boss audit success rate** — `boss_audit_log` rows daily
- **Cron drift** — APScheduler `misfire_grace_time` is 5–60 minutes; check Railway logs for "missed firing"
- **Cost alerts** — should fire 0× per week under normal traffic; investigate any Slack ping

---

## 15. References

### Code

- `agents/llm_router.py` — Multi-LLM dispatcher
- `agents/cost_alerter.py` — Phase 1.10 alerts
- `agents/persona_synthesizer.py` — Phase 1.6 weekly cron
- `resume_agents/g2_*.py` — G2 Resume Builder Graph (Phase 1)
- `api/server.py` — 30+ FastAPI endpoints
- `pipeline.py` — Workflow v2 orchestrator
- `dashboard/src/app/personas/`, `dashboard/src/app/costs/` — Phase 1.7+/1.8+ pages

### Docs

- `docs/ARCHITECTURE.md` — Canonical architecture (post Phase 0 corrections)
- `docs/G2_RESUME_BUILDER_GRAPH.md` — G2 deep-dive (12 nodes, cost analysis, ship plan, 1.11 cost cap, 1.12 quality gate)
- `docs/PERF.md` — Phase 1.9 perf hardening + alerting + scale-up thresholds
- `docs/LIVE_DB_AUDIT.md` — Live data snapshot (2026-05-09)
- `docs/SECURITY.md` — RLS findings + safe migration steps

### Scripts

- `scripts/validate_g2.py` — One-command G2 validation (Phase 1.16)
- `scripts/run.py` — Cron entry point (`python -m scripts.run discovery|resume|both`)

### Database migrations

- `db/schema.sql` — Original schema (pre-Phase 0)
- `db/multi_llm_schema.sql` — Phase 0 (applied as `multi_llm_phase_0_schema`)
- `db/agent_call_log_perf.sql` — Phase 1.9 (applied as `agent_call_log_perf`)
- `db/seed_company_personas.sql` — Phase 0 seed (33 personas inserted)

---

*Generated 2026-05-09 — system at Phase 1.16, awaiting first G2 validation run.*
