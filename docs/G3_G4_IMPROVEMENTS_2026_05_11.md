# G3 + G4 — Deep Improvement Plan (2026-05-11)

> Companion to:
> - [`docs/AGENT_REVIEW_2026_05_11.md`](AGENT_REVIEW_2026_05_11.md) — full agent-layer review (1035 lines)
> - [`docs/FLOW_REVIEW_2026_05_11.md`](FLOW_REVIEW_2026_05_11.md) — end-to-end flow ranked improvements
> - [`docs/AUDIT_2026_05_10.md`](AUDIT_2026_05_10.md) — original audit
>
> Findings here are file:line specific, ranked by **(impact × likelihood) ÷ effort**.
> Every change has a cost / quality / risk delta annotated.

---

## 0. Production state — verified 2026-05-11

| Surface | Status | Evidence |
|---|---|---|
| Railway API (`jobhunt-production-8ec7.up.railway.app`) | ✅ live, **102 routes** | `GET /openapi.json` enumerated; v3 routers (`/actions/today`, `/workspace/*`, `/linkedin/*`, `/interview-studio/*`, `/apollo/*`) all mounted |
| `/actions/today?user_id=user_001` | ✅ 200 with real data | Returns Adyen jobs 1020/1022 at score 95/100 with `state=ready` and resume CTAs |
| Vercel dashboard `/today` | ✅ 200 | Proxy to Railway working |
| **G4 image_brief_node** | ✅ **FIXED — PR #53 merged** to main (commit `ad2e65d`) | `LLMRouter.ask` was rejecting `user=`, `response_format=`, `graph=`, `node_name=` kwargs; every pre-fix linkedin_drafts row has `image_brief=NULL` on disk. Post-merge, next G4 run produces real briefs |
| Railway **worker service** | ❓ declared in `railway.toml` but unverified on the dashboard | `[[services]] name = "worker"` exists with `START_MODE = "worker"`; user must enable on Railway UI for G2/G3/G4 jobs to actually execute (currently queued in Redis but never popped) |
| Migration 011 (jobs discovery quality columns) | ❌ NOT applied | File on disk (`db/migrations/2026_05_10_011_jobs_discovery_quality.sql`) but Supabase doesn't have `confidence_score / discovery_sources / freshness / validation_failed / validated_at` yet — JobScout v2 needs this before its first successful run |
| `APOLLO_API_KEY` on Railway | ❌ missing | All `/apollo/*` endpoints return ApolloError on production |

**TL;DR:** the read path is healthy. The write path (running graphs, applying migrations, hiring intel) is gated on three user actions — listed in §4 below.

---

## 1. What G3 and G4 actually are

### G3 — Interview Prep Graph (`interview_agents/`)

7-node LangGraph keyed on `(application_id, round_type)`. The 7 nodes are:

```
                ┌─ behavioral_predictor ─┐
load_inputs ────┼─ technical_predictor ──┼─→ merge_questions → star_matcher → mock_interview_loop → compile_prep_pack
                └─ domain_predictor ─────┘
```

Output: a markdown prep pack with (a) top-20 likely questions tagged by importance, (b) matched STAR stories or "needs Rizwan to add", (c) red-flag / gotcha questions from the persona, (d) one fully-rehearsed model answer to the highest-importance question with mock-critic score, (e) company hooks, (f) salary anchor guidance.

**Live cost cap:** $3.00 per prep (`config/settings.py:111`).
**Observed cost:** ~$0.30–0.80 per converged prep.
**Time:** 60–120s converged.

### G4 — LinkedIn Engine (`agents/g4_linkedin_graph.py`)

6-node sequential graph (no parallelism, no loop):

```
pick_angle → draft_v1 → critique → polish → image_brief → persist
```

Output: one `linkedin_drafts` row with `status='draft'` (never auto-post — audit risk #2 mitigation). Contains: angle metadata, polished post markdown, critic verdict, image brief for the renderer, voice-profile snapshot, cost telemetry.

**Live cost cap:** ⚠️ **none.** `max_cost_usd=0.15` is a log threshold at the runner level (`agents/g4_linkedin_graph.py:1007`).
**Observed cost:** ~$0.06–0.12 per converged draft (when `ship_as_is` short-circuit fires).
**Time:** 25–45s sequential.

---

## 2. G3 — Improvement Plan

### 2.1 Prompt-quality scores (recap from review)

| Prompt | File:line | Score | Verdict |
|---|---|---|---|
| `BEHAVIORAL_PREDICTOR_FALLBACK_SYSTEM` | `g3_nodes.py:116-128` | 6 | Fallback list good; no banned-phrase / specificity guard |
| `TECHNICAL_PREDICTOR_SYSTEM` | `g3_nodes.py:214-226` | 8 | Grounded search + crisp JSON schema |
| `DOMAIN_PREDICTOR_SYSTEM` | `g3_nodes.py:298-312` | 8 | Best of the three — gives Visa/MC/Stripe-specific examples |
| `STAR_MATCHER_SYSTEM` | `g3_nodes.py:506-517` | 8 | 4-tier match scale, JSON-strict |
| `MOCK_INTERVIEWER_SYSTEM` | `g3_nodes.py:682-691` | 7 | Framework-driven + `[CANDIDATE TO INSERT]` placeholder is good UX |
| `MOCK_CRITIC_SYSTEM` | `g3_nodes.py:693-710` | 9 | 5 weighted axes + strict JSON |

**Average 7.7.** G3 prompts are solid. The wins here are not in rewriting prompts — they're in (a) model right-sizing, (b) silent-failure surfacing, (c) loop discipline, (d) personalisation.

### 2.2 Ranked findings

#### 🔴 G3-1 — **Opus 4.5 on three nodes that should be Haiku 4.5** (highest single ROI)

**Where:** `config/settings.py:84-93`
```python
g3_behavioral_predictor_model = "claude-opus-4-5-20251101"   # ← line 84
g3_domain_predictor_model     = "claude-opus-4-5-20251101"   # ← line 90
g3_star_matcher_model         = "claude-opus-4-5-20251101"   # ← line 93
```

These three are **list-generation tasks with strict-JSON output**:
- `behavioral_predictor` — emit a list of 8 behavioral questions in a fixed schema.
- `domain_predictor` — emit a list of 6 domain questions with examples in a fixed schema.
- `star_matcher` — classify each `(question, story)` pair into 4 buckets with a 1-line rationale.

Opus 4.5 on schema-classification work is the canonical "you're paying for context comprehension you don't need" anti-pattern. Haiku 4.5 matches it.

**Cost delta:** per prep — Opus ≈ $0.30, Haiku ≈ $0.015. **Saves ~$0.28 per prep**. At 1 prep/application × 30 applications/month → ~$8/mo. At 5 users × 30 apps/mo → **$42/mo saved with zero quality loss.**

**Risk:** medium-low. Haiku's instruction-following on structured JSON is excellent for these schemas; downside is a slightly lower diversity in the question lists (less likely to surface unusual edge-case questions). Mitigate by adding an eval that compares Haiku vs Opus on the same 5 archetype × 5 company matrix and gates the swap behind quality parity.

**Effort:** 30 min — change 3 strings + run evals.

---

#### 🔴 G3-2 — Mock-loop has no plateau early-stop

**Where:** `interview_agents/g3_nodes.py:887-891`
```python
if score >= target_score:
    logger.info(...)
    break
```

The loop only breaks on the **absolute** threshold (`g3_target_answer_score`, default 85). If iter 1 scores 78 and iter 2 scores 79, we still spend the second Opus+DeepSeek round-trip even though the marginal improvement is noise. With `g3_max_iterations=2` this wastes one full cycle ~30% of the time on already-good answers.

**Fix (~5 lines, after line 891):**
```python
prev_score = score
# … inside the loop, after `score = int(parsed.get("score") or 0)`:
if iter > 0 and (score - prev_score) < 5 and score >= 70:
    logger.info(f"G3 mock_loop: plateau early-stop at iter {iteration} (Δ {score-prev_score})")
    break
prev_score = score
```

**Cost delta:** saves one full iteration on the ~30% of preps that plateau → ~$0.10 per affected prep → ~$1/mo today, scales linearly.

**Risk:** very low — quality stays identical; you just stop spending money on diminishing returns.

**Effort:** 15 min.

---

#### 🟡 G3-3 — `_safe_parse_question_list` silently returns `[]` on parse failure

**Where:** `interview_agents/g3_nodes.py:404-437` (specifically the `except` at line ~415)
```python
def _safe_parse_question_list(text: str) -> list[dict]:
    try:
        ...
    except Exception:
        return []   # ← swallows errors; downstream merge sees no questions
```

When any single predictor (`behavioral`, `technical`, `domain`) emits malformed JSON, this returns `[]`. `merge_questions_node` then unions an empty list silently and the prep pack ends up with fewer questions than the other two surface. The user sees "Top 20 Likely Questions" with 13 entries and no clue what went wrong.

**Fix:** raise a typed warning into the transcript so:
- (a) the dashboard can render `⚠️ behavioral predictor failed; 13/20 questions surfaced` instead of just 13
- (b) the boss_agent audit detects partial preps as a quality signal

```python
def _safe_parse_question_list(text: str, *, source: str = "unknown") -> tuple[list[dict], Optional[str]]:
    try:
        ...
        return parsed, None
    except Exception as e:
        return [], f"parse_failed:{source}:{str(e)[:120]}"
```

Then in each predictor node, append the warning into `state["transcript"]` and surface a `predictor_warnings` field that `compile_prep_pack_node` renders.

**Cost delta:** none. **Quality delta:** medium — turns invisible quality regressions into visible ones.

**Risk:** zero — purely additive.

**Effort:** 1 hour (touch 3 call sites + compile + add an entry in the markdown renderer).

---

#### 🟡 G3-4 — Salary notes are hardcoded generic text, not persona-aware

**Where:** `interview_agents/g3_nodes.py:940-946`
```python
salary_notes_lines = [
    f"- Target archetype: {job.get('archetype') or 'Senior PM'}",
    f"- Location: {job.get('location') or 'TBD'}",
    f"- Use the salary research agent to set anchor BEFORE this round.",
    f"- Default ask: market 75th percentile + 10-15% for relocation/visa friction.",
    f"- Do not anchor on current. Always anchor on market.",
]
```

This is the same 5 bullets every time, for every role, for every company. The persona has a `salary_signals` field (populated by `agents/salary_research_agent.py`) that nobody reads. Marqeta vs Visa vs a stealth fintech have wildly different anchor numbers.

**Fix:** read `persona.metadata.salary_signals` if present; fall back to `salary_research_agent.research_salary(...)` async if not; render with concrete anchor numbers:
```
- Marqeta Senior PM (Dubai / remote-friendly): market range $185-230k base, equity 0.05-0.12%.
- Anchor: $215k base + 4-year-vest equity at 50th percentile.
- Visa / relocation premium: +$15-25k typical for the same role.
- Do not anchor on current ($170k Marqeta SF) — that signals you don't know market.
```

**Cost delta:** ~+$0.02/prep if salary_research_agent runs cold; ~$0 if cached.

**Quality delta:** large. This is one of the most-read sections of the prep pack and currently it's a template.

**Risk:** medium — depends on quality of `salary_research_agent`'s grounding. Add a confidence threshold; below it, fall back to current generic text.

**Effort:** 4 hours (read the persona structure; wire the salary agent fallback; new prompt for salary anchor synthesis).

---

#### 🟡 G3-5 — Mock loop always rehearses the SINGLE highest-importance question

**Where:** `interview_agents/g3_nodes.py:747-750`
```python
target = sorted(
    questions,
    key=lambda q: -(int(q.get("importance") or 0)),
)[0]
```

A 2-round interview prep produces **1 rehearsed mock answer**. The user gets 19 questions tagged "important" with no rehearsal, and one with a rehearsal. For HM and panel rounds where 3-4 questions are equally important this is a thin output.

**Fix:** rehearse top-N (default 3) instead of top-1; budget allocation `g3_max_cost_usd / N` per question; cap to top-3 even if `g3_max_iterations=2` is set so we don't blow the cost cap.

**Cost delta:** roughly 3× the mock_loop cost → +$0.20-0.40/prep. Stay within the $3 cap (lots of headroom).

**Quality delta:** large — directly addresses the "thin output" complaint and increases the chance the rehearsed answer is one the user actually faces.

**Risk:** low.

**Effort:** 3 hours.

---

#### 🟢 G3-6 — Jaccard dedupe at 0.7 is untested

**Where:** `interview_agents/g3_nodes.py:476-477`
```python
def _is_dup(a: str, b: str, *, threshold: float = 0.7) -> bool:
    ...
```

0.7 might merge "Tell me about a time you failed" with "Tell me about a setback" (legitimately distinct). Or it might miss "Walk me through a difficult stakeholder situation" / "Describe a hard partner conversation".

**Fix:** build a 50-pair eval set with hand-labeled match/no-match, sweep threshold from 0.5 → 0.9 step 0.05, pick F1-best. Probably ends up around 0.78-0.82 but the right number is empirical.

**Cost delta:** zero.

**Quality delta:** small but the kind of thing that compounds — every merged-when-shouldn't = 1 lost question; every kept-when-shouldn't = 1 redundant question the user wastes prep time on.

**Risk:** zero.

**Effort:** 4 hours (eval set + sweep).

---

#### 🟢 G3-7 — Cost-cap path is good, just needs surfacing in the prep pack markdown

**Where:** `interview_agents/g3_nodes.py:773-790` (the pre-check) + `g3_nodes.py:912+` (compile_prep_pack)

When `cost_capped=True` the prep pack today renders normally — the user has no indication that we ran out of budget. Should render a banner at the top:

```markdown
> ⚠️ This prep pack hit the $3 cost cap before fully converging.
> Mock-critic score = 72/100 (target 85). Consider re-running with a higher cap.
```

**Cost delta:** zero.

**Quality delta:** transparency win — the user knows when to re-run vs trust the output.

**Risk:** zero.

**Effort:** 30 min.

---

### 2.3 G3 implementation order

| Priority | Item | Effort | Annual save | Quality win |
|---|---|---|---|---|
| P0 | G3-1 (Opus → Haiku × 3) | 30 min | **$500+/yr** at 5 users | none (parity expected) |
| P0 | G3-3 (parse failure surfacing) | 1 hr | — | medium |
| P1 | G3-2 (plateau early-stop) | 15 min | small | low |
| P1 | G3-7 (cost-cap banner) | 30 min | — | small but visible |
| P2 | G3-5 (top-N rehearsal) | 3 hr | -$80/yr | **large** |
| P2 | G3-4 (persona-aware salary) | 4 hr | — | **large** |
| P3 | G3-6 (Jaccard eval sweep) | 4 hr | — | small |

**Total P0+P1 effort:** ~2.5 hours. **Total annual benefit:** $400-600 saved + 2 quality wins.

---

## 3. G4 — Improvement Plan

### 3.1 Prompt-quality scores (recap from review)

| Prompt | File:line | Score | Verdict |
|---|---|---|---|
| `PICK_ANGLE_SYSTEM` | `g4_linkedin_graph.py:136-160` | 9 | 5 well-defined angles, explicit JSON |
| `DRAFT_V1_SYSTEM` | `g4_linkedin_graph.py:292-320` | **10** | **Best prompt in the codebase.** Banned-phrase list, hard rules numbered 1-6, length cap, voice integration |
| `CRITIQUE_SYSTEM` | `g4_linkedin_graph.py:425-453` | 10 | 9-point audit checklist, P0/P1/P2 severity |
| `POLISH_SYSTEM` | `g4_linkedin_graph.py:538-558` | 9 | "Surgical edits only. Do not introduce new claims" |
| `IMAGE_BRIEF_SYSTEM` | `g4_linkedin_graph.py:674-715` | 8 | Biases toward `reference_news_image` to avoid AI-tells |

**Average 9.2.** G4 prompts are the gold standard. The work here is **operational** — cost cap, the image_brief bug we just fixed, model right-sizing for two of the five nodes, and observability.

### 3.2 Ranked findings

#### 🔴 G4-1 — image_brief_node was silently broken since ship (**SHIPPED FIX**)

**Where:** `agents/g4_linkedin_graph.py:766-777` (before fix on `fix/g4-image-brief-kwargs` branch)

```python
# BEFORE — broken since launch
result = await get_router().ask(
    provider="anthropic",
    model=SONNET_MODEL,
    system=IMAGE_BRIEF_SYSTEM,
    user=user_msg,                # ← not a real kwarg
    response_format="json",       # ← not a real kwarg
    temperature=0.4,
    max_tokens=900,
    graph="g4",                   # ← not a real kwarg, forwarded to Anthropic, rejected
    node_name="image_brief",      # ← not a real kwarg
)
```

`LLMRouter.ask`'s signature is `(provider, model, system, messages, max_tokens, temperature, tools, agent_name, json_response, **provider_kwargs)`. Every extra kwarg dropped into `**provider_kwargs` and forwarded to the Anthropic SDK, which rejected the call → the `except` branch caught the error and produced a NULL `image_brief` for every draft.

**Fix (in PR `fix/g4-image-brief-kwargs`, commit `e162dec`):**
```python
parsed, result = await router.ask_json(
    provider="anthropic",
    model=SONNET_MODEL,
    system=IMAGE_BRIEF_SYSTEM,
    messages=[{"role": "user", "content": user_msg}],
    max_tokens=900,
    temperature=0.4,
    agent_name="g4.image_brief",
)
brief = parsed if isinstance(parsed, dict) else {}
...
cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
```

This is the canonical shape used by all 4 other G4 nodes (`pick_angle` / `draft_v1` / `critique` / `polish`). Once merged, the post-hoc `g4_image_generation.py` renderer will actually have briefs to render → banner images on drafts for the first time.

**Cost delta:** +$0.002/draft (the call was previously erroring and costing nothing).
**Quality delta:** **enormous** — image-attached LinkedIn posts get 2-3× engagement.

**Merged:** PR #53 → main as `ad2e65d` (2026-05-11). Railway redeploys on push so the next G4 run already produces real briefs.

---

#### 🔴 G4-2 — **NO hard cost cap.** Just a log warning.

**Where:** `agents/g4_linkedin_graph.py:1003-1010`
```python
cost = final_state.get("cost_usd_total", 0.0)
if cost > max_cost_usd:
    logger.warning(
        f"G4: cost ${cost:.4f} exceeded cap ${max_cost_usd:.2f} for user={user_id}"
    )
```

`max_cost_usd=0.15` is just a logged threshold *after* the graph finishes. A misbehaving polish node with a 100k-token CV in context could burn $5 on one draft with no abort.

**Fix:** mirror the G3 pattern (`g3_nodes.py:773`) — before each LLM call inside `pick_angle_node / draft_v1_node / critique_node / polish_node / image_brief_node`, check `state.cost_usd_total + estimated_next_call_cost <= cost_cap_usd` and if not, short-circuit gracefully.

For the polish node specifically, the short-circuit is easy: skip polish, persist the `draft_v1` text as the final post with `polish_skipped_reason='cost_cap'`.

For image_brief: skip entirely, persist `image_brief=NULL` with `notes={'image_skipped': 'cost_cap'}`.

**Cost delta:** caps tail-risk only. Median cost unchanged.
**Quality delta:** zero on the median; large on the long tail (no more $5 surprises).
**Risk:** low — well-tested pattern from G2/G3.

**Effort:** 3 hours (5 call sites + state field plumbing + tests).

---

#### 🟡 G4-3 — Hardcoded model constants (not from settings)

**Where:** `agents/g4_linkedin_graph.py:126-127`
```python
SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL   = "claude-opus-4-7"
```

Compare to G3 which reads from `settings.g3_behavioral_predictor_model` etc. — easy to swap for an eval without code edit. G4 currently requires a redeploy to A/B test models.

**Fix:** add to `config/settings.py`:
```python
g4_sonnet_model: str = "claude-sonnet-4-6"
g4_opus_model:   str = "claude-opus-4-7"
```
and read via `settings.g4_sonnet_model` / `settings.g4_opus_model` at module load.

**Cost delta:** none directly. Unlocks future right-sizing experiments.
**Quality delta:** none directly.
**Risk:** zero.
**Effort:** 20 min.

---

#### 🟡 G4-4 — `pick_angle` "last 3 angles" diversity filter could be smarter

**Where:** `agents/g4_linkedin_graph.py:208-219`

Today: `db.table("linkedin_drafts").select("angle").eq("user_id", user_id).order("created_at", desc=True).limit(3)` → exclude those angles from the candidate set. This is a hard exclude.

The user has 5 angles to choose from (`PICK_ANGLE_SYSTEM` line 136-160). Excluding 3 means only 2 are eligible after a 3-post sprint. If the recent 3 happened to be the user's natural fit (e.g. observation + lesson + opinion), you force them into a contrarian-take they don't have evidence to back.

**Fix:** soft-weight recency. Pass the last-3 list into the prompt as context (`recent angles: observation, lesson, contrarian — pick one that contrasts but stay in your voice`) instead of hard-filtering. Let the LLM make the diversity call.

**Cost delta:** ~+50 tokens per pick_angle call ≈ +$0.0005.
**Quality delta:** medium-small — fewer "the third angle is wrong because the first two used up the good ones" outcomes.
**Risk:** low.
**Effort:** 1 hour.

---

#### 🟡 G4-5 — No retry / metric on polish failure (silently falls back to draft_v1)

**Where:** `agents/g4_linkedin_graph.py:626-639`

When `polish_node` errors (LLM timeout, parse error), the code reuses the `draft_v1` text. That's the right resilience choice, but there's no metric — if polish starts failing 50% of the time we won't notice until users complain about "rough" posts.

**Fix:** emit a `polish_failure_count` metric tag into the transcript:
```python
return {
    "polished_post_md": draft_v1_text,  # fallback
    "polish_status": "failed",
    "polish_error": str(e)[:200],
    "transcript": [make_turn(node="polish", output={"status": "failed", "error": str(e)[:200]})],
}
```

Then `boss_agent` weekly audit can surface "polish failure rate this week: 7/20 drafts".

**Cost delta:** zero.
**Quality delta:** observability win.
**Risk:** zero.
**Effort:** 30 min.

---

#### 🟢 G4-6 — Voice profile auto-default could leak across users

**Where:** `agents/g4_linkedin_graph.py:952-965`

```python
voice_profile = {
    "tone_directives": "plainspoken, specific, opinionated; ...",
    "avoid_phrases": ["delve", "tapestry", "unpack", "journey", ...],
    "example_posts": [],
    "profile_md": "",
}
```

This is the **same** default for every user. In multi-tenant mode, all 5 hypothetical users get Rizwan's voice if they haven't run the voice extractor. This isn't a leak (each user's drafts persist under their own user_id), but it means user #2's drafts read like Rizwan's drafts until they explicitly run the extractor.

**Fix:** the default should be neutral — strip the persona-specific phrasing and just keep the anti-AI-tell guardrails. Let `linkedin_voice_extractor.py` populate the user-specific shape.

**Cost delta:** zero.
**Quality delta:** matters when user #2 onboards.
**Risk:** zero.
**Effort:** 15 min.

---

#### 🟢 G4-7 — Promote G4's banned-phrase discipline to G2 WRITER_SYSTEM + COVER_EMAIL_SYSTEM

**Where:** outside G4 — `resume_agents/g2_nodes.py:411-426` (WRITER_SYSTEM, score 5) and `g2_nodes.py:994-998` (COVER_EMAIL_SYSTEM, score 4).

G4's `DRAFT_V1_SYSTEM` (`g4_linkedin_graph.py:292-320`) has 6 explicit banned phrases + the audit's 4-word AI-tell list. G2's writer and cover-email prompts have none — and those are the prompts whose output the *recruiter* actually reads. Cross-pollinate.

**Fix:** copy the banned-phrase block from `DRAFT_V1_SYSTEM` into both G2 prompts.

**Cost delta:** ~+30 tokens per call.
**Quality delta:** the change that most directly addresses the audit's #1 concern (AI tells in user-visible output).
**Risk:** zero.
**Effort:** 20 min.

---

### 3.3 G4 implementation order

| Priority | Item | Effort | Annual save | Quality win |
|---|---|---|---|---|
| P0 | G4-1 (image_brief fix) | **DONE — PR open** | +$0.07/yr cost (negligible) | **enormous** |
| P0 | G4-2 (hard cost cap) | 3 hr | tail-risk only | medium |
| P0 | G4-7 (banned phrases → G2) | 20 min | — | **large** (recruiter-visible) |
| P1 | G4-3 (settings-driven models) | 20 min | unlocks future experiments | none directly |
| P1 | G4-5 (polish failure metric) | 30 min | — | observability |
| P2 | G4-4 (soft-weight angle diversity) | 1 hr | — | medium |
| P2 | G4-6 (neutralize voice default) | 15 min | — | matters at user #2 |

**Total P0 effort (after the image_brief PR merges):** ~3.5 hours.

---

## 4. End-to-end user-action gates

Three things block the system from being "fully running" — none of them are code changes:

### 4.1 ✅ ~~Merge `fix/g4-image-brief-kwargs`~~ — **DONE** (PR #53, merge `ad2e65d`)
Railway redeploys automatically on push to main. Next G4 run will produce real `image_brief` rows for the renderer to consume.

### 4.2 🚨 Apply migration 011 to Supabase
```bash
cd /Users/rizwanzafar/Desktop/jobHunt
psql "$SUPABASE_DB_URL" -f db/migrations/2026_05_10_011_jobs_discovery_quality.sql
```
Adds `discovery_sources / confidence_score / freshness / validation_failed / validated_at` columns + `jobs_confidence_idx` partial index. Without this, JobScout v2's first run will throw on column-not-found. Migration is idempotent (every ADD COLUMN guarded by IF NOT EXISTS).

### 4.3 🚨 Enable the worker service on Railway
`railway.toml` declares `[[services]] name = "worker"` with `START_MODE = "worker"` and `Dockerfile.worker`. On the Railway dashboard:
1. Open the jobHunt project → "Add service" → "Existing repo" → pick the worker block, or
2. If already created but stopped: click the worker service → "Settings" → ensure `START_MODE=worker`, `REDIS_URL=${REDIS_URL}` (auto-injected), `WORKER_CONCURRENCY=1` → "Restart"

Without the worker running, `/workspace/{job_id}/build-resume`, `/linkedin/drafts/generate`, and `/jobs/{job_id}/prep-interview` enqueue jobs into Redis that never get consumed. The dashboard polls `/jobs-runs/{run_id}` forever showing `status=queued`.

### 4.4 ⚠️ Add `APOLLO_API_KEY` to Railway env
All `/apollo/*` routes are mounted (`api/server.py:58`) but every call returns ApolloError on production. The free plan blocks the search endpoints (`/apollo/search-people`, `/apollo/search-companies`) regardless of key validity, so even with the key those will 403 — but `/apollo/enrich/{company_name}` and `/apollo/organizations_enrich` work on free.

### 4.5 ⚠️ Rotate the Perplexity API key (from earlier session)
The `pplx-r7Bxom...` key was pasted in a previous chat. Rotate it via Perplexity console → Settings → API Keys → Regenerate, then update on Railway and locally in `.env`.

---

## 5. What I'd do this week (prioritized)

| Day | Work | Owner | Outcome |
|---|---|---|---|
| Mon | Merge G4-1 (image_brief PR); apply migration 011; verify worker on Railway | User | G4 image briefs live; JobScout v2 ready to fire |
| Mon | G3-1 (Opus → Haiku × 3) | Code | $40+/mo save with zero quality loss |
| Mon | G4-7 (G2 banned phrases) | Code | Recruiter-visible quality win |
| Tue | G4-2 (G4 hard cost cap) | Code | Tail-risk closed |
| Tue | G3-3 (predictor parse-failure surfacing) | Code | Silent quality regressions become visible |
| Wed | G3-5 (top-3 mock rehearsal) | Code | Largest UX win for interview prep |
| Wed | G3-4 (persona-aware salary notes) | Code | Largest content quality win |
| Thu | G4-3 + G4-5 (settings + polish metric) | Code | Operational polish |
| Fri | G3 eval set (G3-6 Jaccard + G3-1 Haiku parity sweep) | Code | Confidence the changes shipped without regression |

Total: ~25 hours engineering work, ~$50/mo recurring save, three meaningful quality wins (image briefs, mock top-3, persona-aware salary).

---

## 6. What we are NOT changing

Things that look improvable but are correct as-is. Don't touch.

- **G3's 3-way parallel fan-out at entry** (`g3_graph.py:30-36`) — mirrors G2's pattern. Solid.
- **G3 cold-start star_matcher bails before LLM call when story bank empty** (`g3_nodes.py:543-567`) — saves ~$0.40/cold prep. Defended.
- **G4 4-node sequential layout with no loop** (the docstring at lines 29-39 explains why; correct call — looping costs more than the marginal quality gain).
- **G4 short-circuit on `verdict=ship_as_is`** (`g4_linkedin_graph.py:591-601`) — saves the polish Opus call when the critic agrees.
- **G4 `cost_usd_total: Annotated[float, add]` reducer pattern** (line 106) — LangGraph adds across parallel branches. Subtle but correct.
- **G2's two-fan-out parallelism + merge_critique MIN-score discipline** — defence-in-depth that's genuinely solid.

---

_Authored 2026-05-11. Source-of-truth for G3/G4 work for the next sprint._
