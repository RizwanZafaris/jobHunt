# 360° Audit Synthesis — jobHunt

**Audited:** 2026-05-10
**Audit lens:** 6 expert agents (LLM orchestration, RAG, UI/UX, backend/scale, competitive, vision-gap)
**Today:** self-use
**Tomorrow:** SaaS subscription business — beat Santiago Valdarrama's career-ops, careerflow.ai, sonara.ai, simplify.jobs, finalround.ai

---

## Part 0 — One-page brutal verdict

You have built **a research-grade resume engine bolted onto a B-grade product**.

What is genuinely strong (defend it):
- **5-LLM ensemble + persona deep-research with success/failure patterns** is novel — nobody in the competitive set runs Anthropic + OpenAI + Gemini + DeepSeek + Kimi against the same target with a calibrated critic.
- **68 companies × pgvector knowledge × 8 success/8 failure patterns each** is an asset. Competitors store generic resume templates; you store outcome-tunable, company-specific evidence.
- **G2's 12-node graph with persona gate + critic merge** is the right shape. Most "AI resume" tools are a single GPT-4 call.
- **Phase 2.0 design tokens + dark/light + Storybook** is ahead of careerflow's actual production UI.

What is brutally weak (today's blockers):
- **No evaluation harness anywhere.** You ship LLM/RAG changes blind. Three independent agents (LLM, RAG, backend) flagged this as the #1 risk. You cannot tell if today's resume is better than yesterday's except by reading it.
- **Single-tenant schema.** Three agents flagged. There is no `user_id` or `org_id` column on `jobs`, `applications`, `personas`, `company_knowledge`. You cannot ship SaaS to user #2 without a migration.
- **`BackgroundTasks` dies on Railway redeploy.** Every running G1/G2/G3 build is orphaned. No reaper, no resume. At 100 paying users this is a daily incident.
- **IA built for an engineer, not a user.** 7 nav tabs, no "what should I do right now" surface, no onboarding. A first-time visitor cannot tell what to click.
- **Status/data-model rot.** `applications.status` mixes `Evaluada` (Spanish) with English enums; `personas.success_patterns` was empty until yesterday. Schema drift will break dashboards as you add users.

What is **structurally missing** vs. your stated vision:
- **Referral graph: 5% built.** You have `companies` and `target_companies`. You have **zero** schema for people, edges, employments, or warm-intro path-finding. This is half your stated product.
- **LinkedIn presence engine: 0% built.** No drafts table, no scheduler, no news→post pipeline, no content calendar. Vision-gap agent rated this 0/100.
- **Outcome → retrieval feedback loop: 0% built.** You collect outcomes (interviews, offers, rejections) but they do not influence the next resume's RAG re-ranking. The "persona evolves to v2" cron is a stub.

What beats Santiago + the field — your **defensible wedge**:
> **Outcome-conditioned, peer-network-aware, persona-evolved job hunt.**
> Every resume, every email, every LinkedIn post improves based on
> (a) real interview/offer outcomes from people in your peer graph who actually landed at your targets,
> (b) which knowledge rows correlate with positive outcomes,
> (c) persona evolution that is **measured**, not just renamed v2.

Nobody in the competitive set has all three. Santiago's career-ops is a **template repo**. Careerflow is a Chrome extension + form-filler. Sonara is bulk auto-apply. Simplify is a one-click apply button. FinalRound is interview-only. You are the only one with the data flywheel — but you have not closed the loop yet.

**Bottom line:** Today's system is 65% of a great self-use tool and 30% of a SaaS product. The next 4 weeks must close the SaaS-blocking gaps (P0), the next 6 weeks must ship the differentiator wedge (P1), and only then does competitive pricing make sense.

---

## Part 1 — Cross-cutting themes (where multiple experts converge)

| # | Theme | Agents flagging | Severity | Effort |
|---|-------|-----------------|----------|--------|
| 1 | No evaluation harness (LLM-as-judge, RAG@k, regression set) | LLM, RAG, Backend | **P0** | M |
| 2 | Single-tenant schema blocks SaaS pivot | Backend, Competitive, Vision-gap | **P0** | M |
| 3 | `BackgroundTasks` not durable; orphans on redeploy | Backend, Vision-gap | **P0** | S |
| 4 | IA bloated; no "Today" surface; built for engineer | UI/UX, Vision-gap | **P0** | S |
| 5 | Referral graph not built — half the stated product | Competitive, Vision-gap | **P1** | L |
| 6 | LinkedIn presence engine not built — 0% | Competitive, Vision-gap | **P1** | L |
| 7 | Outcome → RAG feedback loop missing | LLM, RAG, Vision-gap | **P1** | M |
| 8 | Prompt caching not used (Anthropic 2024) — 40-50% COGS | LLM, Vision-gap | **P2** | S |
| 9 | RAG = vector-only; no BM25, no rerank, no HyDE | RAG | **P2** | M |
| 10 | Status enum drift (Spanish/English mixed) | Backend, UI/UX | **P2** | XS |
| 11 | Persona evolution is a renamed v2 — not actually measured | LLM, Vision-gap | **P1** | M |
| 12 | No onboarding / no LinkedIn import / no warm start | UI/UX, Competitive | **P0** | S |

The convergence is meaningful. Three independent reviewers do not flag the **same** problem unless it is real and load-bearing.

---

## Part 2 — Strengths to defend (do not refactor these next)

1. **5-LLM router with cost+latency telemetry** (`agents/llm_router.py`) — keep the abstraction, add prompt caching on top.
2. **G2 12-node graph + critic + persona gate** — keep the graph; tune the prompts and add the eval harness around it.
3. **`company_knowledge` + pgvector + ivfflat + `search_company_knowledge` RPC** — keep the spine; layer hybrid retrieval and rerank around it.
4. **Apify rag-web-browser deep research** — keep; this is the data flywheel input.
5. **Phase 2.0 design tokens + Storybook** — keep; the visual layer is fine. The information architecture is the problem.
6. **68-target × 8 success / 8 failure patterns × ~20 ATS keywords each** — this is your moat. It is what no competitor can copy without a year of work.

---

## Part 3 — P0: SaaS-blocking — must ship before charging anyone

These must land before user #2 is invited. Each is small-to-medium effort and unlocks the rest.

### P0.1 — Multi-tenancy migration (M, ~3 days)
- Add `user_id uuid not null references users(id)` to: `jobs`, `applications`, `personas`, `company_knowledge`, `target_companies`, `outreach`, `outcomes`, `costs`.
- Add Postgres RLS policies (`auth.uid() = user_id`) on every table.
- Migrate existing rows to a single `system_user` so today's data is preserved.
- Add `users` and `orgs` tables (orgs for B2B later — leave the column nullable now).
- Update every FastAPI handler to read `user_id` from JWT and filter.
- **Test:** create user B, confirm they see zero of user A's data even with raw SQL.

### P0.2 — Durable job queue (S, ~2 days)
- Replace `BackgroundTasks` with **Redis + RQ** (simplest) or Celery if you want stages.
- Move G1/G2/G3 invocations to enqueued jobs with an idempotency key = `(user_id, job_id, generation_kind)`.
- Add `jobs_runs` table: `id, kind, payload, status, started_at, finished_at, attempts, last_error`.
- Add a worker process to `railway.toml` (or a second Railway service).
- Add an **orphan reaper cron** every 5 min: any `running` row with `started_at < now() - interval '15 minutes'` → mark `failed`, requeue with backoff.
- **Test:** kick off resume build, redeploy mid-flight, confirm it resumes.

### P0.3 — Eval harness (M, ~4 days)
This is the highest-leverage P0 because it makes every other change measurable.

- **Golden set:** 20 jobs × 3 personas × hand-curated "ideal resume" markdown. Stored in `evals/golden/`.
- **LLM-as-judge:** Claude Opus 4.5 grades each generated resume on 5 axes (ATS keyword coverage, evidence specificity, persona fit, no hallucinations, length discipline). Score 0-10 per axis. Output JSON.
- **RAG eval:** for each of 50 hand-labelled queries, measure recall@5, recall@10, MRR. Compare vector-only vs. vector+BM25+rerank as you ship P2.9.
- **Regression gate in CI:** if mean score on golden set drops > 0.3 vs. last green, fail the build.
- **Dashboard:** `/evals` page with last 30 days of mean scores per axis + per-LLM cost/quality scatter.
- **Test:** intentionally regress a prompt, confirm CI blocks the merge.

### P0.4 — IA collapse: 7 tabs → 5 + "Today" home (S, ~2 days)
Current nav (`dashboard/src/components/layout/AppNav.tsx`): Pipeline, Targets, Applications, Personas, Costs, Boss, Profile.

New nav:
1. **Today** (was Pipeline) — "what should I do right now" surface, see P0.5.
2. **Targets** — the 68 companies, persona quality per company, last news refresh.
3. **Applications** — Kanban: New → Researched → Resume Ready → Applied → Interviewing → Offer/Reject.
4. **Network** — referral graph (P1 placeholder today; ship as "Coming soon" tab with email-capture).
5. **Insights** — merge of Personas + Costs + Evals. Power-user surface.

Move "Boss" to a `/admin` route gated by `is_admin`. Move "Profile" to user menu dropdown.

### P0.5 — "Today" home surface (S, ~2 days)
First view after login must answer **one** question: *what do I do right now?*

A single ranked list of cards, top 5 actions:
- "Marqeta — Group PM Fraud — score 95/100 — resume ready, apply now" → button to copy resume URL + open Marqeta careers.
- "Stripe — score 92/100 — persona fresh, resume not yet built" → button to kick off G2.
- "Mastercard — score 83/100 — below 85 threshold, raise threshold or skip" → muted card.
- "3 stale jobs you applied 7+ days ago — log outcome" → button.
- "Your LinkedIn post for this week" (P1 — once shipped).

Each card has 1-2 buttons max. No tabs, no charts on the home.

### P0.6 — Status enum normalization (XS, ~2 hours)
- Pick one language (English).
- Migration: `update applications set status = 'qualified' where status = 'Evaluada'` etc.
- Add a Postgres enum type and constrain the column.
- Add a CHECK constraint or domain.

### P0.7 — Onboarding + warm start (S, ~3 days)
First-run flow:
1. Sign in (Supabase auth, Google + LinkedIn).
2. **Import resume** — upload PDF/markdown, we parse to JSON profile.
3. **Pick 5-10 target companies** — show your 68 plus a search-add UI.
4. **Persona deep-research kicks off in background** for those 10 companies.
5. Land on `/today` with "We are researching your 10 targets — first resume ready in ~10 minutes."

Without this, every new user sees an empty dashboard. Activation will be < 10%.

### P0.8 — Secrets hygiene + .env audit (XS, ~2 hours)
Backend agent flagged: `.env.example` is checked in (good), but Railway env vars are not version-pinned. Add `scripts/check-prod-config.ts` style verifier per Felo pattern, plus a "config drift" alert if a new var is added in code but not in Railway.

**P0 total effort:** ~16 person-days. Single engineer ships in ~3.5 weeks at full focus, ~5 weeks with ops overhead.

---

## Part 4 — P1: True differentiators (this is what beats Santiago + careerflow)

These are the moves that make the SaaS pitch defensible. Do **not** start P1 until P0 is on `main` and CI is green.

### P1.1 — Referral graph (L, ~10 days)

**Schema additions:**
```sql
create table people (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id),
  full_name text not null,
  linkedin_url text,
  email text,
  source text, -- 'manual' | 'linkedin_import' | 'apify_scrape' | 'inferred'
  notes text,
  created_at timestamptz default now()
);

create table employments (
  id uuid primary key default gen_random_uuid(),
  person_id uuid not null references people(id),
  company_id uuid not null references companies(id),
  role text,
  started_at date,
  ended_at date, -- null = current
  is_current boolean generated always as (ended_at is null) stored
);

create table edges (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  src_person uuid not null references people(id),
  dst_person uuid not null references people(id),
  kind text not null, -- 'colleague' | 'classmate' | 'first_degree' | 'second_degree'
  strength real default 0.5, -- 0-1
  evidence jsonb,
  created_at timestamptz default now()
);
```

**Path-finder (Postgres recursive CTE or NetworkX in worker):**
- Input: `target_company_id`
- Output: ranked list of `(target_employee, path[], total_strength)` from your network.
- 1-hop = warm intro available.
- 2-hop = "ask Sarah to introduce you to Bob who works at Stripe."

**Sources:**
- LinkedIn import (manual CSV today; OAuth scrape with user consent later — there is licensing risk here, design for the manual path first).
- Email contacts (Google OAuth read-only on `gmail.contacts`).
- Inference from job applications (recruiters who emailed you).

**UI surface (`/network` tab):**
- Force graph (you in center, target companies as colored nodes, paths highlighted).
- "Best warm intros" list — top 10 paths to your top targets.
- "Draft intro email" button → G3-style email node with persona of the introducer.

**Why this beats the field:** Careerflow shows you a button to copy-paste a LinkedIn message. Sonara has no networking. Santiago has nothing here. **You will be the only career tool with a peer-graph optimizer.**

### P1.2 — LinkedIn presence engine (L, ~10 days)

**Concept:** every weekday, generate a draft LinkedIn post tailored to one of the user's target industries, anchored to a real news event from `company_knowledge` scrapes. User reviews → edits → schedules.

**Schema:**
```sql
create table linkedin_drafts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users(id),
  source_company_id uuid references companies(id),
  source_knowledge_id uuid references company_knowledge(id),
  hook text not null,
  body text not null,
  cta text,
  hashtags text[],
  status text not null default 'draft', -- 'draft'|'approved'|'scheduled'|'posted'|'rejected'
  scheduled_for timestamptz,
  posted_at timestamptz,
  posted_url text,
  created_at timestamptz default now()
);
```

**Generator pipeline (a new G4 graph):**
1. **Pick angle** — Claude picks: news commentary, contrarian take, build-in-public, lesson learned, industry analysis.
2. **Draft v1** — Opus 4.5 + persona of the user (`profiles` table) + 1-2 anchor knowledge rows.
3. **Critique** — Sonnet flags: humble-brag, AI-generated tells (em-dashes everywhere, "delve", "tapestry"), claims without evidence.
4. **Polish** — Opus rewrites with critique applied.
5. **Format** — short paragraphs, line breaks, no hashtag spam.

**Posting:** start with **draft → user-approves → schedules** via Buffer/Hypefury API or manual copy-paste. Do **not** auto-post in V1; the legal/spam risk is real.

**Why this works:** the LinkedIn algorithm rewards consistency. Recruiters search "PM payments" — if you post 3x/week with insight tied to real Stripe/Marqeta news, you appear in their search. **The scrape data you already have powers this for free.**

**Why this beats the field:** Nobody in career-ops does this. There are LinkedIn ghostwriters ($500/mo). You bundle this for $59/mo with the resume engine + referral graph.

### P1.3 — Outcome-conditioned RAG (M, ~5 days)

**Today:** `search_company_knowledge` ranks by cosine similarity. That is it.

**Tomorrow:**
1. Add `outcome_score` to each `company_knowledge` row (default 0.5).
2. When user logs an interview win/loss/offer for a job, propagate Bayesian credit back to the knowledge rows that were cited in that resume's `g2_run.context`.
   - Win → +0.05 to each cited row
   - Loss → -0.02
3. Re-rank step in `search_company_knowledge`: `score = 0.7 * cosine + 0.3 * (outcome_score - 0.5)`.
4. After 30 outcomes, persona's "what evidence to lead with" updates automatically.

**Persona evolution becomes measurable:**
- v1 persona ATS bank: `["payments fraud", "TC40", "MDES"]` (synthetic)
- v2 persona ATS bank after 12 outcomes: `["TC40", "Reg E disputes", "step-up auth"]` — TC40 stayed because it correlated with callbacks; "payments fraud" dropped because it was generic.

Show this delta in the persona detail page.

**Why this beats the field:** This is the closed loop. No competitor has it. It is also why your data flywheel actually compounds — the more users, the better the retrieval for new users in the same archetype (cohort-shared knowledge with privacy-preserving aggregation).

### P1.4 — Hybrid retrieval + rerank (M, ~4 days)
RAG agent's #2 recommendation. Today retrieval is vector-only.

- Add `pg_trgm` and `tsvector` columns on `company_knowledge.text`.
- BM25 in pgvector via `paradedb`'s `bm25` operator, or do tsvector + ranking on Postgres.
- RRF (reciprocal rank fusion) merge of vector + BM25 results.
- Add Cohere reranker as a final pass for top-50 → top-10 (free tier covers self-use).
- Wire to `search_company_knowledge_v2` RPC; keep v1 for backward compat.
- A/B in eval harness — only ship if it beats v1 on golden set.

### P1.5 — Persona evolution dashboard (S, ~2 days)
Today's "v1 → v2" is a renamed row. Make it real:
- Persona detail page: timeline of versions with diff (added/removed ATS keywords, changed success patterns).
- "Why this version" — link back to the outcomes that triggered the evolution.
- Manual "freeze persona" toggle for users who don't want auto-evolution.

**P1 total effort:** ~31 person-days. Two engineers ship in ~4 weeks; one engineer in ~7 weeks.

---

## Part 5 — P2: Polish + cost reduction (do these continuously, not in a phase)

| # | Item | Effort | Payoff |
|---|------|--------|--------|
| P2.1 | **Anthropic prompt caching** on system prompts + persona prefixes | XS | -40% Anthropic cost |
| P2.2 | Sonnet swap on critic and gate nodes (not generator) | XS | -30% latency, -50% cost on those nodes |
| P2.3 | HNSW migration over ivfflat | S | 2-5× recall at same speed |
| P2.4 | HyDE (hypothetical document embedding) for low-recall queries | S | Better RAG on novel jobs |
| P2.5 | ColBERT late interaction (long-tail) | M | Skip until eval shows need |
| P2.6 | Self-querying retriever (LLM extracts filters before search) | S | Big win on company-specific queries |
| P2.7 | Storybook publish + design-tokens roll-out | M | Faster UI iteration |
| P2.8 | Mobile responsive pass on Targets + Applications | S | Recruiters look on phones |
| P2.9 | Email/Slack outreach templates | S | Closes outreach loop |
| P2.10 | Browser extension (one-click "track this job") | M | Acquisition surface |
| P2.11 | `applications.outcome_event` audit table | S | Data foundation for P1.3 |
| P2.12 | Streaming responses for resume generation | S | Perceived speed |

Run these in 1-day "polish" Fridays continuously. Do **not** stop P0/P1 work to chase these.

---

## Part 6 — Killer-thing positioning

**Tagline (internal):**
> The only AI job hunter that learns from your interviews and your network.

**One-liner (external, founder voice):**
> Most AI resume tools are autocomplete with extra steps. This one tracks what actually got you callbacks at companies you wanted, watches the news for your shortlist, drafts your LinkedIn presence, and finds the warmest intro from your network — in one place.

**Three pillars (use on landing page):**
1. **Outcome-conditioned.** Every resume gets sharper because the system knows which evidence got you the last interview.
2. **Peer-network-aware.** Find the warmest path into your dream company — not "anyone at Stripe", the right person, two hops away.
3. **Persona-evolved.** Your "Group PM — Payments" persona is not a template. It is a living artefact that updates from your real outcomes.

**What you do not say:**
- "AI-powered" (everyone says it)
- "10x your job search" (cliché)
- "Save hours" (Sonara owns this)

---

## Part 7 — COGS at scale

Marqeta 3109 build observed: **$0.97 / resume, 5m 53s.** Use this as the unit.

| Scale | Resumes/mo (avg 5/user) | LLM cost | Embeddings | Apify | Infra (Railway+Supabase+Vercel+Redis) | Total | $/user |
|-------|------------------------|----------|------------|-------|---------------------------------------|-------|--------|
| Self (1) | 20 | $20 | $1 | $5 | $0 (free tiers) | $26 | $26 |
| 100 users | 500 | $485 | $20 | $80 | $250 | $835 | **$8.35** |
| 1,000 users | 5,000 | $4,850 | $200 | $800 | $1,200 | $7,050 | **$7.05** |
| 10,000 users | 50,000 | $48,500 | $2,000 | $8,000 | $6,000 | $64,500 | **$6.45** |

With **prompt caching** (P2.1) shipped: subtract ~40% from LLM. New $/user at 1k = **$4.50**.

**Implication for pricing:**
- $29/mo Pro at 1k users → 84% gross margin (post-caching).
- $59/mo Career → 92%.
- These margins fund the engineering team needed to ship P1.

---

## Part 8 — Pricing (recommended)

Three tiers + a founders deal:

| Tier | Price | Target | What you get |
|------|-------|--------|--------------|
| **Free** | $0 | Activation | 1 resume / mo, 5 targets, no referral graph, no LinkedIn engine, watermarked |
| **Pro** | $29 / mo | Active job hunters | 10 resumes / mo, unlimited targets, referral graph (warm intros — 1-hop only), basic LinkedIn assist (2 drafts/wk), interview prep (G3) |
| **Career** | $59 / mo | Senior IC + manager | Unlimited resumes, full path-finder (2-hop), LinkedIn auto-draft + scheduler (5/wk), outcome-conditioned RAG, persona evolution dashboard, priority queue |
| **Lifetime** | $249 once | First 500 users | Everything in Career, locked in for life. Founders deal — closes when you hit 500. |

**Why this works:**
- Free tier feeds the data flywheel and is genuinely useful (Sonara has nothing for free).
- Pro is **below** careerflow ($39) and gives you a wedge.
- Career is **above** to capture senior buyers who actually have $200k jobs at stake.
- Lifetime turns early adopters into evangelists and gives you a working capital injection at launch.

**Do not** offer annual discounts in V1. You will misprice.

---

## Part 9 — Two-sprint roadmap (4 weeks, today → end of June)

### Sprint 1 (week 1-2) — SaaS-blocking
**Goal:** ship `main` is multi-tenant, durable, observable.

- **Day 1-3:** P0.1 multi-tenancy migration + RLS.
- **Day 3-5:** P0.2 Redis + RQ queue + orphan reaper.
- **Day 5-7:** P0.3 eval harness (golden set + LLM-as-judge + dashboard skeleton).
- **Day 8:** P0.6 status enum normalization.
- **Day 8-9:** P0.4 IA collapse to 5 tabs.
- **Day 9-10:** P0.5 "Today" home surface.
- **Day 10:** Sprint 1 review — invite user #2.

**Exit gate:** user #2 onboarded end-to-end without engineer intervention. Eval harness on CI. Resume builds survive a redeploy.

### Sprint 2 (week 3-4) — Differentiator foundation
**Goal:** ship the spine of the wedge.

- **Day 11-13:** P0.7 onboarding + LinkedIn-CSV import.
- **Day 13-17:** P1.1 referral graph schema + path-finder + `/network` MVP (1-hop only, manual import).
- **Day 17-20:** P1.3 outcome event collection + outcome_score on knowledge rows + simple re-rank.
- **Day 20-21:** P2.1 prompt caching ship + measure COGS delta.

**Exit gate:** referral graph search returns warm-intro paths for at least 5 of your 68 targets. Outcome events recorded for last 7 days reflect in next resume's RAG ranking. COGS down ≥ 30%.

---

## Part 10 — Six-week roadmap (extends sprint 2 to week 6)

### Week 5 — LinkedIn engine MVP
- P1.2 schema + G4 draft graph + Buffer-API scheduler.
- "1 draft per weekday" for every Career-tier user.
- User-approve → schedule. No auto-post.
- Dashboard: content calendar view.

**Exit gate:** 5 self-use posts go live, ≥ 3 generate measurable post engagement (likes/comments).

### Week 6 — Hybrid retrieval + persona evolution UI
- P1.4 hybrid (vector + BM25 + RRF + Cohere rerank).
- A/B against vector-only on golden set; ship if winning.
- P1.5 persona evolution dashboard with timeline + diff.
- P0.8 secrets hygiene + check-prod-config script.

**Exit gate:** Eval harness shows v2 retrieval > v1 by ≥ 8% on recall@10. Persona evolution dashboard live with at least 3 self-use personas showing real v1→v2 deltas.

---

## Part 11 — Kill list (do **not** build these next)

These were tempting but explicitly out-of-phase:

1. **Auto-apply / mass apply.** Sonara owns this; it has terrible reputation with recruiters and ATS systems. Skip permanently.
2. **AI interview avatar / live AI interviewer.** FinalRound owns this. Wait until P1.1+P1.2 are shipped.
3. **Browser extension.** Worth it for acquisition but not before SaaS gates work.
4. **Mobile native app.** Responsive web is enough until 5k+ users.
5. **Salary negotiation coach.** Adjacent product; do not split focus.
6. **Resume scoring against ATS systems via real submissions.** Legal grey area; high cost.
7. **Public profile / portfolio pages.** Read.cv adjacent; not your wedge.
8. **Custom GPT / OpenAI store integration.** Distribution hack only; build it after PMF.

---

## Part 12 — Risks (what could kill this)

1. **LinkedIn TOS on scraping for the referral graph.** Mitigation: design for manual CSV import first, OAuth-only scraping for user's own connections. Never scrape another user's network.
2. **Auto-posting LinkedIn drafts at scale = LinkedIn account bans.** Mitigation: V1 is **draft + user-approve + manual paste OR Buffer**. Never automate the post call from your servers.
3. **Cost blowout from 5-LLM ensemble at scale.** Mitigation: P2.1 prompt caching + P2.2 Sonnet swaps on non-critical nodes + cost cap per build (already shipped) + tier-gated parallel LLM count (Free = Sonnet only, Pro = 3-LLM, Career = full 5).
4. **Single-engineer bus factor.** Mitigation: documentation discipline (this file + WHAT_WAS_BUILT.md + per-PR ADRs). Hire #2 only after P1.1 lands and there is paying revenue.
5. **Anthropic outage / OpenAI outage.** Already mitigated by the 5-LLM router; verify failover with chaos test in eval harness.
6. **Persona evolution feedback loop poisons retrieval.** Mitigation: cap outcome_score adjustments to ±0.2 from 0.5. Add manual override. Add eval-harness regression check after each evolution cron.
7. **A bad prompt change ships and degrades all users' resumes silently.** Mitigation: P0.3 eval harness on CI is the answer.

---

## Part 13 — How to use this document

1. **Today:** print Part 0 (verdict) and Part 9 (sprint 1). Decide: are you committing to the 4-week SaaS-blocking sprint?
2. **End of week 1:** check Part 9 day-1-3 progress. If you are not done with P0.1, you are behind. Cut scope on P0.4.
3. **End of week 2:** the multi-tenant exit gate. **Do not** start sprint 2 if user #2 cannot onboard.
4. **End of week 4:** the differentiator-foundation gate. **Do not** start week 5 (LinkedIn) if the referral graph does not return warm-intro paths.
5. **Week 6:** publish the landing page with Part 6 positioning. Open the lifetime deal at $249. Pre-sell 50 seats. That is your seed.

This is the audit. The next move is yours.
