# 🚀 Rizwan's AI Job Hunt System v2 — Multi-Agent Edition

Built on top of [career-ops](https://github.com/santifer/career-ops) with:
- **Supabase** — persistent vector memory (pgvector) + job tracker database
- **Railway** — backend API deployment (FastAPI)
- **Vercel** — optional frontend dashboard deployment
- **Claude Opus 4** — CompanyAgent, RizwanAgent, InterviewAgent, BossAgent
- **GPT-4.1** — JobScoutAgent (parallel search)
- **Playwright** — portal scanning (Greenhouse, Ashby, Lever, Workday, Bayt, GulfTalent)

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

| Agent | Model | Role | Trigger |
|-------|-------|------|---------|
| JobScoutAgent | GPT-4.1 | Scans portals, scores jobs | Daily 09:00 |
| CompanyAgent | Claude Opus 4 | Deep company expert, reviews resume gaps | Per company, reused forever |
| RizwanAgent | Claude Opus 4 | Represents Rizwan, fills gaps via dialogue | Per application |
| InterviewAgent | Claude Opus 4 | STAR prep, likely questions, negotiation | Per high-score job |
| BossAgent | Claude Opus 4 | Orchestrator, freshness audit, daily digest | Daily 21:00 |

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
python supabase/setup_schema.py   # Creates all tables + pgvector extension
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

### Railway (Backend API)
```bash
railway login
railway init
railway up
```
Set env vars in Railway dashboard. The API runs on port 8000.

### Vercel (Optional Dashboard)
```bash
cd dashboard && vercel deploy
```

## Output Files

| Location | Contents |
|----------|----------|
| `output/resumes/` | Tailored .docx + .pdf per job |
| `output/reports/` | Evaluation reports, email drafts |
| `output/interview_prep/` | STAR story banks, likely questions |
| Supabase `jobs` table | All discovered jobs with scores |
| Supabase `company_knowledge` | Company intelligence (pgvector) |
| Supabase `applications` | Application tracking |
