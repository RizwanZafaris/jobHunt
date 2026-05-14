# jobHunt — Build Rationale

> **What it is, how it was built, and why every decision was made.**
> Companion to [`README.md`](README.md). The README tells you _what_
> exists; this document tells you _why it exists in this shape_.

Last updated: 2026-05-14

---

## 1. Problem framing

### 1.1 The job market in 2026

Hiring is broken in the same way SEO was broken in 2010:

- 70%+ of ATS-screened resumes never reach a human.
- LinkedIn job posts decay in ~72 hours; >40% of senior listings on the
  feed at any moment are "ghost postings" with no active hiring intent.
- Generic AI resume tools (Teal, Rezi, Kickresume) write _what the JD says_
  — they cannot tell you _why_ the previous candidate at that company won
  the callback. They have no outcome data.
- Referral discovery on LinkedIn is a manual scavenger hunt. The platform
  hides 2nd-degree connections behind premium walls.
- After the rejection, the same generic tool generates the same generic
  resume for the next role. No learning loop.

### 1.2 What we wanted instead

A system that gets _better at landing this user's specific outcomes_ every
week, by learning from interview wins, rejections, and silent rejections.
Three product wedges that no competitor combines:

1. **Outcome-conditioned RAG** — the resume cites specific knowledge rows;
   when an interview lands or fails, credit propagates back to those rows.
2. **Peer-network referral graph** — your LinkedIn CSV becomes a Dijkstra
   path-finder; warm intros surface before cold applications.
3. **Three-layer enrichment** — Apify (depth) + Perplexity (recency) +
   Apollo (firmographic) feed the same per-company persona with
   attribution tags so credit assignment knows _which signal mattered_.

The optimisation target is **interview-stage outcomes per application**,
not pretty resumes.

---

## 2. The build, in five layers

### 2.1 Capture layer — get every signal a human would notice

| Component | Why it exists | What it captures |
|---|---|---|
| **JobScoutAgent** | LinkedIn / Indeed / portal scrapes go stale in hours. A daily 09:00 sweep keeps `jobs` fresh. | Title, company, JD, posted_at, url, ATS keywords |
| **Apify deep scraper** | Most career pages render JS or hide content behind interaction. Apify gives us long-form coverage (blogs, Glassdoor, About pages). | Full page text, screenshots, reviews |
| **Perplexity Sonar / Sonar-pro** | Need _last-30-day_ news anchors for LinkedIn posts + strategic posture for personas. Static scrape misses everything from this week. | Citations + URL + summary; we keep the citation ids |
| **Apollo enrichment** | We need org_id, headcount, funding, open-jobs index, and (paid plan) people directory for the referral graph. | Firmographic record + jobs list + people seed |
| **Firecrawl pilot** | A few enterprise career pages (Visa, Marqeta, Adyen) are JS-rendered behind challenges that Apify struggles with. Firecrawl renders + sandboxes per-domain. | Same shape as Apify; budget-capped at $20/mo |
| **LinkedIn CSV import** | LinkedIn's API is locked. The user's downloaded `connections.csv` is the only legal seed for the referral graph. | People + employments + edges (1st-degree) |

Everything that lands here writes to `company_knowledge` with a
`metadata.source` tag (apify | perplexity | apollo | firecrawl | user).
That tag is the audit trail for credit assignment later.

### 2.2 Reasoning layer — turn signals into per-company expertise

The reasoning layer is implemented as **LangGraph graphs**, not chains.
Why LangGraph and not raw LangChain pipelines:

- **HITL interrupts.** G7 (application-form assist) pauses on every
  answer until the user approves. Raw chains require boilerplate state
  to express this; LangGraph has `interrupt()` as a primitive.
- **Durable state.** A G2 build is 12 nodes and ~5 min. If the worker
  crashes mid-build we want to resume from the last completed node,
  not start over. LangGraph's Postgres checkpointer (using the same
  Supabase instance) gives this for free.
- **Multi-node graphs are debuggable.** Each node writes one row to
  `agent_call_log` with input, output, cost, latency. The `/insights?tab=traces`
  dashboard reconstructs the run from those rows.
- **Routing is explicit.** A node returning `{"next": "critic"}` is
  greppable. Chains hide control flow inside the prompt; graphs make
  it code.

The reasoning graphs we shipped:

| Graph | Nodes | Why |
|---|---|---|
| **G1.5 persona deep-research** | 5 | One-shot Apify+Gemini long-context build of `success_patterns` and `failure_patterns` for a target company. Per-company persona is the single most valuable artefact in the system — we use it on every downstream build. |
| **G2 resume builder** | 12 | The headline output. 12 nodes because each step has a clearly different job: ingest_jd, ingest_persona, retrieve_knowledge, story_picker, outline, drafter, critic, polish, ats_check, persona_quality_gate, cost_gate, persist. Splitting into 12 instead of 3-4 lets us mix models — Opus for the writer, DeepSeek for the critic, Kimi for the ATS check — and lets the critic block on persona quality. |
| **G3 interview prep** | 7 | Mirror of G2 but for interview rounds: likely_qs, STAR retrieval, hook bank, red-flag scan, salary band, prep-pack assembler, persist. |
| **G4 LinkedIn engine** | 5 | pick_angle → draft → critique → polish → image_brief → persist. Critic is a separate node so we can switch its model without rewriting the writer. Image_brief is a separate node so we never block draft delivery on it. |
| **G5 fit scoring** | (deterministic + LLM) | 6-dimension scorecard. Five dimensions are deterministic (location, comp band, seniority match, skill overlap, role keyword) so we get cheap A-F grades on every ingest; only the culture-fit dimension uses an LLM call. |
| **G6 follow-up cadence** | 4 | per-application stale-detector → angle picker → drafter → critic. Runs daily 18:00. |
| **G7 application graph** | 6 | form_scanner → classifier → retriever → critic → fill → HITL approve. HITL is non-negotiable: an LLM auto-submitting a Greenhouse form will get the user blacklisted. |
| **G8 offer evaluation** | 5 | offer_parser → market_analyzer → negotiation_strategist → risk_detector → synthesizer. Persona-as-critic gate at the end ensures the recommendation matches the user's stated career goals. |
| **G9 story bank** | 4 | extract_stories → tag → embed → persist. Powers G3's STAR retrieval and G7's per-question cites. |
| **G11 voice calibration** | 2 | extract_voice_profile → inject_into_user_message. Per-user writing-sample is injected into the USER message at runtime so the writer hears the user's voice without retraining. |

### 2.3 Outcome layer — close the loop

This is the part nobody else builds.

- Every G2 build emits `cite:knowledge_id=<uuid>` markers inline in the
  bullet points. The markers survive into the saved resume.
- When the user logs an interview outcome (`/jobs/[id]/outcome`):
  - Pass: `outcome_to_persona` worker reads the cited knowledge_ids and
    adds positive Bayesian credit to each row's `outcome_score`.
  - Fail: same path, negative credit.
- A weekly `PersonaSynthesizer` (Sun 03:00) re-derives the
  `success_patterns` and `failure_patterns` from the top/bottom
  outcome-scored knowledge rows. Personas evolve from real outcomes,
  not vibes.
- `persona_versions` table stores the full history so we can A/B test
  persona quality over time.

Why this matters: a generic LLM resume tool can't tell the difference
between a bullet that landed the user a Visa onsite and a bullet that
got auto-rejected by Marqeta's ATS. We can — and we make next week's
resumes more like the first kind.

### 2.4 Network layer — referrals are 10× better than cold apply

- `people / employments / edges / target_company_employees` schema
  ingests the LinkedIn CSV directly. No third-party crawler that risks
  the user's account.
- `agents/referral_graph.py` runs **Dijkstra with geometric-mean strength
  scoring** over the edges graph. Why geometric mean: a 2-hop path
  through a "strong → weak" edge is worse than 2 medium edges; arithmetic
  mean hides this, geometric mean rewards balanced paths.
- `/network` UI surfaces the top 3 warm-intro paths per target company.
- `intro_email_agent` writes the email _to the introducer, not to the
  target_ — because users repeatedly burn intros by skipping the polite
  ask.

### 2.5 Surface layer — three apps, one truth

- **Next.js 15 App Router on Vercel** — App Router for free SEO-ready
  pages, RSC streaming for the `/today` ranked queue (no client-side
  spinners), and per-route bundle splitting so the workspace doesn't
  download interview-studio code.
- **FastAPI on Railway** — port 8080, 90+ routes. FastAPI for
  Pydantic-validated request/response, OpenAPI for free, and async
  handlers that don't block the worker.
- **APScheduler embedded in the API process** — Railway charges per
  service. Running scheduler as a separate Railway service was 2× the
  bill. We embedded APScheduler in the FastAPI startup (`api/server.py`
  on_startup) so a single container runs the API _and_ the cron jobs.
  Health probe at `/admin/scheduler-status`. (BUG-053 was when the
  separate scheduler service was silently down for 9 days — embedded
  scheduler fixed that class of bug forever.)
- **Redis + RQ for queueing** — long-running graphs (G2, G3) are
  dispatched to RQ workers via `api/queue.py`. Idempotency dedup via
  payload hash so a double-click on "Build resume" doesn't burn $2.

---

## 3. Architecture decisions, in detail

### 3.1 Why multi-LLM, not single-provider

5 providers wired through `core/llm_router.py`:

| Provider | Model | Why this slot |
|---|---|---|
| **Anthropic** | Opus 4.5-20251101 (writer) / Sonnet 4.6 (critic) | Best long-form quality + prompt-caching for the persona block (50%+ cost saving on repeated runs). Default writer everywhere. |
| **OpenAI** | GPT-4.1 | Best at structured JD extraction (`JobScoutAgent`). |
| **Gemini 2.5 Pro** | google.generativeai | 2M-token context window for persona deep-research over scraped pages. Anthropic's 200K wouldn't fit. |
| **DeepSeek-R1** | deepseek-reasoner | Cheap critic for G2/G4. Reasoning model so the critique is structured. |
| **Kimi K2** | moonshot-v1 | ATS-style keyword density check. Cheap + Chinese-trained models are surprisingly good at this narrow task. |
| **Perplexity Sonar / Sonar-pro** | sonar / sonar-pro | The only models with first-class web-grounded citations. We _save the citation URLs_, not just the text. |

Router cost-routes: every call records `tokens × $/M-token × duration_ms`
to `agent_call_log`. The `/costs` dashboard reads from this; cost-alerter
fires Slack at $20/day threshold.

### 3.2 Why Supabase, not a self-hosted Postgres

- **pgvector built in** — `company_knowledge.embedding` (1536-dim) +
  `story_bank.embedding` are both pgvector with cosine HNSW indexes.
- **RLS from day 1** — 32 user-owned tables × 4 RLS policies each
  (`select_own`, `insert_own`, `update_own`, `delete_own`). When we
  flip multi-tenant on (`RIZWAN_SINGLE_USER_MODE=0`), no schema
  changes are needed.
- **Same DB hosts LangGraph checkpointer** — no separate state store
  for graph durability. One connection string, one backup.
- **Migrations via MCP** — 30+ migrations applied non-destructively
  through Supabase MCP `apply_migration`. Each migration is idempotent
  (`CREATE ... IF NOT EXISTS`, `DROP ... IF EXISTS`) and transactional.

### 3.3 Why Pydantic `extra='forbid'`

We had a class of bug (BUG: target_company_id mismatch) where the
dashboard sent `company_id` but the backend's Pydantic model accepted
`target_company_id`. Pydantic v2's default is `extra='ignore'`, so the
unknown field was silently dropped — the request "succeeded" but
generated content for the wrong company.

Fix: every request body model now has
`model_config = ConfigDict(extra="forbid")`. Unknown fields → 422.
Loud failures are cheaper than silent ones, and a client mismatch is
much easier to diagnose when the server screams.

### 3.4 Why `cite:knowledge_id=<uuid>` breadcrumbs in resume bullets

The naive credit-assignment model is "this resume landed a callback,
upvote everything in the persona." That's useless — the persona has
60+ knowledge rows and only 2-3 actually drove the win.

So every G2 bullet is required to emit `cite:knowledge_id=<uuid>` for
the row it pulled from. The marker survives into the saved resume.
When `outcome_to_persona` runs, it parses the markers and propagates
Bayesian credit to _those specific rows_, not the persona as a whole.

The cost: writers occasionally hallucinate uuids. We added a node
`validate_citations` that drops any `cite:` whose uuid isn't in the
retrieved knowledge set before persisting.

### 3.5 Why phantom regex + DB-level guard (BUG-013 legacy)

Earlier in the project a scraper bug created "phantom companies"
— rows with `name="Senior Product Manager · Visa"` or similar where
the JD title leaked into the company field. Phantoms pollute the
persona deep-research path because every phantom triggers a new
persona build = $0.20 burned per phantom.

Defence in depth:
1. **Regex at ingest** (`agents/_job_guards.py`) — rejects names
   containing job-title tokens (`Engineer|Manager|Director|...`).
2. **`companies.is_phantom` flag** + nightly sweeper that flags any
   row newer than 24h matching the regex.
3. **Hard-delete migration** (2026_05_13_028) that drops `is_phantom=TRUE`
   rows after first NULLing all referencing FKs.
4. **`jobs.validation_failed='phantom_company_string'`** flag
   (migration 030) for the 183 historical jobs whose company field
   is bad text but no `companies` row was created.

### 3.6 Why HITL is non-negotiable for G7

G7 fills Greenhouse / Lever / Workday application forms. We never
auto-submit. Reasons:

- An LLM hallucinating an "expected salary" field could anchor the
  user $40K below market.
- Submitting twice gets a hiring manager to flag the user.
- Greenhouse's ATS treats automated submissions as bot traffic.

So G7 always ends at `interrupt(approve=True/False)` per question, the
user reviews the suggested answer + the `cite:knowledge_id` /
`cite:story_id` it's based on, and only the user clicks Submit.

### 3.7 Why 3-layer enrichment (Apify + Perplexity + Apollo)

Each layer is _orthogonal_ — they capture different signal classes that
the others physically cannot. No single source is sufficient.

| Layer | Captures | Misses |
|---|---|---|
| **Apify** | Depth (long-form pages, Glassdoor, blog archive) | Anything posted today; firmographic |
| **Perplexity** | Recency (last 30 days) + strategic anchors | Long-form depth; structured firmographic |
| **Apollo** | Firmographic (HC, funding, hiring intel) + people | Soft signals, culture, recency |

Each layer writes to `company_knowledge` with a distinct
`metadata.source` so credit assignment can later attribute outcomes
back to which signal class mattered. After 100+ outcomes we expect to
have data on which source predicts callbacks best per role archetype.

### 3.8 Why per-company personas, not a global one

A "persona" is the success/failure signal for a specific company. Visa
rewards security depth + scale stories; Marqeta rewards API design + payment
networks; Adyen rewards platform thinking + cross-border. A global
"persona" would average these to mush.

Schema: `company_personas` has `success_patterns` (array of strings,
the patterns that won past callbacks) and `failure_patterns` (the
patterns that got auto-rejected). Both arrays evolve via Bayesian
credit on every logged outcome.

### 3.9 Why we never auto-post to LinkedIn

`linkedin_drafts.status` flow is `draft → polish → ready_to_copy →
posted_manually`. Reasons:

- LinkedIn's TOS forbids automation; a single auto-post can suspend
  the account. The user's account is the asset.
- Tonality drift — even with G11 voice calibration, a writer-model
  failure mode is "looks like the user but isn't quite." A copy-paste
  human step catches that.
- Image generation is the user's call (we generate the brief, not the
  image). Avoids storing third-party images we don't have rights to.

Future-mode: Buffer integration when we exit single-user mode, still
behind a user toggle.

### 3.10 Why `/insights?tab=traces`

LangGraph runs across 12+ nodes per resume build. When a build is bad,
the user (and the dev) need to inspect which node misbehaved without
trawling logs.

`v_graph_runs` SQL view aggregates `agent_call_log` rows by `run_id`
into per-run cards (total cost, total latency, error counts).
`api/traces.py` serves the per-run detail. `/insights?tab=traces`
renders them with a flamegraph + per-node prompt/response inspector.

This is the difference between "the resume looks weird" (unactionable)
and "the critic node returned `null` for `improvements` at 2026-05-14 19:12 UTC
on run abc-123" (a 5-minute fix).

---

## 4. Things we deliberately did NOT build

| Wanted | Why we didn't |
|---|---|
| **Chrome extension for in-browser apply** | Maintenance burden + browser-store review delays. Apify covers the same use cases for now. |
| **In-house ATS keyword scorer** | Kimi K2 + a 100-line prompt is 95% as good and free of maintenance. |
| **Auto-LinkedIn-posting** | Account-suspension risk (see §3.9). |
| **PDF parsing for the user's CV** | We require markdown. CVs in PDF lose semantic structure; the user pays once to convert. |
| **Real-time job feed websocket** | `/today` is a daily check-in product, not a real-time one. Polling at 60s is fine. |
| **Multi-modal vision models on JDs** | JDs are text. Vision is wasted spend. (We do use vision on screenshots in G7.) |
| **Custom fine-tunes per LLM** | Caching + few-shot examples + persona injection are 90% of fine-tuning's win at 0% of the ops cost. |
| **Mongo / DynamoDB / Pinecone** | Postgres + pgvector + RLS is one system. Three databases means three sets of backups, ACLs, migrations. |

---

## 5. Component map — how everything chains

```
        ┌──────────────────┐
        │  JobScoutAgent   │  daily 09:00 — fresh jobs
        └────────┬─────────┘
                 ▼
        ┌──────────────────┐
        │   G5 fit score   │  6-dim, A-F grade, legitimacy_tier
        └────────┬─────────┘
                 ▼
            jobs table  ───►  /today ranked queue
                 │                │
                 │                ▼
                 │       user clicks "Apply"
                 │                │
                 │                ▼
                 │       /applications/[id]/workspace
                 │                │
                 ▼                │
        ┌──────────────────┐     │
        │  G1.5 persona    │     │
        │  deep-research   │     │
        │  Apify + Gemini  │     │
        └────────┬─────────┘     │
                 ▼                │
       company_personas  ◄────┐  │
       company_knowledge  ◄───┘  │
                                  ▼
                          ┌─────────────────┐
                          │  G2 resume      │
                          │  12 nodes,      │
                          │  emits cite:    │
                          └────────┬────────┘
                                   ▼
                          ┌─────────────────┐
                          │  G3 interview   │
                          │  prep pack      │
                          └────────┬────────┘
                                   ▼
                          ┌─────────────────┐
                          │  G7 application │
                          │  form assist    │
                          │  (HITL)         │
                          └────────┬────────┘
                                   ▼
                            user submits
                                   │
                                   ▼
                          ┌─────────────────┐
                          │  G6 follow-up   │
                          │  cadence        │  daily 18:00
                          └────────┬────────┘
                                   ▼
                              outcome logged
                                   │
                                   ▼
                          ┌─────────────────┐
                          │  outcome→       │
                          │  persona        │
                          │  Bayesian       │
                          │  credit         │
                          └────────┬────────┘
                                   ▼
                         persona evolves ──► next week's G2 is smarter

   In parallel (orthogonal data sources):
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │  Apify       │  │  Perplexity  │  │  Apollo      │  │  Firecrawl   │
   │  depth       │  │  recency     │  │  firmographic│  │  JS-rendered │
   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
          └─────────────────┴─────────────────┴─────────────────┘
                                   ▼
                          company_knowledge
                       (metadata.source tagged)

   G4 LinkedIn engine (independent track):
   ┌──────────────┐   ┌─────────┐   ┌───────────┐   ┌─────────┐   ┌────────────┐
   │ Perplexity   │──►│ pick_   │──►│ Opus      │──►│ Sonnet  │──►│ Opus       │
   │ recency      │   │ angle   │   │ writer    │   │ critic  │   │ polish     │
   └──────────────┘   └─────────┘   └───────────┘   └─────────┘   └─────┬──────┘
                                                                        ▼
                                                              ┌──────────────────┐
                                                              │ Sonnet           │
                                                              │ image_brief      │
                                                              └──────┬───────────┘
                                                                     ▼
                                                          linkedin_drafts
                                                          (status=ready_to_copy)
                                                                     │
                                                                     ▼
                                                          /linkedin UI
                                                          user copies + posts
                                                          (never auto-posted)
```

---

## 6. Cost reality check

Steady-state monthly burn: **~$30–80/month** (Anthropic-dominated;
Perplexity ~$2.50; Apollo gated on credits; Apify on demand).

| Reduction lever | Estimated saving | Status |
|---|---|---|
| Prompt caching on persona blocks | 30-40% | partial — caching enabled for Opus only |
| Sonnet for critics (currently Opus) | 15-20% | done in G4; pending in G2 |
| Conditional ensemble fan-out (skip Kimi when ATS score >85) | 10-15% | not yet shipped |
| Haiku for orchestration nodes | 5-10% | not yet shipped |
| Aggressive `extra='forbid'` to kill silent re-runs | 5% | shipped via BUG-target_company_id fix |

Net target: $20-40/month at the same quality level. See
[`docs/AUDIT_2026_05_10.md`](docs/AUDIT_2026_05_10.md) for the cost
audit details.

---

## 7. What's next (parked)

- **Apollo paid-plan unlock** — turns on `/search-people` (referral
  seeding) and `/search-companies` (canonical org_id lookup). Free
  plan returns 402. ~$99/mo.
- **Multi-tenant pivot** — 65 endpoints still use service-role. The
  scaffold (`Depends(get_current_user)`, JWT verification) is ready;
  needs the bulk endpoint sweep. Recipe in [`api/AUTH.md`](api/AUTH.md).
- **Persona evolution dashboard** — `persona_versions` history exists,
  no UI yet. Want a slider that shows "Visa persona 2026-04-01 vs
  2026-05-14" side-by-side with outcome attribution.
- **Mobile-friendly `/today`** — currently desktop-first. The user
  checks `/today` on phone most mornings. Needs RSC + responsive pass.
- **Buffer integration for LinkedIn** — once multi-tenant ships, add
  the third party scheduler so users can opt in to auto-publishing
  (still defaulted off).

---

## 8. The single most important property

Everything above is in service of one invariant: **the system learns
from real outcomes, not from prompt cleverness.**

Generic LLM tools improve by upgrading the foundation model. We improve
by:

1. Adding more `cite:knowledge_id` markers.
2. Logging more `interview_outcomes` and `resume_outcomes`.
3. Letting `outcome_to_persona` propagate credit.
4. Watching `success_patterns` evolve in `/personas/[name]`.

If the user logs 50 outcomes and the personas don't get sharper, the
build is wrong and we need to fix the credit-assignment loop. If they
do get sharper, every other lever (prompt quality, model choice, fan-out
strategy) is secondary tuning.

That's why the data model is the centerpiece — not the prompts.
