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

### Parallel bug-hunt sprint completed 2026-05-12

Agents A (UI E2E — Chrome MCP), B (static code audit), C (Supabase data audit) all
returned. Combined: **35 findings**. BUG-004 through BUG-009 closed in PR
`fix/wallet-protection-and-list-filter` (shipped 2026-05-12). BUG-010 onward
remain open — logged here as the canonical follow-up backlog.

### BUG-004: `POST /jobs/{id}/generate-resume` legacy route bypassed wallet guard
- Severity: CRITICAL (~$5 / call)
- Status: FIX_SHIPPED — `fix/wallet-protection-and-list-filter`
- Fix: route through new `api/_job_guards.py::load_open_job` helper. 409 with structured
  `code=posting_closed | validation_failed` body. `force=true` override preserved.

### BUG-005: `POST /jobs/{id}/prep-interview` legacy route bypassed wallet guard
- Severity: CRITICAL (~$0.50 / call)
- Status: FIX_SHIPPED — same helper, cost_label="G3 interview prep (~$0.50)".

### BUG-006: `resume_agents/g2_io.py::load_job` had no staleness guard (G2 graph entrypoint)
- Severity: HIGH (defense-in-depth — direct G2 invocation could burn $1-5)
- Status: FIX_SHIPPED — inline check, raises `RuntimeError("Job N is stale ...")` unless `force=True`.

### BUG-007: `GET /jobs` canonical list surfaced stale rows
- Severity: HIGH (every downstream dashboard / integration sees stale rows by match_score)
- Status: FIX_SHIPPED — `filter_open_jobs_query()` helper; new `?include_closed=true` debug param.

### BUG-008: Boss-agent daily digest counted + recommended stale jobs
- Severity: HIGH (daily email content)
- Status: FIX_SHIPPED — `filter_open_jobs_query()` applied to top-jobs queries.

### BUG-009: Staleness threshold too loose — 2 "1 month ago" jobs missed
- Severity: HIGH
- Status: PARTIAL — SQL backfill closed jobs 2982 (Thunes) and 5387 (Light Commercial Vehicle).
  Code threshold tighten (months >= 1 instead of months >= 3) deferred to follow-up after
  this freshness PR merges.

### BUG-010: Counts contradict across /today, /applications, /insights
- Severity: HIGH | Status: OPEN
- /today says "1 Ready to apply, 3 Resumes to build" but renders 4 green "Ready" cards.
  /applications "Total: 2" — neither of the 4 /today jobs appear. /insights Conversion
  Funnel says "Resumes built: 8". Three sources of truth disagree.
- Likely tied to BUG-017 (React hydration errors). Audit /today, /applications, /insights
  filter logic; pick one source of truth (API) and force every counter through it.

### BUG-011: Company knowledge News/Funding cards show stale LLM hardcoded text
- Severity: HIGH (same archetype as BUG-002) | Status: OPEN
- /companies/<id> News card literally contains: "As this analysis is based on general
  knowledge without recent web research, specific news from the last 12 months is not
  available." Funding: "As of early 2024, its market capitalization fluctuates...". No
  "last updated" badge.
- Fix: add `last_synthesized_at` Pill to every persona card; if > 30d show "stale"
  badge + "Refresh" CTA; strip "based on general knowledge" disclaimers.

### BUG-012: Rejected Score 52 (below threshold) + duplicate Mark-Applied CTA
- Severity: HIGH | Status: OPEN
- /applications shows Emirates NBD score 52 status=Rejected — but apply threshold is 85.
  Workspace has TWO identical "Mark as applied" CTAs.
- Fix: threshold-violation flag on `applications`; dedupe Mark-Applied CTAs.

### BUG-013: Persona/Insights table polluted with 3+ phantom companies running LLM ops
- Severity: HIGH (direct LLM spend on garbage) | Status: OPEN
- Phantom rows in personas: "SuperApp", "Merchant Acquiring", "Adyen Careers",
  "Job in Dubai,UAE by Finkraft.ai", "68 Vacancies Apr 2026", "Careem (Uber subsidiary)".
  Each runs `persona.deep_research` (Agent C: $3.35 / 146 calls of which unknown fraction
  is for phantoms). /companies has 68 but personas count is 71 → 3 phantoms.
- Fix: company-name validator in persona-bootstrap path (reject "Job in", "Vacancies",
  "Apr 2026", "Careers", parenthetical descriptors). Backfill: identify phantoms and
  delete / flag `is_phantom=true`.

### BUG-014: /insights Costs panel time-window math contradicts itself
- Severity: HIGH (cost dashboard untrusted) | Status: OPEN
- Header "Today $0.00 · 30d $30.20 · TODAY 0 calls" but Recent Calls shows calls
  "13-18m ago today". "AVG / BUILD (0) $0.00" but Cost by Agent shows g2.writer 28 calls
  / 7d. Daily chart x-axis skips 05-11. Footer "Total 1,092 · Last 7d 1,092 · Last 24h 15".
- Fix: audit every time-window query; use same query for header + footer; pick UTC.

### BUG-015: Internal DB column / view / file path names leaked to UI
- Severity: MEDIUM | Status: OPEN
- "emits one to resume_builds.cover_email_md" (Apply tab), "source: v_company_conversion_funnel"
  (Insights), "Written by agents/llm_router.py" (Costs), etc.
- Fix: hide behind `?debug=1` OR replace with capability language.

### BUG-016: /profile/sources classifier dumps JDs / cover letters into "role_specific_resume"
- Severity: HIGH (pollutes ATS keyword scoring → biases G2 prompts) | Status: OPEN
- 174 docs tagged "role_specific_resume" include `Cover letter.pdf`, `Hiring Manager.docx`,
  `Chief Product Officer (CPO) JD.pdf` (an actual JD!), `Roadmap Q3-Q4-2024.pdf`,
  `mastercard_hr_question_bank.docx`, `Doc2.docx` (1.4KB junk), 707-char OCR garbage.
  These feed Keyword Intelligence (26,756 occurrences / 310 keywords) — inflates scores.
- Fix: improve classifier (JDs have "Job Description"/"Responsibilities" headers; covers
  start "Dear"; roadmaps have quarter headings); re-run on 174 tagged rows; add
  "needs review" tier for borderline cases.

### BUG-017: React hydration errors fire on every page (#418 #423 #425)
- Severity: MEDIUM (likely cause of BUG-010 count drift) | Status: OPEN
- Server-rendered HTML doesn't match client first-render. Errors fire on initial render
  and on tab switches.
- Fix: reproduce locally with strict mode; identify divergence (usually `toLocaleDateString`
  without explicit locale, or `Date.now()` in a component body); use
  `suppressHydrationWarning` OR move to `useEffect`.

### BUG-018: "Why you fit" / "Matched skills" right-rail show empty states with no remediation
- Severity: MEDIUM (dead-column-gate archetype) | Status: OPEN
- Fix: gate on actual content presence OR remove cards until data is wired up.

### BUG-019: /linkedin "Scheduled this week 0" contradicts "Scheduled (1)" tab count
- Severity: LOW | Status: OPEN
- Fix: one query, used for both header card + tab badge.

### BUG-020: /network shows synthetic demo-data names with no "demo" disclaimer
- Severity: MEDIUM | Status: OPEN
- "Sarah Lin", "Raj Mehta", "Alice Hwang", etc. — seed fixtures. Header "Top 5 of 5
  target companies" but /targets has 68.
- Fix: add "Demo data — import LinkedIn CSV to replace" Pill when seed fixtures detected.

### BUG-021: /today renders truncated location string as category label
- Severity: MEDIUM | Status: OPEN
- "UK MAY 2026 IN GREATER LONDON ..." shown as category on Vestd card.
- Fix: find the fallback in today-card renderer; replace with constant ("Other") OR hide chip.

### BUG-022: "Open posting" link on stale jobs likely 404s
- Severity: LOW (mostly mooted once BUG-007/008 filter staleness everywhere) | Status: OPEN

### BUG-023: /targets URL silently redirects to /companies; page title generic
- Severity: LOW | Status: OPEN

### BUG-024: `agent_call_log.graph` 100% NULL — cost-by-graph telemetry broken
- Severity: HIGH (operational blindness — $32.40 spend untraceable) | Status: FIXED 2026-05-12
- 1,092 / 1,092 rows had `graph` NULL. Only `agent_name` was populated (which had the
  prefix info — `g2.writer`, `g3.coach`, etc.).
- Fix: `_derive_graph_and_node()` in `agents/llm_router.py` maps agent_name prefixes
  (g1./jobscout/scout, g2., g3., g4./linkedin) to G1-G4 (utility prefixes
  persona./company./boss./profile./debug. resolve to graph=NULL by design) and
  strips the prefix into `node_name`. Both columns now populated at insert time.
  Backfilled all 1,092 rows via MCP. Post-fix: G1=702, G2=153, NULL=237 (utility/
  bare-name agents — semantically correct). node_name NULL=0.

### BUG-025: `jobs.confidence_score` populated for only 20 / 405 rows (95% drift)
- Severity: HIGH (ranking integrity) | Status: FIXED 2026-05-12
- Migration 011 added the column but had no backfill and no writer set a default.
- Fix: `_default_confidence_score(source)` in `db/client.py` seeds a source-keyed
  default in `upsert_job` when caller didn't supply one (ATS=80, LinkedIn=70,
  regional aggregators=60, generic web/perplexity=50). Backfilled all 385 NULL rows
  via MCP using the same map. Post-fix: 0 NULL, min=50, max=85.

### BUG-026: `jobs.discovered_at` is overwritten by re-discovery → corrupts "true age"
- Severity: HIGH | Status: FIXED 2026-05-12
- 3 jobs had `resume_generated_at < discovered_at` (causally impossible).
- Fix: `upsert_job` in `db/client.py` now reads any existing row by URL and strips
  `discovered_at` from the payload before upsert, preserving the first-discovery
  timestamp. Backfilled jobs 89, 103, 3109 to `resume_generated_at - 1 minute`.
  Post-fix: 0 anomalies.

### BUG-027: 2 applications stuck `status=rejected` with `applied_date IS NULL`
- Severity: MEDIUM | Status: FIXED 2026-05-12
- Affected: `6469d8cd-...` (SuperApp), `7ce700f3-...` (Emirates NBD).
- Fix: backfilled `applied_date = created_at::date` for both rows via MCP. Added
  migration `db/migrations/2026_05_12_014_applications_applied_date_check.sql`
  with a CHECK constraint preventing future post-apply rows (applied/interviewing/
  offered/rejected/withdrawn) from having NULL applied_date. Migration NOT yet
  applied — runs via `db/migrations/APPLY.sh`. Post-fix: 0 anomalies.

### BUG-028: All 15 failed resume_builds have `cost_usd_total=0` AND zero `agent_call_log` rows
- Severity: MEDIUM (investigate first — could be correct OR cost leak) | Status: NO-FIX (true zero-progress) 2026-05-12
- All 15 rows confirmed: `iterations=0`, `cost_usd_total=0.0000`, `agent_transcript=[]`
  (empty array), `error IS NULL`, all `finalized_at` identical at
  `2026-05-11 23:34:13.070859+00`. The exception handler in
  `resume_agents/g2_run.py:309-327` writes both `error` and `finalized_at` on graph
  failure — these rows have neither, so they did not fail through that path. The
  identical finalized_at and absence of an `error` string indicate a one-shot bulk
  cleanup of stuck-`running` rows (likely operator-initiated SQL UPDATE after
  process kill/OOM). No LLM calls were made — no cost was incurred — no leak.
  agent_transcript snippet: `[]` (literal empty JSON array on every row).
  No code change required. Recommend adding a dedicated reaper that sets
  `error='reaped: stuck in running >Nm'` for future bulk cleanups so the failure
  mode is self-documenting.

### BUG-029: `boss_audit_log` table has RLS DISABLED (security)
- Severity: HIGH | Status: OPEN — DO NOT AUTO-FIX (would break writes)
- Anon key can read/write 5 rows. Decision needed: enable RLS + service-role policy, OR
  document as intentional debug-only.

### BUG-030: `jobs.report_path` dead column gated artifact card
- **Discovered:** 2026-05-12 by Agent B (static code audit) | Severity: LOW
- **Status:** FIX_SHIPPED on `fix/bug-dead-column-gates`
- **Component:** `db/client.py::_JOBS_COLUMNS` (line 183), `api/server.py:1820`
- **Symptom:** `report_path` listed in the upsert allow-list and the
  `/jobs/{id}/detail` artifacts loop. Grep confirmed zero callers write to
  it. Any value passed by a caller was silently dropped.
- **Fix:** Removed `report_path` from `_JOBS_COLUMNS` and the artifacts loop.
  Column itself NOT dropped (irreversible).
- **Financial impact:** $0.

### BUG-031: `/jobs/{id}/detail` Cover-email card stuck "missing" after every G2 build
- **Discovered:** 2026-05-12 by Agent B | Severity: HIGH (UX trust)
- **Status:** FIX_SHIPPED on `fix/bug-dead-column-gates`
- **Component:** `dashboard/src/app/jobs/[id]/page.tsx:223`, `api/server.py::get_job_detail`
- **Symptom:** Cover-email ArtifactCard renders "Not generated yet" after
  every successful G2 build — even though G2 wrote `cover_email_md` to
  the resume_builds row. Card was gated on `j.email_path` (dead column).
- **Fix:** `/jobs/{id}/detail` now joins `resume_builds` and returns the
  latest converged build's `cover_email_md` as a synthesised inline
  artifact under `artifacts.email_path`. ArtifactCard updated to render
  artifacts with content but no path.
- **Archetype:** Dead-column gate.

### BUG-032: `/jobs/{id}/detail` Interview-prep card stuck "missing" after every G3 build
- **Discovered:** 2026-05-12 by Agent B | Severity: HIGH (UX trust)
- **Status:** FIX_SHIPPED on `fix/bug-dead-column-gates`
- **Component:** `dashboard/src/app/jobs/[id]/page.tsx:224`, `api/server.py::get_job_detail`
- **Symptom:** Same archetype as BUG-031, for the interview pack card.
  Gated on `j.interview_path` (dead column). G3 writes
  `interview_prep.prep_pack_url` / `prep_pack_md`.
- **Fix:** `/jobs/{id}/detail` joins `interview_prep` and exposes either
  the remote URL (when storage upload succeeded) or an inline-rendered
  pack (when upload returned None — graceful degradation path in
  `interview_agents/g3_io.upload_prep_pack`).

### BUG-033: ApplyTab "Cover note ready" gated on dead `applications.cover_email`
- **Discovered:** 2026-05-12 by Agent B | Severity: MEDIUM (UX trust)
- **Status:** FIX_SHIPPED on `fix/bug-dead-column-gates`
- **Component:** `dashboard/src/components/workspace/ApplyTab.tsx:63`
- **Symptom:** Apply tab checklist row "Cover note ready" never auto-checked
  after a successful G2 build. Gate was `!!application?.cover_email`; no
  pipeline writes that column. Detail copy also leaked the internal column
  name `resume_builds.cover_email_md` (also covered by BUG-015).
- **Fix:** Predicate now `!!resume?.cover_email_md || !!application?.cover_email`.
  Workspace bundle exposes `cover_email_md` on the resume artifact (was
  already in the SELECT for resume_builds, just not serialised). Detail
  copy rewritten in capability language.

### BUG-034: InterviewPrepTab `has_pack` false when Supabase Storage upload fails
- **Discovered:** 2026-05-12 by Agent B | Severity: MEDIUM (data loss UX)
- **Status:** FIX_SHIPPED on `fix/bug-dead-column-gates`
- **Component:** `api/workspace.py::_get_interview_prep_summary` (line 215),
  `dashboard/src/components/workspace/InterviewPrepTab.tsx:66`
- **Symptom:** When `interview_agents/g3_io.upload_prep_pack` returns None
  on storage error, `prep_pack_url` stays NULL while `prep_pack_md` holds
  the rendered markdown. Old gate `bool(prep_pack_url) and converged`
  reported no pack and the user re-spent ~$0.50 to rebuild G3.
- **Fix (two layers):**
  1. Backend `has_pack = converged AND (prep_pack_url OR prep_pack_md)`.
     Bundle now serialises `prep_pack_md`.
  2. Frontend: when only `prep_pack_md` is present, render a collapsible
     inline preview under the "Open interview studio" CTA so the user
     can read the pack even without storage.

### BUG-035: Interview-studio Download .md link hidden when storage upload fails
- **Discovered:** 2026-05-12 during BUG-030+ sweep | Severity: LOW | Status: OPEN
- **Component:** `dashboard/src/components/interview-studio/PrepMaterial.tsx:143`
- **Symptom:** Same archetype as BUG-034 — link is gated on
  `prep.prep_pack_url`. When storage upload fails, the rest of the studio
  still renders from `prep_pack_md` but the "Download .md" CTA disappears.
- **Fix (suggested):** offer a client-side Blob URL download built from
  `prep_pack_md` when `prep_pack_url` is null.

### BUG-036: `applications.pdf_url` and `applications.report_url` have zero writers
- **Discovered:** 2026-05-12 during BUG-030+ sweep | Severity: LOW | Status: OPEN
- **Component:** `db/schema.sql:87-88`, `dashboard/src/lib/types/workspace.ts:59-60`
- **Symptom:** Both columns are declared in schema and exposed in the
  WorkspaceApplication TypeScript interface but no code path writes to
  either. No current UI gates on them, but they are future BUG-031/032
  in waiting. Same risk as `applications.cover_email` before BUG-033.
- **Fix (suggested):** either wire a writer (G2 export → applications row)
  or remove from the TS interface so future authors can't accidentally gate
  on them. Column drops still deferred per repo convention.

### BUG-037: G3 story_bank `retrieved_stories` schema not in db/schema.sql migration
- **Discovered:** 2026-05-12 during Tier 2 §4.2 G3 story-bank integration |
  Severity: MEDIUM | **Status: FIX_SHIPPED** (migration 021 applied via MCP + source file landed on `tier2/g3-story-bank-integration` follow-up commit `2aa9bbb`; PR #94 merged)
- **Component:** `interview_agents/g3_io.py::finalize_interview_prep`,
  `db/migrations/2026_05_12_021_interview_prep_g3_tier2_columns.sql` (NEW)
- **Symptom (before fix):** Tier 2 G3 writes three new JSONB columns —
  `retrieved_stories`, `story_gaps`, `persona_critic_drops` — into the
  `interview_prep` table via `finalize_interview_prep`. The writer was
  defensive (omits fields from payload when None), but Tier 2 sets them
  to empty containers, not None, so the UPDATE would fail on first run.
- **Fix shipped:** added migration 021 with `ALTER TABLE interview_prep
  ADD COLUMN ... JSONB DEFAULT '{}'/'[]'::jsonb` for the three columns.
  Applied live via Supabase MCP 2026-05-12; source file committed to the
  same branch so future `APPLY.sh` runs against fresh clones / staging
  re-create the same shape.

<<<<<<< HEAD
### BUG-040: G7 cover-letter generation does not yet cite proof_points
- **Discovered:** 2026-05-12 during Tier 4 §6.4 proof-point agent build |
  Severity: LOW (forward-looking — G7 isn't merged yet) | Status: OPEN
- **Component:** `agents/proof_point_agent.py`, future
  `resume_agents/g7_*.py` (not yet shipped)
- **Symptom:** Tier 4 ships the proof_points table, search RPC, agent
  module, REST API, extractor, LinkedIn-post auto-seeding, and G3
  story_retriever_node sidecar — but G7 (cover-letter graph) is still
  on its own branch and does not yet pull `search_proof_points` when
  drafting "why I'm a fit" paragraphs. When G7 lands, the
  `insider_expert`-style node needs to call
  `agents.proof_point_agent.search_proof_points(user_id, jd_topic_text,
  k=5)` and surface the top matches as candidate facts the LLM can
  weave in, with the proof_point.id captured in the agent_transcript so
  outcome_to_persona can credit them.
- **Fix (suggested):** in the follow-up PR after G7 merges, add
  one node that runs alongside the JD-extractor and writes
  `state.proof_point_hits: list[ProofPointMatch.asdict()]`. The
  finaliser embeds the IDs into cite:knowledge_id markers, same shape
  as story_bank.
- **Workaround until G7 lands:** none required — the Tier 4 surface is
  complete from G3 + LinkedIn + extractor sides; G7 integration is a
  separate scope deliberately excluded per Tier 4 spec.

### BUG-037+ onward — reserved for future agent sweeps
=======
### BUG-038: Tier 2 §4.3 Legitimacy agent v1 — ghost-posting detector
- **Discovered:** 2026-05-12 (planned roadmap §4.3, not a regression)
- **Severity:** N/A (feature ship) | **Status:** FIX_SHIPPED on `tier2/legitimacy-agent-v1`
- **Component:** `agents/legitimacy_agent.py` (new), `api/workspace.py::check_legitimacy`,
  `api/actions.py::_build_job_actions`, `agents/job_scout_agent.py::_enqueue_legitimacy_for_new_jobs`,
  `api/queue.py::enqueue_legitimacy_check`, `api/worker.py::worker_run_legitimacy`,
  `db/migrations/2026_05_12_020_jobs_legitimacy_v1.sql`.
- **Why it was needed:** career-ops uses a 6-signal heuristic to flag
  "ghost postings" — jobs that look real but are dead-from-the-start
  (already filled internally, posted for compliance, recruiter bait,
  "always hiring" sourcing pools). Tier 1's freshness gate catches stale
  postings (BUG-002 — OKX 1-year-old listing) but doesn't catch the
  dead-from-the-start ones. The pre-v1 GPT-4.1 narrative classifier set
  `legitimacy_tier ∈ {"High Confidence", "Proceed with Caution", "Suspicious"}`
  inside the scoring pass; it was opaque (no per-signal breakdown), not
  cacheable, and not graded against verifiable web signals.
- **Implementation:** 4 of the 6 career-ops signals with static weights
  (each 0.25, sum 1.0):
  - **Signal 1 — Posting age** (`jobs.discovered_at` vs NOW): linear decay
    1.0 at <7d → 0.0 at 60d. Uses BUG-026's preserved-on-rediscovery
    field. Cost $0.
  - **Signal 2 — URL reachability** (httpx HEAD probe via
    `agents/job_validation.py::_http_head_or_get`): 200/301/302 → 1.0,
    404 → 0.5, 410/500+/timeout → 0.0, other 4xx → 0.3. Cached 24h. Cost $0.
  - **Signal 3 — Repost pattern**: `COUNT(*)` of jobs with same
    `(company, ILIKE title)` in 90d. count=0 → 1.0, 1 → 0.7, 2 → 0.4,
    3+ → 0.1. Cost $0.
  - **Signal 5 — Recent news** (Perplexity Sonar via
    `agents/perplexity_search.py::recency_check`): positive hiring → 1.0,
    neutral → 0.7, layoff/freeze → 0.3, no signal → 0.5. Three-bucket
    keyword classifier on the summary (no second LLM call). Cached 24h.
    Logged to `agent_call_log` with `agent_name='legitimacy.news'`,
    `graph=NULL` (utility). Cost ~$0.005/call.
  - **Deferred to v2** — Signal 4 (company hiring velocity) and Signal 6
    (per-company response rate) need ≥50 application outcomes.
- **Composite & tiers:** weighted sum ∈ [0, 1].
  - ≥ 0.7 → "legitimate" (lowercase — new v1 shape)
  - 0.5 ≤ x < 0.7 → "caution"
  - < 0.5 → "suspicious" (hidden from /today by default)
- **Cost:** ~$1/month at current 50 new jobs/week × $0.005 each. Per-job
  ceiling $0.005 (only signal 5 has marginal cost; signals 1/2/3 are $0).
  Cache hits drop the amortised cron-driven re-score to ~$0.
- **Wallet guard:** the `/workspace/{job_id}/legitimacy-check` route wraps
  with `api/_job_guards.load_open_job` so we never burn Perplexity dollars
  on jobs already marked `posting_closed_at`. `force=true` overrides.
- **Auto-trigger:** `agents/job_scout_agent.py` enqueues a legitimacy
  check on every newly-stored row, skipping rows where
  `legitimacy_signals.scored_at` is within 24h. Cron cadence (~6h) means
  every new job gets scored once; re-runs are cache hits.
- **/today integration:** `_build_job_actions` filters out rows where
  `legitimacy_tier='suspicious'` (case-insensitive — legacy capitalised
  "Suspicious" rows from the GPT-4.1 classifier are also hidden).
  `?include_suspicious=true` surfaces them for debugging.
- **Pre-cutover snapshot (legacy tiers, will be overwritten):**
  - Proceed with Caution: 314 rows
  - High Confidence: 66 rows
  - Suspicious: 20 rows (hidden from /today as of this ship)
- **Tests:** 51 added (`tests/test_legitimacy_agent.py`). All 354 existing+new
  tests green.
- **Financial impact:** $0 (new feature; v1 scoring cron starts at next scout pass).
- **Follow-up:** v2 = add signals 4 + 6 once `applications` outcome data
  hits ≥50. The static weights become a regression problem then —
  career-ops calibrates weights on outcome data; we'll do the same.

### BUG-039+ onward — reserved for future agent sweeps
>>>>>>> origin/main

The Bug Log is append-only. New findings add entries with monotonic IDs. When a bug is
verified fixed in production, update **Status** to `VERIFIED` with a date.
