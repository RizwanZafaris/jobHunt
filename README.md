# 🚀 Rizwan's AI Job Hunt System v2 — Multi-Agent Edition

Built on top of [career-ops](https://github.com/santifer/career-ops) with:
- **Supabase** — persistent vector memory (pgvector) + job tracker database
- **Railway** — backend API deployment (FastAPI)
- **Vercel** — optional frontend dashboard deployment
- **Multi-LLM router** — Anthropic (Claude Opus 4.5) · OpenAI (GPT) · Google (Gemini 2.5 Pro) · DeepSeek (R1+V3) · Moonshot (Kimi K2)
- **LangGraph** — G2 Resume Builder graph (12 nodes, ensemble ATS critic)
- **Playwright** — portal scanning (Greenhouse, Ashby, Lever, Workday, Bayt, GulfTalent)

## What's in this codebase (12 phases on main)

| Phase | What | Status |
|---|---|---|
| 0 | Multi-LLM router (5 providers) + agent_call_log + company_personas + resume_builds + outcomes schema | merged |
| 1 | G2 Resume Builder LangGraph (12 nodes, behind `USE_G2_GRAPH` flag) | merged |
| 1.5 | Outcome-logging UI on `/jobs/[id]` (closes the learning loop) | merged |
| 1.6 | PersonaSynthesizer weekly cron (Sun 03:00 GST) | merged |
| 1.7 | `/personas` dashboard page | merged |
| 1.8 | `/costs` dashboard page | merged |
| 1.9 | `agent_call_log` perf hardening + `/costs/health` endpoint | merged |
| 1.10 | Cost-budget alerts (daily threshold + Sunday digest, Slack/SendGrid) | merged |
| 1.11 | Per-build cost cap (`G2_MAX_COST_USD`, default $5) | merged |
| 1.12 | Persona quality gate (refuses low-quality personas without `force=true`) | merged |
| 1.13 | Persona bulk-regenerate by quality tier on `/personas` | merged |
| 1.14 | Cost-alerter audit history table on `/costs` | merged |

Deeper docs: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/G2_RESUME_BUILDER_GRAPH.md`](docs/G2_RESUME_BUILDER_GRAPH.md) · [`docs/PERF.md`](docs/PERF.md) · [`docs/LIVE_DB_AUDIT.md`](docs/LIVE_DB_AUDIT.md) · [`docs/SECURITY.md`](docs/SECURITY.md)

## Activating G2 (the new Resume Builder graph)

The graph is dormant until you flip three things on Railway:

1. **Set the new API keys** (Settings → Variables on Railway):
   ```
   GOOGLE_API_KEY=...        # Gemini 2.5 Pro (insider expert + meta-critic)
   DEEPSEEK_API_KEY=...      # R1 (ATS critic)
   KIMI_API_KEY=...          # K2 (ensemble ATS critic)
   ```

2. **Flip the master switch**:
   ```
   USE_G2_GRAPH=true
   ```

3. **Redeploy** so Railway picks up the new requirements.txt entries (`langgraph`, `google-genai`, `langgraph-checkpoint-postgres`, `langchain-core`):
   ```bash
   railway up
   ```

Optional but recommended:
```
SUPABASE_DB_URL=postgresql://...    # crash-recovery for langgraph checkpointer
SLACK_WEBHOOK_URL=https://hooks...  # cost-alert dispatch (Phase 1.10)
G2_MAX_COST_USD=5.0                 # per-build hard cap (Phase 1.11)
G2_MIN_PERSONA_QUALITY=medium       # quality gate (Phase 1.12)
DAILY_COST_ALERT_USD=20             # alert threshold (Phase 1.10)
```

After activation, trigger your first build via:
- **Dashboard**: any job at score ≥ 85 → "🎯 Generate Tailored Resume" button
- **API**: `POST /jobs/{id}/generate-resume` (with `?force=true` to bypass persona gate, `?max_cost_usd=10` to relax cap)
- **CLI**: `python main.py --now --skip-scout`

Watch the build land in real-time on `/costs` (calls flowing into `agent_call_log`) and inspect the result on `/jobs/[id]` (resume + cover email + outcome logger).

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        BOSS AGENT (nightly)                         │
│   Audits all company agents · freshness check · sends daily digest  │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
     ┌─────────────────┼─────────────────┐
     │                 │                 │
┌────▼────┐    ┌───────▼──────┐   ┌─────▼────────┐
│  JOB    │    │   COMPANY    │   │  INTERVIEW   │
│  SCOUT  │    │   AGENT(s)   │   │    AGENT     │
│ GPT-4.1 │    │ Claude Opus  │   │ Claude Opus  │
│ 9am run │    │ one/company  │   │ on-demand    │
└────┬────┘    └───────┬──────┘   └─────┬────────┘
     │                 │                │
     │         ┌───────▼──────┐         │
     │         │ RIZWAN AGENT │         │
     │         │ Claude Opus  │         │
     └────────►│ fills gaps   │◄────────┘
               │ builds CV    │
               └───────┬──────┘
                       │
         ┌─────────────▼──────────────┐
         │   SUPABASE (pgvector)      │
         │  company_knowledge table   │
         │  jobs table · applications │
         │  rizwan_profile · stories  │
         └────────────────────────────┘
```

## Agents

| Agent | Model (today) | Role | Trigger |
|-------|---------------|------|---------|
| JobScoutAgent | GPT-4.1 | Scans portals, scores jobs | Daily 09:00 |
| CompanyAgent | Claude Opus 4.5 | Deep company expert, reviews resume gaps | Per company, reused forever |
| RizwanAgent | Claude Opus 4.5 | Represents Rizwan, fills gaps via dialogue | Per application |
| InterviewAgent | Claude Opus 4.5 | STAR prep, likely questions, negotiation | Per high-score job |
| BossAgent | Claude Opus 4.5 | Orchestrator, freshness audit, daily digest | Daily 21:00 |
| **PersonaSynthesizer** | Gemini 2.5 Pro (fallback Claude) | Per-company persona refresh from outcomes + transcripts | **Sun 03:00 weekly** |
| **CostAlerter** | (rule-based, no LLM) | Daily spend-threshold check + weekly digest via Slack/SendGrid | **Daily 22:00 + Sun 09:00** |

When `USE_G2_GRAPH=true`, resume builds for jobs scoring ≥ 85 route through a 12-node LangGraph (`resume_agents/g2_*.py`) with ensemble ATS critic (DeepSeek-R1 + Kimi K2) and cost cap. See `docs/G2_RESUME_BUILDER_GRAPH.md`.

## Setup

### 1. Install dependencies
```bash
cd job_hunt_v2
pip install -r requirements.txt
npm install   # for playwright + career-ops scripts
```

### 2. Set API keys in .env
```bash
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, SERPER_API_KEY
```

### 3. Set up Supabase
```bash
python db/setup_schema.py   # Creates all tables + pgvector extension
```

### 4. Configure your profile
```bash
# Edit config/profile.yml with your details
# Or run: python main.py --onboard
```

### 5. Run
```bash
# Full pipeline now
python main.py --now

# Start daily scheduler (09:00 job search + 21:00 boss audit)
python main.py --scheduler

# Target specific company
python main.py --company "Tabby" --now

# Interview prep for a job
python main.py --interview-prep --job-id 42

# Deploy to Railway
railway up
```

## Deployment

### Railway — TWO services

The system uses two Railway services backed by the same code:

1. **API service** (`START_MODE=api`) — serves FastAPI on port 8000 for the dashboard
2. **Scheduler service** (`START_MODE=scheduler`) — runs the cron jobs:
   - Daily 09:00 GST · `JobScoutAgent.run()`
   - Daily 21:00 GST · `BossAgent.run()`
   - Daily 22:00 GST · `CostAlerter.check_daily_spend()` *(Phase 1.10)*
   - Sun 03:00 GST · `PersonaSynthesizer.run()` *(Phase 1.6)*
   - Sun 09:00 GST · `CostAlerter.send_weekly_digest()` *(Phase 1.10)*

Both services need the same env vars. If you only run the API service, the crons never fire — you'd have to trigger them via API endpoints (`/alerts/check`, `/personas/synthesize`) or the CLI (`python main.py --persona-synth`, `--alert-check`, `--weekly-digest`).

```bash
railway login
railway init
railway up --service api
railway up --service scheduler   # separate service, same image, START_MODE=scheduler
```

### Vercel (Dashboard)
```bash
cd dashboard && vercel deploy
```

The dashboard surfaces:
- `/` — pipeline stats + jobs table
- `/companies` — target companies + research intel
- `/applications` — kanban board
- `/personas` — 33 company personas with quality tiers + bulk regenerate *(Phase 1.7 + 1.13)*
- `/costs` — LLM telemetry + per-provider health + alert history *(Phase 1.8 + 1.9 + 1.14)*
- `/profile/*` — master profile, keywords, sources, recommendations
- `/jobs/[id]` — full job detail + outcome logger *(Phase 1.5)*

## Output Files

| Location | Contents |
|----------|----------|
| `output/resumes/` | Tailored .docx + .pdf per job |
| `output/reports/` | Evaluation reports, email drafts |
| `output/interview_prep/` | STAR story banks, likely questions |
| Supabase `jobs` table | All discovered jobs with scores |
| Supabase `company_knowledge` | Company intelligence (pgvector) |
| Supabase `applications` | Application tracking |
