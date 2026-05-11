# Flow Review — How to Make This Better (2026-05-11)

**Scope:** end-to-end user journey + code-level review of the load-bearing
modules (~10,800 LOC Python + 1,500 LOC dashboard). Written after
shipping Sprint 1 P0 + Phases 1/2/3 + Perplexity + Apollo + 4 boot fixes
this session.

**Companion docs:**
[`docs/AUDIT_360_SYNTHESIS.md`](AUDIT_360_SYNTHESIS.md) (6-expert
strategic audit + P0/P1/P2 roadmap from 2026-05-10) ·
[`docs/AUDIT_2026_05_10.md`](AUDIT_2026_05_10.md) (cost + test +
security audit by a parallel Claude session) · [`docs/SPRINT_1_STATUS.md`](SPRINT_1_STATUS.md) (decisions log).

This doc focuses on **flow / UX / specific code paths** — the gap
between what's shipped and what's *useful*.

---

## TL;DR — one paragraph verdict

The infrastructure shipped this session is genuinely solid (multi-tenant
schema, durable queue, three-layer RAG pipeline, citation markers,
outcome credits, eval harness scaffold). **The product is one Railway
worker deployment + one APOLLO_API_KEY env var away from being fully
functional end-to-end.** Beyond those two production gaps, the biggest
flow improvements are (a) make `/today` round-robin across kinds instead
of score-sorting everything (Adyen ×3 currently fills the top), (b)
loosen the LinkedIn-post-due slot semantics so the first draft of the
week actually surfaces, (c) wire `outcome_to_persona.credit_outcome` to
fire on outcome insert (it currently only runs via Sunday cron), (d) cut
the workspace bundle's 7-query N+1 to a single CTE, and (e) burn the
audit's Tier-1 prompt-caching change (40-60% cost reduction, neutral
quality). None of these are large.

---

## Part 1 — Three load-bearing operational gaps

These are blocking the killer-thing wedge from spinning. None require
code changes — they're deployment / config gaps.

### Gap 1.1 — Railway worker service not running

**Symptom (2026-05-11 production):** `POST /linkedin/drafts/generate`
returns 202 with `run_ids: [fc337aa5-...]`, the `jobs_runs` row gets
written with `status='queued'`, but `started_at` stays NULL and
`attempts` stays 0 forever. Same for `POST /workspace/{id}/build-resume`
(G2) and `POST /interview-studio/{id}/build-prep-pack` (G3).

**Why:** `api/queue.py` writes to Redis. A separate Railway service
running `python -m api.worker` is supposed to dequeue and execute. Today
there is no worker service — so `apify_token`, all 5 LLM provider keys,
the Anthropic prompt caching potential — all of it is on standby.

**Fix:** Railway → New Service → Same repo → Build settings: use
`Dockerfile.worker` → Env vars: copy from the API service →
`WORKER_CONCURRENCY=1` → Deploy.

**Time to fix:** 5 minutes in Railway dashboard.

**Until this lands:** G2 / G3 / G4 are dead. Every "Start application
process" click on /today silently enqueues a row that never runs.
That's the #1 thing to fix.

### Gap 1.2 — Apollo API key missing on Railway

**Symptom:** `POST /apollo/enrich/Visa` returns 500 with the same
"Internal Server Error" body. `agents/apollo_enrich.py:73` raises
`RuntimeError("APOLLO_API_KEY not set...")` on first call when the
env var is unset.

**Fix:** Railway → API service → Variables → Add `APOLLO_API_KEY=<key>`
→ Deploy (auto on Railway).

**Time to fix:** 60 seconds.

### Gap 1.3 — Job validator never runs on a schedule

**Symptom:** `jobs.last_validated_at` is NULL on **every job in
production** including job 1020 (Adyen Head of Product Operations,
score 95, resume_ready). Per
[`agents/job_validator.py:46`](../agents/job_validator.py) the
validator has a 6-hour cache, but it requires an outer scheduler to
invoke `validate_batch()`.

**Why:** Designed to run on a 30-min APScheduler cron alongside
`orphan_reaper.py`, but no schedule entry was added in
[`main.py::start_scheduler`](../main.py).

**Effect:** If a JD URL goes dead (Adyen takes a job down, Stripe
fills the role), `/today` keeps surfacing it as "ready to apply" until
the next manual `python -m agents.job_validator` run.

**Fix:** Add a 30-min `APScheduler` job. Two lines in `main.py`.

```python
sched.add_job(
    lambda: validate_batch(user_id=DEFAULT_USER_ID, limit=50),
    trigger=CronTrigger.from_crontab("*/30 * * * *"),
    id="job_validator",
)
```

**Time to fix:** 10 minutes incl. test.

---

## Part 2 — Flow-level UX issues (concrete with file:line)

These are the moments where the product feels wrong, ranked by
how often each fires.

### Flow 2.1 — `/today` shows the same 3 Adyen jobs above the fold every day

**File:** [`api/actions.py:309-318`](../api/actions.py) — `_KIND_PRIORITY`
ranks `resume_ready=1, score_high_no_resume=2`, and within each kind
sorts strictly by `-score`. Result: **all 6 `resume_ready` cards come
first, regardless of company diversity.**

In production today: top 6 cards are Adyen ×3 (score 95) → Adecco →
Finkraft → Ventula. Same Adyen-heavy block every day until you mark one
applied. Visa (90, no resume) sits at rank 10; Marqeta (88) at 13.

**Why this is wrong:** The user already knows about their top targets.
The reason for `/today` is to surface what's *fresh* or *actionable
right now*, not to re-rank an applications backlog by score.

**Better ranking:**

```python
def _rank(actions):
    by_kind = defaultdict(list)
    for a in actions:
        by_kind[a["kind"]].append(a)
    # 1 from each high-priority kind first (round-robin) so user sees
    # variety, THEN extra cards from the deepest kind.
    out = []
    kind_order = [
        "linkedin_post_due",
        "resume_ready",
        "score_high_no_resume",
        "stale_application",
        "persona_stale",
        "score_below_threshold",
    ]
    # First pass: one card from each kind that has any
    for k in kind_order:
        if by_kind[k]:
            out.append(by_kind[k].pop(0))
    # Second pass: depth-fill in priority order
    for k in kind_order:
        out.extend(by_kind[k])
    return out
```

**Effect:** top 6 = 1 linkedin_post + 1 resume_ready + 1
score_high_no_resume + 1 stale_application + 1 persona_stale + 1
score_below_threshold (or fewer if some are empty), giving the user
fresh / diverse cards every day. **Adyen Head of Product Operations
still leads, but Visa Cross-border PM (rank 10 under current logic)
moves to rank 3.**

**Effort:** 15-line edit to `_rank()`. Single PR.

### Flow 2.2 — LinkedIn post never surfaces because slot window is too tight

**File:** [`api/actions.py:130-156`](../api/actions.py) —
`_build_linkedin_post_due` only returns a card if a draft has
`status IN ('approved', 'scheduled')` **AND** `scheduled_for` falls
strictly inside today (`>= today 00:00 UTC, < tomorrow 00:00 UTC`).

**Why this breaks in practice:**
- A new user has 0 drafts → no card ever
- An existing user has drafts in `status='draft'` waiting for review →
  no card (UI never tells them to approve)
- Once approved, `scheduled_for` is set to "next Mon/Wed/Fri 09:00" so
  a Tuesday-morning approval shows a card 24 hours later — the user
  sees nothing on the day they actually approved

**Better:**

| Condition | Card |
|---|---|
| Any draft in `status='draft'` | "1 LinkedIn draft awaiting review — open /linkedin" (state=`pending`) |
| Draft `status='approved'` with `scheduled_for` between now and +24h | "Post going live in 4h — review and copy" (state=`ready`) |
| Draft `status='scheduled'` with `scheduled_for` >24h out | (silent — no card) |
| Zero drafts at all | "No LinkedIn draft this week — generate one (visibility +2× reach)" (state=`stale`) — clickable to trigger G4 |

That last empty-state card is the most important one. **Without it the
user has no nudge that the LinkedIn engine even exists.**

**Effort:** ~40-line rewrite of `_build_linkedin_post_due`. Add an
empty-state card builder.

### Flow 2.3 — `/today` action card primary CTA semantics are inconsistent

**File:** [`dashboard/src/components/today/TodayActionCard.tsx`](../dashboard/src/components/today/TodayActionCard.tsx)
maps `primary.onClick` to 3 enum values:
- `copy` (LinkedIn post) → JS clipboard
- `kickoff_g2` (score_high_no_resume) → POST /workspace/{id}/build-resume
- `log_outcome` (stale_application) → some modal

But [`api/actions.py:240`](../api/actions.py) — `score_high_no_resume`
cards always set `primary_label="Start application process"` and
`primary_href` to the workspace, NOT `kickoff_g2`. So the actual button
just navigates — the build doesn't auto-trigger.

**Behaviour mismatch:** User clicks "Start application process",
lands on the workspace, sees "Build my resume" button, clicks it,
THEN G2 enqueues. Two clicks instead of one.

**Better:** Either (a) the `/today` card auto-triggers G2 on click and
then redirects to the workspace (which polls), OR (b) the workspace
auto-triggers G2 on load if `resume_generated_at IS NULL` AND there's
no in-flight `jobs_runs` row. **(b) is the cleaner choice — keeps the
workspace as the source of truth.**

**Effort:** 30 lines in `dashboard/src/app/applications/[id]/workspace/page.tsx`
(server component) to enqueue on first load when no resume exists.

### Flow 2.4 — Workspace bundle does 7 separate Supabase queries

**File:** [`api/workspace.py:415-510`](../api/workspace.py) — `GET /workspace/{job_id}` calls 7 helpers in sequence:
- `_get_job_for_user`
- `_get_application_for_job`
- `_get_latest_resume_build`
- `_get_persona_for_company`
- `_get_interview_prep_summary`
- `_get_warm_intro_paths` (calls referral graph)
- `_resolve_target_company_id` (3-step fuzzy match)

**Cost:** ~150ms per workspace view (Vercel server function → Railway
HTTP → 7× Supabase RPC). Visible page-load lag.

**Better:** single Postgres CTE that joins jobs + applications +
resume_builds + company_personas + interview_prep + target_companies
in one shot. The fuzzy company-name match can stay in Python (the
trgm index is already there from migration 004).

**Effort:** ~80-line SQL function + 30-line Python caller. One PR. **3-5×
faster bundle load.** Not blocker but felt.

### Flow 2.5 — Resume editor's chat history dies on tab close

**File:** [`dashboard/src/components/workspace/ResumeEditor.tsx:166-280`](../dashboard/src/components/workspace/ResumeEditor.tsx) —
edit chat lives in React state only. Refresh the page → all turns gone.

**Why this hurts:** User does 5 "Quick tweak" edits, switches tabs to
check something, comes back — chat is empty, no record of what
changed. Editor reverts to last *saved* markdown but the conversation
is gone.

**Better:** Persist turns to a new `resume_edit_chats` table keyed on
`resume_build_id`. Stream from there on workspace load.

**Migration:** new table, ~40 lines SQL.
**Code:** ~50 lines in ResumeEditor + new GET endpoint.

**Effort:** half-day. **Pays for itself the first time you regret
losing edits.**

### Flow 2.6 — Interview studio tutor has no transcript persistence

Same pattern as 2.5. [`dashboard/src/components/interview-studio/TutorChat.tsx`](../dashboard/src/components/interview-studio/TutorChat.tsx)
stores messages in React state only. But the **DB table already exists**
(migration 008 created `interview_tutor_messages`) — the wiring just
wasn't done.

**Effort:** ~30 lines (already has table, just wire reads + writes).

---

## Part 3 — The killer-thing wedge is wired but not firing

The audit's #1 differentiator was "outcome-conditioned, peer-network-aware,
persona-evolved." Each leg has shipped, but:

### Wedge 3.1 — Outcome credits don't update on insert

**File:** [`agents/outcome_to_persona.py`](../agents/outcome_to_persona.py) —
`credit_outcome(outcome_id, kind)` is a CLI-callable function. It's
called from [`api/interview_studio.py::log_outcome`](../api/interview_studio.py)
**but** the call is best-effort async-wrapped, and on production today
nothing has actually run yet (no outcomes logged).

**Better:** This should fire **synchronously on the same request** as
the outcome insert, with a try/except. The outcome→persona link is the
*entire reason* the table exists. A "log outcome" UX without a "we
recomputed your persona based on this" follow-up is the same
pre-existing system the audit said had a fake wedge.

**Effort:** make it sync in the same handler, add a 1-line response
field: `"persona_delta": {"keywords_added": [...], "keywords_dropped": [...]}`.

### Wedge 3.2 — Persona version history table is empty

`persona_versions` table exists (migration 008) but no row has been
written. Every persona is still at the version it was synthesized to
(Marqeta v3, Mastercard v4, Visa v3). The evolution loop hasn't run.

**Why:** Same as 3.1 — only fires via Sunday cron + only after enough
outcome events accumulate. With no outcomes logged yet, no evolution.

**Better:** Snapshot the *current* persona once into `persona_versions`
on every successful G2 build (not just on evolution). Then the
timeline UI has something to show from day 1, and the eventual
evolution diff is comparable to a real baseline.

**Effort:** 20-line addition to `g2_run.py::run_g2_graph` post-finalize.

### Wedge 3.3 — Referral graph has zero people

`people` and `edges` tables exist (migration 004). Schema is good.
But the `me` row hasn't been inserted, no LinkedIn CSV has been
imported, so `/network` shows the empty-state.

**The product's only meaningful "warm intro" capability is gated on
this manual step.** No nudge anywhere in the dashboard tells the user
to upload.

**Better:** First-load detection in `/today`:
- Zero `people` rows for user → show a fixed top card: "Upload your
  LinkedIn connections CSV — unlock warm intros to your 71 targets
  (~5 min, manual export)"
- After upload → card disappears, replaced by per-target warm-intro
  cards on /today and /workspace

**Effort:** 30-line addition to `_rank()` in actions.py + new card
kind `network_seed_missing`.

---

## Part 4 — Cost wins from the audit doc, ranked & file-pinned

Drawing from [`docs/AUDIT_2026_05_10.md`](AUDIT_2026_05_10.md) — the
parallel session's cost audit identified 40-60% LLM reduction. Already
pre-costed; I'm just re-pinning to specific PRs you'd open.

| # | Win | File | LOC delta | Monthly save |
|---|---|---|---|---|
| C1 | **Anthropic prompt caching ON** | [`agents/llm_router.py:300`](../agents/llm_router.py) | +15 | $30-80 |
| C2 | `max_tokens=2000` for reasoning models | [`agents/llm_router.py:214`](../agents/llm_router.py) | +2 | $15-40 |
| C3 | Skip critic B when critic A score ≥ 60 | [`resume_agents/g2_graph.py`](../resume_agents/g2_graph.py) + [`g2_nodes.py:644+671`](../resume_agents/g2_nodes.py) | +25 | $20-50 |
| C4 | Persona-synth gate (skip when no outcomes) | [`agents/persona_synthesizer.py`](../agents/persona_synthesizer.py) | +20 | $40-80 |
| C5 | Haiku 4.5 for orchestrator + polisher | [`config/settings.py`](../config/settings.py) | 2-line env default | $10-30 |
| **Total** | | | **~65 LOC** | **$115-280/mo** |

These are the cheapest big wins available. They have **no quality risk**
per the audit doc (each gated behind a golden-eval run before flip).

---

## Part 5 — Top 12 improvements, ranked by `impact / effort`

| Rank | What | Effort | Impact | File |
|---|---|---|---|---|
| 1 | **Deploy Railway worker service** | 5 min config | All graphs go live | n/a (Railway dashboard) |
| 2 | **Add `APOLLO_API_KEY` to Railway** | 1 min | Apollo enrich works | Railway env |
| 3 | **Round-robin /today ranking** | 15 lines | Visa/Marqeta visible above fold | `api/actions.py:_rank` |
| 4 | **LinkedIn empty-state nudge on /today** | 40 lines | First-time user sees the engine exists | `api/actions.py:_build_linkedin_post_due` |
| 5 | **Synchronous outcome→credit** | 20 lines | Wedge actually fires | `api/interview_studio.py:log_outcome` |
| 6 | **Prompt caching** | 15 lines | -40% Anthropic cost | `agents/llm_router.py:_call_anthropic` |
| 7 | **Network seed empty-state nudge** | 30 lines | User actually uploads CSV | `api/actions.py:_rank` |
| 8 | **Workspace bundle CTE** | 80 lines SQL + 30 Python | 3-5× page-load speed | `api/workspace.py:get_workspace` |
| 9 | **Persona version snapshot on every G2** | 20 lines | Evolution dashboard has data | `resume_agents/g2_run.py:finalize` |
| 10 | **Wire interview_tutor_messages reads** | 30 lines | Studio chat persists | `dashboard/.../TutorChat.tsx` |
| 11 | **Job validator schedule** | 10 lines | Dead JD URLs disappear | `main.py:start_scheduler` |
| 12 | **Audit C2-C5 (max_tokens, critic gating, Haiku swap, persona gate)** | ~50 lines total | -25% LLM cost | various |

**Top 4 alone (≤ 100 LOC total) unlock the wedge end-to-end:**
- #1 makes G2/G3/G4 actually run
- #3 makes the right cards visible
- #4 makes the LinkedIn engine discoverable
- #5 makes outcome logging *do* something

That's a half-day of work after the Railway worker is up.

---

## Part 6 — What's good and shouldn't change

This review is mostly "what's friction" — but the codebase has
substantive strengths that should be defended:

1. **Migration discipline.** 10 migrations this session, all idempotent,
   all transactional, all multi-tenant from day 1. The
   `IF NOT EXISTS` / `pg_constraint` guard pattern across
   [`db/migrations/2026_05_10_001_multi_tenancy.sql:148-220`](../db/migrations/2026_05_10_001_multi_tenancy.sql)
   is a model for the rest of the codebase.

2. **Citation marker propagation.** [`resume_agents/g2_nodes.py::insider_expert_node`](../resume_agents/g2_nodes.py)
   emits `cite:knowledge_id=<uuid>` both inline AND as a structured
   `cited_knowledge_ids` list on the transcript turn. **Defence in
   depth** — when `outcome_to_persona.credit_outcome` parses, both
   formats work. That's the kind of decision that pays off the first
   time the format drifts.

3. **Apollo wrapper plan-block handling.** [`agents/apollo_enrich.py:73-120`](../agents/apollo_enrich.py)
   classifies API_INACCESSIBLE responses as a typed
   `ApolloPlanBlocked` exception. The FastAPI router maps it to HTTP
   402 with an actionable hint. **No client confusion when the free
   plan blocks `/search-people`.**

4. **Perplexity disambiguation prompt.** [`agents/perplexity_search.py:213-260`](../agents/perplexity_search.py)
   handles the Visa-as-payments-company vs Visa-as-travel-document
   problem with both whitelist and blacklist constraints. The validation
   on Visa (2 real results, 7 noise discarded) shows the discipline works.

5. **Boot fix sequence.** When Railway crashed on email_validator →
   multipart → NameError, each fix was a clean small PR with the trace
   and the why. Net effect: production now boots cleanly on the same
   image that crashed three times yesterday.

6. **Hybrid resume edit cost gradient.** Quick tweak ($0.05) → Rebuild
   section ($0.30-0.50) → Full rebuild ($1) is the right cost shape.
   Users pick edit intensity, not the system. The auto-replace-with-
   confirm-if-dirty UX in `ResumeEditor.tsx` is the right default.

---

## Part 7 — Two-sprint recommendation

### Sprint A (this week, 1-2 days) — close the operational gaps

1. Deploy Railway worker service (5 min)
2. Add `APOLLO_API_KEY` to Railway env (1 min)
3. Re-run the queued `fc337aa5-...` G4 (should auto-pickup)
4. Trigger 1 G2 build to validate the citation markers fire end-to-end
5. Open improvements #3 (round-robin) and #4 (empty-state) as one PR

**Deliverable:** every flow proven working with real data, top 10 cards
on /today actually surface variety.

### Sprint B (next week, 3-5 days) — close the wedge

6. Improvement #5 (synchronous outcome→credit) + #9 (persona version
   snapshot on every G2)
7. Improvements #7 (network seed nudge) + #10 (tutor message
   persistence)
8. Audit C1 (prompt caching) + C3 (conditional critic) — these are the
   $115-280/mo wins

**Deliverable:** outcome-conditioned + persona-evolved + peer-network-aware
all visibly active. LLM cost drops 40-60%.

After these two sprints the system isn't just *built* — it's *useful*.
That's the gap between today's state (95% built, 5% felt) and the
"best in class" the audit was aiming at.
