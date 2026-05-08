# G3 — Interview Prep Graph (Design)

**Status**: Design + initial implementation · Phase 2 branch
**Phase**: 2 (after Phase 1 G2 lands)
**Owner**: Rizwan
**Last updated**: 2026-05-09

---

## 1. Why this graph

The current interview prep flow in `agents/interview_agent.py` is a single
Claude Opus call that takes a job description and returns one big blob of
"likely questions + STAR templates". Three problems:

1. **No live grounding**. The questions are Claude's general knowledge of
   the company, not what the company actually asks. Reddit / Blind /
   Glassdoor signal is the highest-value source for this and we don't use it.
2. **No story-bank awareness**. The templates are generic — they don't pull
   from the candidate's actual STAR+R stories or flag where stories are
   missing. The user copy-pastes generic answers and rehearses badly.
3. **No quality loop**. The output is single-shot — there's no critic,
   no convergence, no rehearsal feedback. A bad prediction or weak answer
   template ships unchanged.

G3 fixes all three:

| Problem | G3 mechanism |
|---|---|
| No live grounding | Technical predictor uses Gemini 2.5 Pro + Google Search grounding (`site:reddit.com OR site:blind.teamblind.com`) |
| No story-bank awareness | star_story_matcher pulls from `story_bank`; flags missing stories with `needs_rizwan_input=true` |
| No quality loop | mock_interview_loop runs Claude (interviewer) ↔ DeepSeek-R1 (critic) until score ≥ 80 or 2 iters |

This is the same multi-LLM, multi-node pattern as G2, scaled down for the
interview-prep use case (smaller cap, fewer iterations).

---

## 2. State schema

```python
from typing import Annotated, TypedDict
from operator import add

class InterviewPrepState(TypedDict, total=False):
    # ─── Inputs (set at entry) ──────────────────────────────────────────
    application_id: str             # uuid, public.applications.id
    application: dict               # full row
    job_id: int                     # public.jobs.id (INTEGER)
    job: dict                       # full row
    company_name: str               # canonicalised
    round_type: str                 # recruiter | hm | panel | exec | technical | take_home
    round_number: int               # 1-based
    company_persona: dict | None    # row from public.company_personas (None on cold start)
    story_bank: list[dict]          # all rows from public.story_bank
    last_resume_build: dict | None  # most recent resume_builds for this job
    interview_history: list[dict]   # prior interview_outcomes for this application
    interview_prep_id: str          # uuid, set on entry

    # ─── Predictor outputs (parallel branch) ────────────────────────────
    behavioral_questions: list[dict]   # [{question, competency, importance}]
    technical_questions: list[dict]
    domain_questions: list[dict]

    # ─── Merged + matched ───────────────────────────────────────────────
    likely_questions: list[dict]    # union → dedupe → sort → cap 20
    star_stories: list[dict]        # [{question, story_id, match_quality, needs_rizwan_input}]

    # ─── Mock loop ──────────────────────────────────────────────────────
    mock_target_question: dict | None
    mock_answer_md: str
    mock_critic_score: int
    mock_critic_feedback: list[str]
    mock_iteration: int

    # ─── Final pack outputs ─────────────────────────────────────────────
    company_hooks: list[str]
    red_flag_questions: list[str]
    salary_negotiation_notes: str
    prep_pack_md: str
    prep_pack_url: str

    # ─── Audit / control ────────────────────────────────────────────────
    transcript: Annotated[list, add]   # append-only — every node turn
    cost_usd_total: Annotated[float, add]
    latency_ms_total: Annotated[int, add]
    iteration: int
    converged: bool
    cost_cap_usd: float
    cost_capped: bool
    error: Optional[str]
```

Two LangGraph reducer notes (mirror G2):
- `transcript` uses `operator.add` so multiple nodes can append turns
  without conflicting (every node MUST return its turn wrapped in a
  single-element list).
- `cost_usd_total` and `latency_ms_total` use `operator.add` for cumulative
  totals across nodes.

---

## 3. Node-by-node spec

### 3.1 entry
**Pure code, no LLM.** Loads from Supabase:
- `applications` row (by `application_id`)
- `jobs` row (`jobs.id` is INTEGER)
- `company_personas` row (NULL on cold start — graceful)
- `story_bank` rows (live count = 0 today; cold-start path is HOT)
- Most recent `resume_builds` for this job (for keyword surface area)
- Prior `interview_outcomes` for this application (round 2+ signal)

Creates an `interview_prep` row with `status='running'` and seeds
`interview_prep_id`.

### 3.2 behavioral_predictor (parallel branch A)
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Reads: `job`, `company_persona` (recruitment_process, interview_format,
hiring_signals from metadata), `interview_history`.
Outputs: `behavioral_questions` — list of `{question, competency, importance}` dicts.

Why Claude: behavioural / competency questions need narrative reasoning to
predict accurately. The persona's `recruitment_process` + `interview_format`
fields drive the prompt, with a fallback `BEHAVIORAL_PREDICTOR_FALLBACK_SYSTEM`
for cold-start.

### 3.3 technical_predictor (parallel branch B)
**Provider**: `google` · **Model**: `gemini-2.5-pro` · **Tools**: `google_search` grounding

Reads: `job`, `company_persona`, `last_resume_build` (resume excerpt for
keyword surface area).
Outputs: `technical_questions` — list of `{question, competency, importance}`.

Why Gemini: native Google grounding pulls live signal from Reddit / Blind /
Glassdoor without a separate Serper call. The system prompt explicitly
instructs the model to search e.g. `"{company} interview process site:reddit.com"`.

### 3.4 domain_predictor (parallel branch C)
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Reads: `job` (for `archetype`), `company_persona` (for `category`).
Outputs: `domain_questions` — payments-domain questions tailored to
archetype × category.

Why Claude: domain depth and concrete framing — e.g. "walk me through
the lifecycle of a B2B settlement as a CPO owning treasury reconciliation"
rather than "explain payments".

### 3.5 merge_questions
**Pure code, no LLM.**

- Tag each question with its source ("behavioral", "technical", "domain")
- Sort candidates by `(-importance, -source_weight)` where
  `source_weight = {technical: 3, domain: 2, behavioral: 1}` (technical
  questions get tie-break priority because grounded signal is the most
  distinguishing)
- Greedy dedup using token-set Jaccard ≥ 0.7
- Cap result at 20

Stored as `likely_questions`.

### 3.6 star_story_matcher
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Reads: `likely_questions`, `story_bank`.
Outputs: `star_stories` — `[{question, story_id, match_quality, rationale, needs_rizwan_input}]`.

**Cold-start path** (live DB has 0 stories today): when `story_bank` is empty,
short-circuit — flag every question with `needs_rizwan_input=true` and skip
the LLM call entirely (saves ~$0.40). The single most common failure mode
is "no stories yet" so this is the hot path, not an edge case.

### 3.7 mock_interview_loop
**Single-node internal loop.**

Per iteration (max `g3_max_iterations=2`):
1. **mock_interviewer** (Claude Opus 4.5) generates a strong-answer template
   for the highest-importance question (or revises previous draft using
   critic feedback).
2. **mock_critic** (DeepSeek-R1) scores 0-100 on clarity / relevance /
   specificity / reflection / brevity, suggests improvements if score < 80.

Stop when `score ≥ g3_target_answer_score` OR `mock_iteration ≥ g3_max_iterations`
OR cost cap hit.

**Why single-node** rather than two nodes + a conditional edge (like G2's
writer ↔ critic ↔ orchestrator): tightly coupled, no need for LangGraph to
checkpoint between interviewer and critic, and the iteration cap is small (2).
Simpler topology, fewer edges to debug.

### 3.8 compile_prep_pack
**Pure code, no LLM.**

Renders the markdown prep pack:
- Top 20 questions grouped by source (behavioral / technical / domain)
- Each question annotated with STAR match quality (✅ strong / ⚠️ partial / ❌ missing)
- "Stories Rizwan must add before this round" section
- Red-flag questions (from persona)
- Mock answer transcript with critic score
- Company hooks (from persona)
- Salary negotiation notes (anchor on market 75th percentile, no current-comp anchor)

Surfaces persona arrays verbatim — doesn't paraphrase.

### 3.9 export
**Pure code, no LLM.**
- Upload `prep_pack_md` to Supabase Storage `interview-packs/<company>/<id>.md`
- UPDATE `interview_prep` row with status (cost_capped > exhausted > converged
  hierarchy, mirrors G2 Phase 1.11), all prep fields, transcript, cost +
  latency totals.

---

## 4. Graph topology

```python
from langgraph.graph import StateGraph, END

g = StateGraph(InterviewPrepState)
g.add_node("entry",                 entry_node)
g.add_node("behavioral_predictor",  behavioral_predictor_node)
g.add_node("technical_predictor",   technical_predictor_node)
g.add_node("domain_predictor",      domain_predictor_node)
g.add_node("merge_questions",       merge_questions_node)
g.add_node("star_story_matcher",    star_story_matcher_node)
g.add_node("mock_interview_loop",   mock_interview_loop_node)
g.add_node("compile_prep_pack",     compile_prep_pack_node)
g.add_node("export",                export_node)

g.set_entry_point("entry")

# Three-way parallel fan-out
g.add_edge("entry", "behavioral_predictor")
g.add_edge("entry", "technical_predictor")
g.add_edge("entry", "domain_predictor")

# All three feed merge (LangGraph waits for all three)
g.add_edge("behavioral_predictor", "merge_questions")
g.add_edge("technical_predictor",  "merge_questions")
g.add_edge("domain_predictor",     "merge_questions")

g.add_edge("merge_questions",     "star_story_matcher")
g.add_edge("star_story_matcher",  "mock_interview_loop")
g.add_edge("mock_interview_loop", "compile_prep_pack")
g.add_edge("compile_prep_pack",   "export")
g.add_edge("export",              END)

graph = g.compile(checkpointer=postgres_saver)
```

ASCII sketch:

```
                     entry
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
behavioral_pred  technical_pred  domain_pred
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                merge_questions
                       │
                       ▼
              star_story_matcher
                       │
                       ▼
            mock_interview_loop
              (internal loop:
               interviewer ↔ critic
               max 2 iters)
                       │
                       ▼
              compile_prep_pack
                       │
                       ▼
                    export
                       │
                       ▼
                      END
```

---

## 5. Checkpointing

Use `langgraph-checkpoint-postgres` against the existing Supabase Postgres
(already installed for G2). Thread id = `g3-app-<application_id>-r<round>`.
Lets us:

- Resume a prep mid-flight if the API server crashes
- Re-run individual nodes during prompt iteration without re-running the
  whole graph
- Audit-replay any historical prep deterministically

The `langgraph` namespace tables are shared with G2 — no new migration needed.

---

## 6. Cost analysis (per prep)

| Node | Provider | Tokens (in/out) | Cost |
|---|---|---|---|
| Behavioral Predictor | Claude Opus 4.5 | 6k / 1.5k | $0.20 |
| Technical Predictor | Gemini 2.5 Pro | 30k / 2k | $0.05 |
| Domain Predictor | Claude Opus 4.5 | 5k / 1.5k | $0.19 |
| Merge Questions | (none) | — | $0 |
| STAR Matcher | Claude Opus 4.5 | 12k / 3k | $0.41 |
| Mock Interviewer × 1.5 avg iters | Claude Opus 4.5 | 4k × 1.5 / 2.5k × 1.5 | $0.37 |
| Mock Critic × 1.5 iters | DeepSeek-R1 | 2k × 1.5 / 0.6k × 1.5 | $0.004 |
| Compile Prep Pack | (none) | — | $0 |
| Export | (none) | — | $0 |
| **Total per prep** | | | **~$1.22** |

At ~5 high-score interview rounds/month, that's ~$6/month. Manual gating
(only fired when the user moves an application to interview status) keeps
this in check.

---

## 7. Dependencies on Phase 1

This design assumes:

- ✅ `agents/llm_router.py` — multi-provider dispatch (Phase 0)
- ✅ `config/settings.py` G3_* slots (added in this phase)
- ✅ `db/g3_schema.sql` for `interview_prep` (added — apply manually via apply_migration)
- ✅ `langgraph` + `langgraph-checkpoint-postgres` installed (already there for G2)
- ✅ `resume_agents.g2_run.check_persona_quality_gate` (Phase 1.12) — REUSED, not duplicated
- ⏳ `story_bank` seeding — currently 0 rows; G3 handles this via cold-start
  fallback that flags every question. After 5–10 STAR stories are seeded,
  the matcher starts producing real matches.

---

## 8. Ship plan

| Step | What | Verification |
|---|---|---|
| 1 | Apply `db/g3_schema.sql` to Supabase via apply_migration | `SELECT * FROM interview_prep LIMIT 1;` succeeds |
| 2 | Already-installed: LangGraph + google-genai (came with G2) | `python -c "import langgraph"` |
| 3 | Add `interview_agents/g3_*.py` with the 9 node functions | `from interview_agents.g3_graph import build_g3_graph; build_g3_graph()` compiles |
| 4 | Wire `POST /jobs/{id}/prep-interview` endpoint | Endpoint returns 202 with `application_id` + persona gate result |
| 5 | Run G3 against 1–2 historical applications (Tabby / Mastercard) — compare to legacy InterviewAgent baseline | Side-by-side dashboard view |
| 6 | Seed `story_bank` with 5–10 high-leverage STAR stories | `SELECT count(*) FROM story_bank;` ≥ 5 |
| 7 | Flip `USE_G3_GRAPH=true` on Railway | `/jobs/{id}/prep-interview` returns prep pack URL |

---

## 8.5. Cost cap (Phase 2)

Mirrors G2 Phase 1.11. The `mock_interview_loop` enforces a per-prep hard
cap on cumulative LLM spend. This is the production-safety guarantee that
lets us run G3 in `USE_G3_GRAPH=true` mode without an unbounded worst-case.

### Configuration

| Setting | Default | Source |
|---|---|---|
| `settings.g3_max_cost_usd` | `3.0` | `config/settings.py` (env: `G3_MAX_COST_USD`) |
| Per-prep override | `None` | API: `POST /jobs/{id}/prep-interview?max_cost_usd=5` |

### Mechanism

The `mock_interview_loop_node` performs two cost checks per iteration:

1. **Pre-iter short-circuit** — if `state.cost_usd_total + local_cost >= cap`
   *before* the next iteration runs, skip the iteration entirely and
   force termination with `cost_capped=True`.
2. **Mid-iter check** — after the interviewer call, if its cost pushes
   the cumulative total over the cap, skip the critic call too and
   terminate.

The earlier nodes (predictors, matcher) don't enforce the cap because they
each run exactly once and their combined max cost (~$1.05) is well under
`g3_max_cost_usd=3.0`. The cap exists specifically to bound the
mock-interviewer × N iterations explosion.

### Status lifecycle

`interview_prep.status` takes one of five values:

| Status | When it's set |
|---|---|
| `running` | At entry_node, before any agent has run |
| `converged` | mock_critic_score ≥ g3_target_answer_score, or final pass with no convergence loop |
| `exhausted` | Hit `g3_max_iterations` without converging on quality |
| `cost_capped` | Hit `g3_max_cost_usd` mid-prep *(Phase 2)* |
| `failed` | Fatal error mid-graph |

Hierarchy in `export_node`: cost_capped beats exhausted beats converged.

### Worst-case spend bounds

With the default `g3_max_cost_usd=3.0`:

```
  behavioral_predictor   Claude   ≤ $0.30
  technical_predictor    Gemini   ≤ $0.50  (long context + grounding)
  domain_predictor       Claude   ≤ $0.30
  merge_questions        (none)
  star_story_matcher     Claude   ≤ $0.40
  mock_interviewer × N   Claude   ≤ $0.40 × N  (max 2 iter)
  mock_critic × N        DeepSeek ≤ $0.05 × N
  ── (cap forces converge here at ≥$3) ──
  compile_prep_pack      (none)
  export_node            (none)
                         ─────
  worst-case total       ~$3.10 (capped)
```

Unlike G2 (where polisher + cover_email overshoot the cap by ~$0.66),
G3's post-cap nodes are pure code — there's no overshoot. The actual
worst-case is exactly the cap value.

### Tuning guidance

- **For top-tier targets** (Stripe, Plaid): override per-prep to
  `max_cost_usd=5` if you want the mock loop to push past 2 iterations.
- **For exploratory budgets**: drop `g3_max_cost_usd=1.5` in env; the
  mock loop will only run 1 iteration before the cap fires.
- **For cold-start preps** (no story_bank): the matcher short-circuits
  without an LLM call, so total cost is dominated by predictors (~$1.00)
  and the loop runs to completion.

---

## 8.6. Persona quality gate (Phase 2)

**Why**: ~$1.20 per prep × 5 low-quality target personas = $6 wasted on
prep packs that read generic. The same Phase 1.12 mechanism that gates
G2 builds also gates G3 preps — except G3 **reuses** the function
rather than duplicating it.

### Reuse, don't duplicate

```python
from resume_agents.g2_run import check_persona_quality_gate

# In api/server.py /jobs/{id}/prep-interview:
gate = check_persona_quality_gate(
    company_name,
    force=force,
    min_quality=settings.g3_min_persona_quality,   # default 'medium'
)
```

This is the canonical pattern for shared infrastructure across graphs.
The function lives in `resume_agents.g2_run` and is documented to support
arbitrary callers via the `min_quality` param. G3 just passes its own
default. **The function is NOT redefined in `interview_agents/`** — see
`tests/test_g3_graph.py::TestPersonaGateReuse` for the verification test.

### Quality tiers (set by Phase 0 seed + Phase 1.6 synthesizer)

| Tier | unknown_sections | Live examples (2026-05-09) |
|---|---|---|
| `high` | 0 | PayPal, Plaid, Revolut, Square (Block), Standard Chartered |
| `medium` | 1–2 | Adyen, Mastercard, Stripe, Wise, Tabby (and 18 more) |
| `low` | 3+ | Visa, Thunes, Wio Bank, Payoneer, "Merchant Acquiring …" |

### Gate logic

Identical to G2 Phase 1.12 (force → cold_start → blocked → pass).

### How it surfaces

**API**: `POST /jobs/{id}/prep-interview?force=true` to bypass.
On block, returns HTTP 400 with the same structured detail shape as G2
(plus `min_quality` reflecting `g3_min_persona_quality`).

### Configuration

| Setting | Default | Effect |
|---|---|---|
| `G3_MIN_PERSONA_QUALITY=high` | — | Only PayPal/Plaid/Revolut/Square/Standard Chartered allowed without force |
| `G3_MIN_PERSONA_QUALITY=medium` | ✅ default | Blocks low only |
| `G3_MIN_PERSONA_QUALITY=low` | — | Allows everything (gate disabled) |

### Tuning guidance

Same as G2 Phase 1.12. The same 5 low-quality personas (Visa, Thunes, etc.)
need outcome data before re-synthesizing — there's nothing to gain by
prepping interviews against them yet, since the resume_builds were also
gated.

---

## 9. Live-data validation snapshot (2026-05-09)

| Check | Result | Implication |
|---|---|---|
| Applications at `status='evaluated'` (interview-eligible) | 2 | Small but workable interview backlog |
| `story_bank` row count | **0** | Cold-start path is the PRIMARY path for the first 5–10 preps. star_story_matcher's empty-bank short-circuit is hot, not edge case. |
| `interview_outcomes` row count | 0 | Round-2+ predictor signal is empty until the user actually has interviews and logs them |
| `company_personas` ready (≥ medium quality) | ~28 of 33 | Persona gate will pass for most target companies on default `medium` |
| `company_personas` low-quality (blocked by default) | 5 (Visa, Thunes, Wio Bank, Payoneer, "Merchant Acquiring …") | Same gate behaviour as G2 — passes `?force=true` to override |

So the design holds, but the **cold-start path** (no stories, no prior
rounds, possibly low persona quality) is the primary path for the first
5–10 preps. Make it robust, not an afterthought.

---

## 10. Open questions (for later resolution)

1. **PDF rendering** — should `compile_prep_pack` produce a PDF in
   addition to markdown? Today: markdown + Storage URL only.
2. **Round-aware predictor prompts** — the predictors currently get
   `round_type` as input but treat all round types similarly. Round 1
   recruiter chat is very different from a panel exec round; specialise
   the prompts per round_type as data accumulates.
3. **Story embedding match** — once `story_bank.embedding` (vector(1536))
   is populated, swap the LLM-based matcher for a pgvector cosine search
   for the top-K candidates, then ask Claude only to choose among the
   short list. Saves ~$0.30 per prep at scale.
4. **Multi-round prep pack chaining** — when round 2 fires, the predictors
   should weight round-1 questions/feedback heavily. Today the prompt
   passes `interview_history` but doesn't explicitly dampen overlap with
   what was already asked. Fix this once we have ≥ 3 prior rounds logged.
5. **Mock loop topology** — current single-node loop is simpler but loses
   LangGraph checkpointing between iterations. If we ever want to support
   "resume mid-mock-iteration" (the user comes back to the dashboard
   mid-rehearsal), promote it to two nodes + conditional edge like G2.
6. **Negotiation drill** — salary_negotiation_notes is currently a static
   template. A future node could call `SalaryResearchAgent` for live market
   anchors and inject specific numbers (75th percentile, relocation premium).
