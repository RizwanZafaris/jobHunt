# Gap Closure Roadmap — jobHunt vs. career-ops

**Date:** 2026-05-12
**Status:** Draft (approved direction; implementation pending)
**Owner:** Rizwan
**Source documents:**
- `~/Downloads/Gap_Audit_jobHunt_vs_career_ops.docx` (the audit)
- `~/Downloads/jobHunt_AI_Gap_Closure_Strategy.docx` (the architect's strategy)
- `docs/SYSTEM_AUDIT_2026_05_12.md` (internal audit — 13 PRs shipped against this)
- `docs/AUDIT_REVIEW_EXTERNAL_2026_05_12.md` (external audit — P1/P2/P3 work)

This file supersedes the two `~/Downloads/*.docx` files as the canonical implementation roadmap. The .docx files are reference inputs; this is the build plan.

---

## 0. TL;DR

| Phase | Window | Focus | Cumulative incremental cost |
|---|---|---|---|
| **0** | NOW | Production stability — verify all 7 backlogged PRs land green | $0 |
| **1** | weeks 1-2 | Foundations: `comp_research` + `story_bank` + G6 follow-up | ~$10/mo |
| **2** | weeks 3-4 | G5 evaluation + G3 story enhancement + legitimacy agent | +~$8/mo |
| **3** | month 2 | G7 application assistant (Playwright + HITL) | +~$15/mo |
| **4** | month 2-3 | G8 offer + legitimacy v2 + voice calibration | +~$3/mo |
| **TOTAL** | 8-12 weeks solo · 4-6 weeks with 2 engineers | | ~$36/mo total |

**Strategic frame:** career-ops is a CLI prompt orchestrator. jobHunt is a stateful multi-agent platform. We don't copy career-ops' verbs — we re-implement each gap as a LangGraph workflow that uses our infra advantages (Postgres checkpointer, pgvector RAG, `outcome_to_persona`, multi-LLM router, Redis queue, persona-as-critic). Every output becomes persistent, learnable, and observable.

---

## 1. Infrastructure advantages we already have

| Capability | career-ops | jobHunt | Why it matters |
|---|---|---|---|
| State persistence | File-based, session-bound | Postgres checkpointer, crash-recoverable | Workflows survive restarts |
| Multi-LLM routing | Claude only | 5 providers (Anthropic, OpenAI, Gemini, DeepSeek, Moonshot) | Cost-aware per node |
| Vector RAG | WebSearch only | pgvector with semantic search | Learned company context |
| Outcome attribution | Manual notes | `cite:knowledge_id` + Bayesian credit via `outcome_to_persona` | Auto-learning from results |
| Durable queue | None | Redis + RQ with retry | Background processing |
| Database | Markdown files | 32 tables + 88 RLS policies | Structured queries |
| Graph topology | Linear prompts | 12-node conditional graphs | Complex branching logic |
| Persona-as-critic | N/A | `persona_critic_node` (shipped PR #fix-persona-critic-and-identity-lock) | Banned/required keyword enforcement |
| Identity hardening | N/A | Pure-code header splice + regex fabrication scan | Cannot be ignored by LLM |

---

## 2. Phase 0 — production stability (NOW)

Status of the 13 PRs from the May-12 sprint:

| PR | Branch | Status |
|---|---|---|
| #60 | upsert_rizwan_profile fix | merged |
| #61 | safeguard #2 over-rejection fix | merged |
| #62 | upsert_job + upsert_company user_id fix | merged |
| #63 | create_resume_build + geo filter | merged |
| #64 | PDF/DOCX on-demand render | merged |
| #71 | slowapi rate limiting | merged |
| #72 | adaptive ATS critic + meta-critic trim | merged |
| #73 | apscheduler coalesce/max_instances/listener | merged (broke deploys — see #81) |
| #74 | Anthropic prompt caching | merged |
| #75 | network ilike escape | merged |
| #76 | Pydantic v3 prep | merged |
| #77 | Next.js 15 + React 19 (dashboard) | merged |
| #78 | G2 writer direct persona injection | merged |
| #79 | linkedin_drafts.source_company_name denorm | merged |
| #80 | async `credit_outcome` + `evolve_persona` | merged |
| #81 | `EVENT_JOB_MISFIRE` → `EVENT_JOB_MISSED` hotfix | merged — unblocked 8 failed deploys |

### Outstanding Phase 0 actions

| # | Action | Owner | Status |
|---|---|---|---|
| 0.1 | Verify Railway deploy `34fc2ce0` healthy | done | ✅ HTTP 200 on /health |
| 0.2 | Eyeball Vercel dashboard at `dashboard-eight-theta-t11irr7qdu.vercel.app` | user | in progress |
| 0.3 | Smoke-test build-resume on Visa or another live target | user | in progress |
| 0.4 | Add CI import-check step (`python -c "import main"`) | spawned task | pending |
| 0.5 | Apply migration 012 via `db/migrations/APPLY.sh` (linkedin_drafts denorm) | user | pending |
| 0.6 | Deploy Railway Redis ($10/mo) — currently using in-process fallback | user | pending |
| 0.7 | Add `APOLLO_API_KEY` to Railway env | user | pending |
| 0.8 | Rotate Perplexity key | user | pending |

**Gate to Phase 1:** items 0.2, 0.3, 0.4 complete. 0.5-0.8 can run in parallel.

---

## 3. Phase 1 — foundations (weeks 1-2)

These three builds are the load-bearing pieces for everything in Phases 2-4. Build in this exact order — each depends on the previous.

### 3.1 — `comp_research.py` agent (Phase 1.1)

**Why first:** Cheapest, highest leverage. Powers G5 (evaluation), G7 (application salary fields), G8 (offer negotiation), and G6 (follow-up signal injection) simultaneously. Two weeks of cache builds a personal comp database.

**Design (not a graph — a caching agent):**

```
agents/comp_research.py
├── CompResearchAgent
│   ├── get_comp_band(company, role, level, location) → CompBand
│   │   ├── 1. Check comp_cache (free, 30d TTL)
│   │   ├── 2. If miss → Perplexity Sonar query (~$0.02)
│   │   ├── 3. Parse + normalize (p25/p50/p75/p90)
│   │   └── 4. Cache for 30 days
│   └── suggest_salary_strategy(job_id, user_profile) → SalaryStrategy
│       ├── If band wide → "range" approach
│       └── If band narrow → "anchor" at p75
```

**Files to create:**
- `agents/comp_research.py`
- `db/migrations/2026_05_XX_013_comp_cache.sql`
- `tests/test_comp_research.py`

**Files to modify:**
- `api/workspace.py` — add `/workspace/{job_id}/comp-band` GET endpoint
- `api/perplexity_search.py` — confirm Sonar endpoint exposed for comp queries

**Migration:**
```sql
CREATE TABLE comp_cache (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id),
  company TEXT NOT NULL,
  role TEXT NOT NULL,
  level TEXT,
  location TEXT,
  p25 NUMERIC, p50 NUMERIC, p75 NUMERIC, p90 NUMERIC,
  currency TEXT DEFAULT 'USD',
  source_summary TEXT,  -- Sonar raw answer
  source_citations JSONB,  -- Sonar citations array
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '30 days',
  UNIQUE (user_id, company, role, level, location)
);
CREATE INDEX idx_comp_cache_lookup ON comp_cache (company, role, level, location, expires_at);
```

**Cost:** ~$5/mo at current scale (1 Sonar query per unique (company, role, level, location) tuple per 30 days).

**Effort:** 2 days.

---

### 3.2 — `story_bank` table + G9 extractor (Phase 1.2)

**Why second:** Foundation for G3 (interview prep enhancement) AND G7 (application behavioral answer retrieval). Build the table + auto-extractor once, two downstream consumers benefit.

**Design:**

```
G9 Story Extraction Graph (3 nodes, runs once on cv.md update, ~$0.20)

entry → experience_parser (Sonnet) → star_formatter (Sonnet) → persist

Input:  cv.md / master resume
Output: 10-15 STAR+R stories, each with pgvector embedding,
        stored in story_bank with competency + archetype tags
```

**Files to create:**
- `agents/g9_graph.py`, `agents/g9_nodes.py`, `agents/g9_state.py`, `agents/g9_io.py`
- `agents/story_bank_agent.py` (CRUD + semantic search)
- `db/migrations/2026_05_XX_014_story_bank.sql`
- `tests/test_g9_extractor.py`, `tests/test_story_bank.py`

**Files to modify:**
- `api/profile.py` — trigger G9 on cv.md update
- `api/workspace.py` — add `/workspace/stories` GET (list), `/workspace/stories/search` POST (semantic search)

**Migration:**
```sql
CREATE TABLE story_bank (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id) NOT NULL,
  title TEXT NOT NULL,
  situation TEXT NOT NULL,
  task TEXT NOT NULL,
  action TEXT NOT NULL,
  result TEXT NOT NULL,
  reflection TEXT NOT NULL,         -- the +R (what you learned)
  competencies TEXT[],              -- ['leadership', 'technical_depth', ...]
  archetypes TEXT[],                -- ['PM', 'SA', 'IC', ...]
  quantified_metrics TEXT[],        -- ['$50M TPV', '3x conversion', ...]
  embedding VECTOR(1536),           -- for semantic search
  outcome_score INT DEFAULT 0,      -- Bayesian credit from interview outcomes
  source_cv_hash TEXT,              -- which cv.md version produced this
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_story_bank_user ON story_bank (user_id);
CREATE INDEX idx_story_bank_embedding ON story_bank USING ivfflat (embedding vector_cosine_ops);
ALTER TABLE story_bank ENABLE ROW LEVEL SECURITY;
CREATE POLICY story_bank_user_isolation ON story_bank FOR ALL USING (user_id = auth.uid());
```

**Cost:** ~$0.20 one-time per CV update.

**Effort:** 3 days.

---

### 3.3 — G6 Follow-up Graph (Phase 1.3)

**Why third (highest immediate ROI):** Recovers 30% of currently-lost callbacks. We have ~60 in-flight applications generating zero follow-ups today. This single graph could surface dozens of warm threads.

**Design:**

```
G6 Follow-up Graph (6 nodes, daily APScheduler cron at 09:00, ~$0.07/follow-up)

cadence_checker (code)
  ↓
context_builder (code + RAG: company_knowledge last 7 days)
  ↓
draft_generator (Sonnet, $0.05)
  ↓
┌──────────────────┬──────────────────┐
│ tone_calibrator  │ persona_critic   │   ← parallel fan-out
│ (Sonnet, $0.02)  │ (Sonnet, $0.05)  │      (matches G2 pattern)
└──────────────────┴──────────────────┘
                  ↓
       merge_critique (code, no LLM)
                  ↓
       urgency_scorer (code)
                  ↓
       persist + notify (code)
```

**Cadence rules (encoded from career-ops):**

| Status | Follow-up after | Max follow-ups |
|---|---|---|
| Applied | 7 days | 2 |
| Responded | 3 days | 3 |
| Interview scheduled | 1 day pre | 1 (thank-you) |
| Offer received | 2 days | 2 (negotiation) |
| Ghosted 14d+ | AUTO-CLOSE | mark COLD |

**Draft framework (encoded from career-ops):**

```
Sentence 1: Reference role + when applied
Sentence 2: ONE new signal (company news from RAG OR personal win)
Sentence 3: Soft ask + availability
NEVER: "just checking in", "circling back", "touching base"
ALWAYS: Lead with value, not the ask
EVERY signal must carry a cite:knowledge_id for outcome attribution
```

**Files to create:**
- `agents/g6_graph.py`, `agents/g6_nodes.py`, `agents/g6_state.py`, `agents/g6_io.py`
- `db/migrations/2026_05_XX_015_follow_up_cadence.sql`
- `tests/test_g6_cadence.py`

**Files to modify:**
- `main.py` — add daily G6 APScheduler job at 09:00 (uses the `EVENT_JOB_MISSED` listener we just shipped)
- `api/workspace.py` — add `/workspace/follow-ups` GET (queue), `/workspace/follow-ups/{id}/send` POST (approve + send), `/workspace/follow-ups/{id}/skip` POST (mark cold)
- `api/actions.py` — `_build_today_actions()` adds URGENT/OVERDUE follow-ups to /today queue

**Migration:**
```sql
CREATE TABLE follow_up_cadence (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES profiles(id) NOT NULL,
  application_id BIGINT REFERENCES applications(id) NOT NULL,
  current_status TEXT NOT NULL,  -- applied | responded | interview_scheduled | offer_received | rejected
  follow_up_count INT DEFAULT 0,
  last_contact_date TIMESTAMPTZ,
  next_follow_up_date TIMESTAMPTZ,
  urgency TEXT,                  -- URGENT | OVERDUE | waiting | COLD
  draft_email TEXT,
  draft_cites JSONB,             -- array of cite:knowledge_id breadcrumbs
  draft_persona_critique JSONB,  -- {banned: [], required_covered: [], voice_match: 0.x}
  outcome TEXT,                  -- response | no_response | callback | (null)
  outcome_recorded_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_follow_up_user_urgency ON follow_up_cadence (user_id, urgency, next_follow_up_date);
CREATE INDEX idx_follow_up_application ON follow_up_cadence (application_id);
ALTER TABLE follow_up_cadence ENABLE ROW LEVEL SECURITY;
CREATE POLICY follow_up_user_isolation ON follow_up_cadence FOR ALL USING (user_id = auth.uid());
```

**Cost:** ~$2/mo at 50 active applications.

**Effort:** 4 days.

---

## 4. Phase 2 — high-leverage additions (weeks 3-4)

### 4.1 — G5 Evaluation Scoring (scoring agent, NOT a new graph)

**Why scoring agent vs new graph:** We already have `fit_score` on `jobs`. Don't fork — extend.

**Design:**

```
agents/scoring_agent.py — extends existing fit_score logic

Inputs:  job + master_cv + company_persona + comp_research result
Outputs: jobs.fit_score_breakdown JSONB = {
  role_fit: 0.0-1.0,
  growth: 0.0-1.0,
  comp: 0.0-1.0 (uses comp_research from Phase 1.1),
  culture: 0.0-1.0 (uses company_persona from existing system),
  remote: 0.0-1.0,
  trajectory: 0.0-1.0,
  composite: weighted_avg,
  letter_grade: A|B|C|D|F
}
```

**Files to create:**
- `agents/scoring_agent.py`
- `tests/test_scoring_agent.py`

**Files to modify:**
- `db/migrations/2026_05_XX_016_jobs_fit_score_breakdown.sql` (add JSONB column + letter_grade column)
- `agents/jobs_scout.py` — call scoring_agent after job ingestion
- `dashboard/src/app/targets/page.tsx` — add A-F filter + sort

**Cost:** ~$0.15/role × 50 new jobs/week ≈ $30/mo. Cacheable (only re-score when job changes or persona evolves).

**Effort:** 3 days.

---

### 4.2 — G3 Story Bank Integration

**Why now:** `story_bank` (Phase 1.2) exists; wire it into G3.

**Design:**

```
G3 Enhanced (add 2 nodes to existing 7-node graph):

[existing G3 nodes 1-3]
  ↓
[NEW: story_retriever (code + pgvector, no LLM cost)]
  ↓ Embeds likely behavioral questions, finds top-3 STAR matches
[existing nodes 4-5]
  ↓
[NEW: gap_analyzer (Sonnet, $0.05)]
  ↓ Compares required competencies vs available stories,
    suggests how to reframe existing experience
[existing nodes 6-7]
```

**Files to modify:**
- `agents/g3_graph.py`, `agents/g3_nodes.py` — add 2 nodes
- `tests/test_g3_integration.py`

**Cost:** ~$0.05/interview prep. Marginal.

**Effort:** 2 days.

---

### 4.3 — Legitimacy Agent (Phase 1: signals 1, 2, 3, 5 only)

**Why partial:** Signals 4 (hiring velocity) and 6 (response rate) need months of data to be meaningful. Ship the static-weight version now, learned weights in Phase 4.

**Design:**

```
agents/legitimacy_agent.py — 4-signal scoring (no LLM for signals 1-3)

score_legitimacy(job_id) → LegitimacyScore:
  signal_1: posting_age      (code, free)       — weight 0.3
  signal_2: apify_verified   (code, free)       — weight 0.2
  signal_3: repost_pattern   (SQL, free)        — weight 0.3
  signal_5: recent_news      (Perplexity, $0.005) — weight 0.2
  composite → tier: high | caution | suspicious
```

**Files to create:**
- `agents/legitimacy_agent.py`
- `tests/test_legitimacy.py`

**Files to modify:**
- `agents/jobs_scout.py` — call after dedup
- `db/migrations/2026_05_XX_017_jobs_legitimacy.sql` (add columns: `legitimacy_score`, `legitimacy_tier`, `legitimacy_signals` JSONB)

**Cost:** ~$1/mo (50 new jobs/week × $0.005).

**Effort:** 2 days.

---

## 5. Phase 3 — the big build (month 2)

### 5.1 — G7 Application Graph (Playwright + HITL)

**Why month 2:** Largest build. Needs `story_bank` (Phase 1.2) and `comp_research` (Phase 1.1) as prerequisites. Playwright in Docker is finicky (the existing build already downloads Chromium 170MB + headless 112MB).

**Scope decision:** Greenhouse-only for v1, expand to Lever/Ashby/Workday in Phase 4. ~70% of our target companies use Greenhouse.

**Design:**

```
G7 Application Graph (7 nodes, ~$0.30/form, HITL required):

form_scanner (Playwright, code)
  ↓
question_classifier (Sonnet, $0.05)
  ↓
answer_retriever (code + RAG)
  ├── behavioral → search story_bank (pgvector)
  ├── why-company → search company_knowledge
  ├── salary → comp_research.suggest_salary_strategy()
  └── basic info → profile.yml
  ↓
answer_generator (Sonnet, $0.15) + persona_critic_node (parallel, $0.05)
  ↓
human_review (HITL CHECKPOINT — graph pauses)
  ↓ (user approves in dashboard)
form_filler (Playwright)
  ↓ (stops BEFORE final submit)
persist + auto-enroll in G6 follow-up cadence
```

**HITL is mandatory.** Graph literally pauses; dashboard surfaces all proposed answers; user clicks approve/edit/regenerate/abort per field.

**Files to create:**
- `agents/g7_graph.py`, `agents/g7_nodes.py`, `agents/g7_state.py`, `agents/g7_io.py`
- `agents/form_scanner.py` (Playwright wrapper)
- `agents/ats_patterns/greenhouse.yml`
- `db/migrations/2026_05_XX_018_application_answers.sql`
- `tests/test_g7_greenhouse.py`
- `dashboard/src/app/applications/[id]/assist/page.tsx` (HITL review UI)

**Files to modify:**
- `api/workspace.py` — add `/workspace/{job_id}/apply` POST (start), `/workspace/{job_id}/apply/{id}/approve` POST (resume after HITL)
- `Dockerfile` — ensure Playwright deps stay installed
- `requirements.txt` — pin Playwright version

**Cost:** ~$15/mo at 50 applications/month.

**Effort:** 5 days.

---

## 6. Phase 4 — protection layer (month 2-3)

### 6.1 — G8 Offer Evaluation Graph

```
G8 Offer Graph (5 nodes, ~$0.40/offer, on-demand only):

offer_parser (code)
  ↓
market_analyzer (Perplexity Sonar + Gemini, $0.05)
  ↓
negotiation_strategist (Opus, $0.20)
  ↓
risk_detector (DeepSeek-R1, $0.10)  ← long-context reasoning
  ↓
synthesizer (Opus, $0.15) → ACCEPT / NEGOTIATE / DECLINE
```

**Files to create:**
- `agents/g8_graph.py`, `agents/g8_nodes.py`, `agents/g8_state.py`, `agents/g8_io.py`
- `db/migrations/2026_05_XX_019_offer_evaluations.sql`

**Effort:** 4 days. Build when first offer arrives.

---

### 6.2 — Legitimacy Agent v2 (learned weights)

Gate behind `applications.count >= 50`. Replace static weights with weights learned from `applications.outcome`. Add signals 4 (hiring velocity from `company_knowledge` RAG) and 6 (per-company response rate).

**Effort:** 1 day.

---

### 6.3 — Voice Calibration Graph (G11)

**Why low priority:** Persona already encodes voice via `success_patterns`. G11 sharpens it but isn't load-bearing.

```
G11 Voice Calibration (3 nodes, one-time, ~$0.10):

entry → style_extractor (Sonnet) → voice_profiler (Sonnet) → persist

Output: profiles.voice_calibration JSONB injected into every
        writer node prompt across G2 / G6 / G7 / G8.
```

**Files to create:**
- `agents/g11_voice_calibrator.py`
- `db/migrations/2026_05_XX_020_profiles_voice_calibration.sql` (add JSONB column)

**Effort:** 2 days.

---

### 6.4 — Proof Point Agent

Curated achievement library with semantic search. Auto-embed articles, launches, metrics for interview reference.

**Files to create:**
- `agents/proof_point_agent.py`
- `db/migrations/2026_05_XX_021_proof_points.sql`

**Effort:** 2 days.

---

## 7. Things we are NOT building (and why)

| career-ops feature | Decision | Reason |
|---|---|---|
| LaTeX PDF (tectonic/pdflatex) | Skip | We have `reportlab` + planned `style_critic` node — same result, simpler ops |
| Canva MCP integration | Skip | Niche (design roles only); low ROI |
| Auto-pipeline mode | **Skip permanently** | HITL is the feature, not the bug. We never auto-submit |
| CLI wrapper (`jobhunt-cli`) | Skip | Dashboard *is* our power-user tool; CLI is a different abstraction |
| Single-file `applications.md` source of truth | Skip | 32-table DB is superior; export-to-markdown for git backup is enough |
| TSV batch imports | Skip | API bulk endpoints already do this |

---

## 8. New database migrations (proposed numbers)

| # | File | Purpose |
|---|---|---|
| 013 | `2026_05_XX_013_comp_cache.sql` | Phase 1.1 — comp band caching |
| 014 | `2026_05_XX_014_story_bank.sql` | Phase 1.2 — STAR+R stories + pgvector |
| 015 | `2026_05_XX_015_follow_up_cadence.sql` | Phase 1.3 — G6 state machine |
| 016 | `2026_05_XX_016_jobs_fit_score_breakdown.sql` | Phase 2.1 — A-F scoring breakdown |
| 017 | `2026_05_XX_017_jobs_legitimacy.sql` | Phase 2.3 — ghost detection signals |
| 018 | `2026_05_XX_018_application_answers.sql` | Phase 3 — G7 form answer history |
| 019 | `2026_05_XX_019_offer_evaluations.sql` | Phase 4.1 — G8 offer eval persistence |
| 020 | `2026_05_XX_020_profiles_voice_calibration.sql` | Phase 4.3 — voice JSONB column |
| 021 | `2026_05_XX_021_proof_points.sql` | Phase 4.4 — achievement library |

Number 012 is already taken by linkedin_drafts denorm (pending `APPLY.sh` run by user).

---

## 9. New graphs to build

| Graph | Nodes | Purpose | Phase | Est. cost |
|---|---|---|---|---|
| G6 | 6 | Follow-up cadence | 1.3 | ~$0.07/follow-up |
| G9 | 3 | Story bank extraction | 1.2 | ~$0.20 one-time |
| G7 | 7 | Application assistant | 3 | ~$0.30/form |
| G8 | 5 | Offer evaluation | 4.1 | ~$0.40/offer |
| G11 | 3 | Voice calibration | 4.3 | ~$0.10 one-time |

G5 is NOT a graph — it's a scoring agent extending existing fit_score.

---

## 10. New agents to build (non-graph)

| Agent | Purpose | Phase |
|---|---|---|
| `comp_research.py` | Comp band caching + salary strategy | 1.1 |
| `story_bank_agent.py` | CRUD + semantic search over story_bank | 1.2 |
| `scoring_agent.py` | A-F dimensional scoring | 2.1 |
| `legitimacy_agent.py` | Ghost posting detection | 2.3 |
| `proof_point_agent.py` | Achievement library | 4.4 |

---

## 11. Engineering principles (carried forward from this session's PRs)

Every new graph/agent MUST follow these patterns:

1. **Persona-as-critic, not just persona-as-input.** If the agent produces user-visible content, add a `persona_critic_node` parallel sibling that checks banned/required keywords + success/failure patterns. Reference: PR `fix/persona-critic-and-identity-lock`.

2. **cite:knowledge_id breadcrumbs on every claim.** Without cites, `outcome_to_persona` learns from outputs in aggregate; with cites, it learns from specific knowledge_id → outcome attribution. Mandatory for G6 drafts, G7 answers, G8 negotiation scripts.

3. **Identity lock + structural enforcement, not just prompt-level rules.** For any node generating user-visible content: (a) prompt-level fact-integrity directive, (b) pure-code structural enforcement of master-CV facts, (c) regex fabrication scan in polisher. Reference: G2's `_enforce_master_header`.

4. **user_id NOT NULL discipline.** Every new INSERT must populate user_id from `os.environ.get("RIZWAN_USER_ID", "00000000-0000-0000-0000-000000000001")`. Reference: 7 fixes shipped this session for this same archetype.

5. **APScheduler hardening.** Every new cron job inherits `coalesce=True, max_instances=1, misfire_grace_time=300` from `job_defaults`. EVENT_JOB_MISSED listener already wired in main.py.

6. **Geo filter pre-LLM.** Filter by `TARGET_LOCATION_TOKENS` before any LLM call. Reference: PR #63 `_is_target_geo()`.

7. **Cost telemetry on every LLM call.** `agent_call_log` insert must include user_id (Bug #7 fix in PR `fix/redis-fallback-and-telemetry-and-nav`).

8. **Anthropic prompt caching for system prompts ≥1024 tokens.** ~90% input-token discount on cache hits. Reference: PR #74.

9. **HITL for any action that contacts the outside world.** No auto-send emails. No auto-submit forms. No auto-apply. Graph pauses; user approves in dashboard; only then proceed.

---

## 12. Open decisions (need user input)

| # | Decision | Recommendation | Status |
|---|---|---|---|
| 12.1 | Phase 1 build order: comp_research → story_bank → G6, or G6 first? | comp_research first (foundation) | recommended |
| 12.2 | G7 v1 ATS scope: Greenhouse only, or all 4? | Greenhouse only | recommended |
| 12.3 | Apply migrations on-demand (now per Phase) or batch (every 2 weeks)? | On-demand per phase | recommended |
| 12.4 | Should `comp_research` be user-scoped or global cache? | User-scoped (different target levels/geos per user) | recommended |
| 12.5 | G6 cron time: 09:00 IST default, or per-user timezone? | 09:00 in `profiles.timezone` if set, else IST | recommended |

---

## 13. Cost model

| Component | Monthly cost | Notes |
|---|---|---|
| Anthropic (G2 + G3 + G6 + G7 critics) | ~$25-40 | Down from $60-90 after prompt caching (#74) |
| Perplexity Sonar (comp_research + legitimacy + G8 market) | ~$8 | 30-day cache reduces hits 10x |
| Gemini (scoring + culture analysis) | ~$3 | Cheap tier for high-volume scoring |
| DeepSeek-R1 (G8 risk detector) | ~$1 | Low frequency |
| OpenAI (fallback only) | ~$1 | Rare |
| **LLM total** | **~$38-53/mo** | |
| Supabase | $25 | Existing |
| Railway (API + worker + Redis) | $20-30 | Worker + Redis still pending |
| Vercel (dashboard) | $0 | Hobby tier |
| **Infrastructure total** | **~$45-55/mo** | |
| **GRAND TOTAL** | **~$83-108/mo** | At 50 applications/month volume |

---

## 14. Success metrics (how we'll know it worked)

| Phase | Metric | Target |
|---|---|---|
| 1.1 | comp_research cache hit rate after 30 days | ≥80% |
| 1.2 | story_bank entries auto-extracted from cv.md | ≥10 |
| 1.3 | URGENT/OVERDUE follow-ups surfaced in /today | ≥5/day |
| 1.3 | Callback rate from follow-ups vs no-follow-up baseline | +25% |
| 2.1 | Applications gated to A/B grades only | ≥70% of applies |
| 2.3 | Ghost postings filtered before applying | ≥30% of jobs flagged |
| 3 | Time per application: from 30-60min → ? | ≤10 min |
| 3 | G7 HITL approval rate (user approves vs edits/regenerates) | ≥60% first-pass |
| 4.1 | Offers evaluated before accept | 100% |

---

## 15. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Playwright in Docker breaks on Railway | Medium | High (blocks G7) | Already installed; test in worktree first |
| comp_research data quality varies by company size | High | Medium | Surface cite citations in UI; flag low-confidence |
| story_bank pgvector embeddings drift after cv.md update | Medium | Low | Hash-gate re-embedding; only re-extract when cv hash changes |
| G6 generates emails that hurt rapport ("just checking in") | Low | High | Hard banned-phrase regex in persona_critic |
| HITL fatigue → user auto-approves G7 answers | Medium | Medium | Dashboard requires explicit per-field action, no "approve all" |
| Outcome attribution noisy with few data points | High initially | Medium | Static weights for first 50 applications; learned weights gate behind threshold |
| APScheduler thundering herd on Railway restart | Low (fixed in #73) | Medium | `coalesce=True, max_instances=1` shipped; EVENT_JOB_MISSED listener observes |

---

## 16. Change log

| Date | Author | Change |
|---|---|---|
| 2026-05-12 | Claude (Opus 4.7) | Initial draft synthesized from gap_audit.docx + gap_closure.docx + 13 shipped PRs |
| 2026-05-12 | Claude (Opus 4.7) | Added §17 Bug Log after production bug-hunt sprint (stale OKX job + download gate) |

---

## 17. Bug Log (live, append-only)

This is the **canonical bug tracker** for production issues discovered during testing.
Every bug gets logged here with: ID, discovered date, severity, status, root cause,
fix (PR / branch / commit), and financial impact (if money was burned).

**Statuses:** `OPEN` → `FIX_SHIPPED` (PR pushed, awaiting merge) → `MERGED` →
`VERIFIED` (user confirmed fix works in production) → `CLOSED`.

### BUG-001: APScheduler ImportError took down 8 consecutive deploys
- **Discovered:** 2026-05-12 by Claude during dashboard test
- **Severity:** CRITICAL (production deploy pipeline down)
- **Status:** VERIFIED — deploy 34fc2ce0 green, /health 200
- **Component:** `main.py:26`, scheduler bootstrap
- **Symptom:** Every Railway deploy after PR #73 crashed at startup with
  `ImportError: cannot import name 'EVENT_JOB_MISFIRE' from 'apscheduler.events'`.
  Previous good build (3982ec0a) kept serving production traffic, masking the
  failure for ~20 minutes before user noticed.
- **Root cause:** PR #73 used `EVENT_JOB_MISFIRE` (APScheduler 4.x constant) on a
  3.x install (`apscheduler>=3.10.0` in `requirements.txt`). 2-char typo:
  the 3.x equivalent is `EVENT_JOB_MISSED`.
- **Fix:** PR #81 `fix/apscheduler-event-misfired-typo` — 2-char rename in 2 callsites.
- **Financial impact:** $0 (production stayed up on the prior build).
- **Follow-up:** Spawned task to add CI import-check step
  (`python -c "import main"`) so this class of bug cannot recur.

### BUG-002: 1-year-old OKX listing scored 95/100 on /today, resume built against it
- **Discovered:** 2026-05-12 by user during dashboard test
- **Severity:** CRITICAL (direct financial waste; trust signal)
- **Status:** FIX_SHIPPED on `fix/freshness-text-and-build-resume-guardrail`
- **Component:** `agents/job_validation.py::_compute_freshness`, `_has_expiry_phrase`
- **Symptom:** "Senior Product Manager, Payment" at OKX
  (https://www.linkedin.com/jobs/view/.../4024890576) appeared as a top match
  on /today with `match_score=95, confidence_score=75`. LinkedIn's own
  rendered text says "1 year ago" but our system had no idea.
- **Root cause:** LinkedIn renders posting age as plain text in the body
  ("1 year ago Be among the first 25 applicants"), NOT in
  `<meta property="article:published_time">` or in Apify's structured
  `postedAt` field. `_compute_freshness` only looks at structured metadata,
  so it defaulted to "recent" → confidence stayed at 75.
- **Fix (3-layer defense):**
  - `_detect_stale_age_marker(body)` — new regex scanner with LinkedIn-context
    disambiguator (requires "Be among" / "applicant" / "Apply now" within 80
    chars to avoid in-body false positives).
  - `_has_expiry_phrase` — calls the new scanner; a hit closes the posting.
  - `_compute_freshness` — defense-in-depth fallback for re-validation paths.
  - `api/workspace.py::build_resume` — pre-flight 409 if `posting_closed_at`
    or `validation_failed` set. **Wallet protection** for jobs already in DB.
  - SQL backfill: closed 3 known stale rows (OKX 1641, Nium 2714, Wise 7335)
    via Supabase MCP.
- **Financial impact:** ~$1 already burned on the OKX resume build (build UUID
  `936560e1-1cec-449e-a266-e02af233ed50`, 5.2KB markdown, polisher_score 68
  which already reflected the staleness). Future similar incidents prevented.
- **Production data state after fix:**
  - jobs.id=1641 OKX → `posting_closed_at=2026-05-12 01:00 UTC`
  - jobs.id=2714 Nium → `posting_closed_at=2026-05-12 01:00 UTC`
  - jobs.id=7335 Wise → `posting_closed_at=2026-05-12 01:00 UTC`

### BUG-003: PDF/DOCX download buttons disabled on every successful build
- **Discovered:** 2026-05-12 by user during OKX resume test
- **Severity:** HIGH (core functionality unreachable from UI)
- **Status:** FIX_SHIPPED on `fix/dashboard-pdf-docx-download-gate`
- **Component:** `dashboard/src/components/workspace/ResumeTab.tsx:240, 246`
- **Symptom:** After a successful G2 build, only "Markdown" download worked.
  PDF and DOCX buttons rendered greyed-out with tooltip "PDF unavailable
  until next build" — but "next build" would also leave them disabled.
- **Root cause:** Dashboard gated `<DownloadLink enabled={!!localResume.resume_pdf_url}>`.
  Those columns are **always NULL** on every build because G2 hardcodes them at
  `resume_agents/g2_nodes.py:1148` (pandoc/LaTeX not installed in the Railway
  slim Dockerfile). PR #64 added on-demand render to the backend
  (`api/workspace.py:848-908`), but the frontend was never updated to use
  content-based gating.
- **Fix:** Switch gate to `!!((user_edited_md ?? resume_md ?? '').trim())` —
  matches the same fallback the markdown editor uses (line 82 of ResumeTab.tsx).
  Tooltip rewritten to be truthful: "Resume content is empty — rebuild to enable
  download" (only shown when there's actually no content).
- **Verified data:** 5 most recent resume_builds (OKX 1641, Fintech 103,
  Marqeta 3109, Finkraft 89, Adyen 1023) all have `resume_md` populated and
  `resume_pdf_url=NULL` — pre-fix, all 5 had broken PDF/DOCX buttons.
- **Financial impact:** $0 directly, but blocked the user's ability to actually
  USE the resumes they had paid to build → indirect waste.

### Bug archetypes identified (prevent these going forward)

The two bugs above have an underlying common shape worth naming so we don't
repeat them in Phase 1-4 builds:

| Archetype | Description | Prevention |
|---|---|---|
| **Dead-column gate** | Frontend gates UI on a column the backend never sets. Users see permanently-disabled features. | When adding a new UI gate, grep backend writes to that column. If no path writes it (or only legacy paths), gate on content presence instead. |
| **Staleness blindness** | Discovery pipeline only checks structured metadata; misses staleness signals rendered as plain text by the source (LinkedIn "X year ago", Indeed "30+ days ago", etc.). | Layer regex scanning of body text as an additional Safeguard #4 hit. Always close (not just downscore) postings with explicit stale-age markers. |
| **No wallet protection** | Money-burning endpoints (LLM-spending) execute without checking whether the input is even valid (closed posting, failed validation). | Every LLM-spending endpoint needs a pre-flight check on input quality. 409 with explicit `force=true` override gives the user agency. |

### Parallel bug-hunt sprint dispatched 2026-05-12 (in flight)

Three agents running in background:
- **Agent A (UI E2E):** Chrome MCP through every dashboard route, finds visual / interaction bugs.
- **Agent B (Code audit):** Greps for more "dead-column gate" + "staleness blindness" patterns across `api/`, `agents/`, `dashboard/`.
- **Agent C (Data integrity):** Supabase SQL audit across 32 tables for orphans, stale rows, FK violations, user_id NULLs, cost leaks.

Findings will be triaged and logged as BUG-004, BUG-005, … in this section.
