# jobHunt System Audit — 2026-05-12 (post-session)

> Companion docs (read in order if you're new):
> - [`docs/AGENT_REVIEW_2026_05_11.md`](AGENT_REVIEW_2026_05_11.md) — original per-agent review
> - [`docs/AUDIT_REVIEW_EXTERNAL_2026_05_12.md`](AUDIT_REVIEW_EXTERNAL_2026_05_12.md) — response to external audit
> - [`docs/G3_G4_IMPROVEMENTS_2026_05_11.md`](G3_G4_IMPROVEMENTS_2026_05_11.md) — G3/G4 deep plan
> - [`docs/FLOW_REVIEW_2026_05_11.md`](FLOW_REVIEW_2026_05_11.md) — end-to-end flow

This is the **post-session audit** — after ~12 hours of fixes shipped today. Captures the current state and ranks every remaining gap.

---

## 0. What this session shipped (timeline)

11 PRs merged today (#60–#66 plus follow-ups), all on `origin/main` HEAD `e038f05`:

| # | PR | What it fixed |
|---|---|---|
| 1 | #60 | `upsert_rizwan_profile` user_id + `actions.py` linkedin_drafts.source_company_name 400 |
| 2 | #61 | Safeguard #2 over-rejected ATS-branded URLs (`stripe.com/jobs?gh_jid=…`) |
| 3 | #62 | `upsert_job` / `upsert_company` user_id + v2 columns added to `_JOBS_COLUMNS` |
| 4 | #63 | `create_resume_build` user_id + geo filter (UAE/KSA/Qatar/UK/Singapore) |
| 5 | #64 | Resume PDF + DOCX on-demand render (was 501 before) |
| 6 | #65 | persona_critic node — banned/required/success/failure check |
| 7 | #66 | G2 writer identity lock (prompt + structural) + fabrication scan |
| _untracked_ | — | docs branches, JobScout v2, Geo filter |

Bug archetype shipped 5 times: **`<table>.user_id` was added NOT NULL by migration 001 but the writer was never updated**. Caught in `rizwan_profile`, `jobs`, `companies`, `resume_builds`, and (still open — see §3) `agent_call_log`.

---

## 1. Production state — verified just now

```
Live URLs
  Dashboard:    https://dashboard-eight-theta-t11irr7qdu.vercel.app/today  → 200, 5 cards
  Backend API:  https://jobhunt-production-8ec7.up.railway.app             → 200, 102 routes
                /actions/today                                              → total=5

Data
  jobs                       405 rows  (20 v2 validated · 385 legacy v1)
  jobs on /today             5 cards   (score ≥ 80 + confidence ≥ 50 + open)
  resume_builds              22        (7 converged · 15 stuck "running")
  applications               2
  interview_prep             0         ← G3 never used in prod
  linkedin_drafts            0         ← G4 never used in prod
  jobs_runs                  4 queued  ← worker never ran them
  company_personas           71        (all synthesized · avg version 2.4)
  agent_call_log (24h)       0 rows    ← cost telemetry broken (Bug #7)
```

---

## 2. What's actually working

| Surface | Status |
|---|---|
| Dashboard / Vercel | ✅ Live, 200 OK, force-dynamic, cache-control no-cache |
| Backend API (102 routes) | ✅ Live, healthy |
| `/today` ranked action queue | ✅ Working — 5 cards across kinds |
| JobScout v2 — Perplexity + ATS + 7 safeguards | ✅ Verified end-to-end (Visa + multi-target sweep) |
| Geo filter (UAE/KSA/Qatar/UK/SG) | ✅ Verified (`dropped 28 non-target-geo jobs`) |
| G2 resume builder (graph + topology) | ✅ Compiled, 7/22 converged on prod |
| G2 identity lock + persona_critic | ✅ Just merged — first build after merge will be the test |
| Resume PDF / DOCX download | ✅ On-demand render, no Dockerfile changes |
| Supabase RLS (88 policies on 32 tables) | ✅ Verified by external audit |
| Migration 011 (jobs discovery quality cols) | ✅ Applied |

---

## 3. 🚨 P0 — system is broken / partly broken right now

### 3.1 Worker service not deployed → 4 jobs_runs stuck in `queued` forever

**Evidence:** `jobs_runs` table shows 4 rows in status `queued`, none `succeeded`, none `failed`. Last queued 2026-05-11 21:18. Railway has only the `jobHunt` API service running; the `worker` service declared in `railway.toml` was never created.

**Effect:** `/workspace/{job_id}/build-resume`, `/linkedin/drafts/generate`, `/jobs/{job_id}/prep-interview` ALL enqueue jobs that never execute. UI polls `/jobs-runs/{run_id}` forever showing `queued`. The 0 interview_prep + 0 linkedin_drafts counts in §1 reflect this.

**Fix:** Path A from earlier — Redis plugin + worker service + 14 env vars. ~5 min via Railway CLI, ~$10/mo recurring. **Pending user authorization.**

### 3.2 `agent_call_log` insert silently failing → no cost telemetry (Bug #7)

**Evidence:** `SELECT COUNT(*) FROM agent_call_log WHERE called_at >= NOW() - INTERVAL '24 hours'` returns **0**, despite ~30+ G2 / persona_critic / scout LLM calls today.

**Root cause:** `agents/llm_router.py:554` does a best-effort INSERT into `agent_call_log` wrapped in `try/except Exception: pass`. The table has `user_id NOT NULL` (Bug archetype #5/#6 same shape) — every insert fails with 400, silently swallowed. **Every `/costs/by-agent`, `/costs/daily`, `/costs/health` endpoint returns empty.**

**Fix:** Same one-liner as the other writers — add `user_id` to the payload with env-default. **20 minutes. Highest impact for cost visibility.**

### 3.3 15 of 22 resume_builds stuck in `running` for avg 67 hours

**Evidence:**
```
SELECT status, COUNT(*), AVG(NOW()-created_at) FROM resume_builds GROUP BY status;
running    15    avg 241,816 sec (~67h)
converged  7     avg 334 sec (~5m, $0.95 each)
```

**Root cause:** These are orphans — `pipeline.run()` crashed mid-G2 (bug #1: rizwan_profile user_id, bug #5: upsert_job user_id, bug #6: resume_builds user_id) and never finalized the row. `api/orphan_reaper.py` exists but either isn't running or its threshold is too generous.

**Fix:** (a) one-time SQL backfill to mark these 15 as `failed`, (b) verify orphan_reaper is in the APScheduler config, (c) tighten the "stuck threshold" to 15 min. **~30 min.**

### 3.4 `APOLLO_API_KEY` missing → `/apollo/*` endpoints return ApolloError

**Evidence:** Railway env shows zero Apollo-related vars. Every `/apollo/*` route was mounted (PR #47) but errors at runtime.

**Fix:** Add `APOLLO_API_KEY` to Railway. **User action.**

### 3.5 Stale Perplexity API key (leaked earlier in session) not rotated

**Evidence:** User pasted the key in chat; said "I will rotate all keys once project is live and all issue are fixed".

**Fix:** Rotate via Perplexity console + update Railway env. **User action.**

---

## 4. 🟡 P1 — running but suboptimal / failure latent

### 4.1 G3 Interview Prep has never run in production (0 rows)

**Evidence:** `interview_prep` table is empty. G3 graph code is shipped, endpoints are mounted (`/jobs/{id}/prep-interview`, `/interview-studio/*`), but no one's ever pressed the button. Once the worker (§3.1) is up, G3 will be testable end-to-end.

**Pre-shipping fixes needed before first user-facing run** (from `G3_G4_IMPROVEMENTS_2026_05_11.md`):
- G3-1: Opus → Haiku × 3 (config/settings.py:84-93) — $500+/yr save, 0 quality loss
- G3-3: predictor parse-failure surfacing (silent 13/20 question regressions)
- G3-5: top-3 mock rehearsal (current only rehearses 1 question)
- G3-4: persona-aware salary anchor (currently generic boilerplate)

### 4.2 G4 LinkedIn Engine has never run in production (0 rows)

**Evidence:** `linkedin_drafts` empty. G4 graph + image_brief fix (PR #53) + hard cost cap (commit `469615c`) all shipped, but never invoked. Needs worker (§3.1) to run.

**Pre-shipping fixes still open:**
- G4-3: model constants moved to settings (HIGH-quality A/B testing unlocked)
- G4-7: banned-phrase block applied to G2 WRITER + COVER_EMAIL (G4's gold-standard prompt discipline should propagate to G2's user-visible output)

### 4.3 Outcome conditioning loop hasn't fired in production

**Evidence:** 2 applications, 0 interview_prep, 0 outcome events. `outcome_to_persona.evolve_persona` has never been triggered because no real outcome data exists yet. The system's long-term moat — learning from real callback/rejection signal — has 0 training samples.

**Implication:** Persona quality is purely a function of cold-start synthesis. The 71 personas have avg version 2.4, but those versions came from the synthesizer re-running on new knowledge_chunks, NOT from real-world outcome credits. The wedge isn't yet wedge-y.

**Fix path:** This is a usage / cadence issue, not a code issue. Once user applies + logs outcomes + interviews happen, the loop closes naturally.

### 4.4 `linkedin_drafts.source_company_name` workaround needs a 2nd-look fix

**Evidence:** PR #60 patched `actions.py` to resolve company name via a separate companies-table lookup. That works but adds 1 round-trip per `/today` call. A view-based JOIN or a denormalized `source_company_name` column on `linkedin_drafts` would be cleaner.

**Severity:** Low — works correctly today, just slightly wasteful.

### 4.5 Async footgun in `outcome_to_persona.evolve_persona`

**Evidence (from `AGENT_REVIEW_2026_05_11.md:349`):** Lines 621-630 use `asyncio.run()` from inside what's likely an already-async caller. When the loop is already running, falls through to `asyncio.new_event_loop` — textbook footgun on RQ workers.

**Fix:** Refactor `evolve_persona` to `async def`, await it from callers. ~2 hr.

### 4.6 Persona used as INPUT only, now also as CRITIC — but writer's user message structure is the next-best improvement

**Evidence:** Even with persona_critic shipped, the writer's user message still puts master_resume_md BEFORE JD (good — last fix), but the persona's banned/required vocab is only reachable via `insider_expert_notes`. Direct injection of `persona.ats_keyword_bank.banned` into `WRITER_SYSTEM` (compile-time concat) would close one indirection.

**Fix:** ~30 min, additive. Recommended in agent review §G4-7.

---

## 5. 🟢 P2 — right but inefficient (cost / latency)

### 5.1 Anthropic prompt caching never enabled

**Evidence:** `grep cache_control agents/llm_router.py` returns 0 lines.

**Impact:** ~$30-80/month savings if enabled on G2's system prompts + persona context (external audit P1-1, our flow review §C1). Anthropic gives 90% input-token discount on cache hits. The G2 INSIDER_EXPERT system prompt is 2KB per persona × 71 personas × multiple builds/wk → massive cache-hit potential.

**Fix:** Wrap system prompt content in `cache_control={"type":"ephemeral"}` blocks when length > 1024 tokens. Gate behind `ANTHROPIC_PROMPT_CACHE_ENABLED` env var for staged rollout. **~4 hours.**

### 5.2 Eight Opus → Sonnet/Haiku swaps documented but not all shipped

**Status:** PR `chore/model-right-sizing-2026-05-12` (PR #55, merged `c5928acc`) shipped 9 of the swaps. **Still on Opus:**
- `g2_meta_critic_model` (line 46) — Gemini-2.5-pro is already fine
- `g2_orchestrator_model` (line 50) — Opus 4.5, just judges convergence from JSON (Sonnet/Haiku fine)
- `g2_polisher_model` (line 51) — Opus 4.5, IS load-bearing (final voice gate) but Sonnet might suffice

**Fix:** A/B `g2_orchestrator_model` → Sonnet 4.6 first (~$0.10/build save). Test on 5 builds, compare convergence decisions. **~3 hr.**

### 5.3 ATS critic ensemble runs A+B unconditionally (~$0.10/iter wasted when A alone is great)

**Evidence (from review):** Both `ats_critic_a` (DeepSeek-R1) and `ats_critic_b` (Kimi K2) always run, even when A returns score 95 + 0 fabrications. B's marginal value is zero in those cases.

**Fix:** Adaptive ensemble — run B only if (a) A's score < 60 OR (b) A flagged fabrications OR (c) iteration count <= 1 (early iters need both signals). **~4 hr. Save $20-50/mo at scale.**

### 5.4 Meta-critic dumps full past-transcripts JSON (50K chars) every iteration

**Evidence:** `g2_nodes.py:367` — uncapped JSON dump of `state["past_transcripts"]`.

**Fix:** Summarize past transcripts to `[iteration, node_name, model, score]` tuples; cap history at last 5 turns. Full transcript stays available via DB lookup. **~2 hr. Save $5-15/mo.**

### 5.5 G2 cost cap fires mid-iteration

**Evidence (from review §G2-5):** The cost-cap check at `g2_nodes.py:814-838` happens AFTER writer runs each iter — a single $1.50 writer pass can blow a $1.00 cap. Move to a state-edge guard before writer.

**Fix:** ~3 hr, prevents tail-risk spend.

### 5.6 Database queries lack index hints in hot path

**Evidence (from external audit):** No connection pooling (PgBouncer), no vector-search result cache, no `idx_company_personas_name` confirmed.

**Fix:** Verify indexes exist, add Redis cache layer for persona + RAG results (separate DB index from RQ queue). **~16 hr. Bigger investment, large performance win.**

### 5.7 Serper credits exhausted + queries duplicate per-target

**Evidence:** Live log `"Not enough credits","statusCode":400`. Plus the scout fires 15 Serper queries per target × 71 targets = 1,065 queries on a full sweep. Wasteful.

**Recommendation:** Gate Serper behind `USE_SERPER=0` flag (default off). Perplexity per-target + ATS APIs cover the 71 targets adequately. **~15 min. Saves Serper costs entirely + 30-90s/scan latency.**

---

## 6. 🟢 P3 — strategic investment / multi-tenant readiness

### 6.1 Single-user auth bypass still silent

**Evidence:** `api/context.py::get_current_user` short-circuits to user_001 when `RIZWAN_SINGLE_USER_MODE=1` with no log, no `BIND_USER_ID` enforcement (audit P0-1).

**Fix:** Emit `WARNING AUTH_BYPASS:` per request with IP + path + method. Require `BIND_USER_ID` to be explicitly set (raise 500 if missing). **2 hr.**

### 6.2 No API rate limiting on 102 routes

**Evidence:** From external audit P1-3. None of `/jobs/*/generate-resume`, `/queue/*`, `/auth/*` have rate limits.

**Fix:** `slowapi` middleware with tiered limits per endpoint category. **4 hr.**

### 6.3 365-day signed URL expiry on resume / cover-letter PDFs

**Evidence:** Confirmed live at `db/client.py:249` — `expires_in=60*60*24*365`. External audit P0-5.

**Fix:** 7-day default, 30-day cap. Add `/artifacts/{id}/refresh-url`. **30 min. High-perceived-risk reduction.**

### 6.4 LinkedIn CSV import has no size / MIME cap

**Evidence:** External audit P0-4. `api/network.py` reads entire upload into memory.

**Fix:** 10MB cap + libmagic MIME check + 10k row limit. **3 hr.**

### 6.5 APScheduler missing `coalesce=True` + `max_instances=1`

**Evidence:** `main.py:166-211` — 5 `add_job` calls, none set either. External audit P1-2.

**Fix:** Set `JOB_DEFAULTS` at scheduler config level. **2 hr.**

### 6.6 `ilike()` pattern injection in network search

**Evidence:** `api/network.py:123` — `qb.ilike("full_name", f"%{q}%")` with raw user input. `%` and `_` are wildcards.

**Fix:** Escape `%` / `_` / `\` in user input. **30 min.**

### 6.7 Next.js 14.2.5 has published CVE

**Evidence:** External audit P1-7.

**Fix:** Upgrade to Next.js 15 + retest routes. **8 hr.**

### 6.8 Pydantic v3 prep

**Evidence:** Tests show deprecation warnings on `Field(env=...)` usage.

**Fix:** Audit + migrate. **8 hr.**

---

## 7. Test coverage gap

**Current:** 145 tests pass / 2 skipped across 6 test files
```
tests/test_cost_alerter.py
tests/test_g2_graph.py
tests/test_g3_graph.py
tests/test_job_validation.py
tests/test_llm_router.py
tests/test_persona_synthesizer.py
```

**Missing test files (high-impact gaps):**

| Module | Risk if untested |
|---|---|
| `api/actions.py` | The `/today` API contract — already broke once with linkedin_drafts.source_company_name |
| `api/workspace.py` | PDF/DOCX render path, build-resume contract |
| `agents/llm_router.py` | The cost-log callback (Bug #7 would have been caught by a unit test) |
| `db/client.py::upsert_*` | All the user_id NOT NULL bugs would have been caught by a single round-trip test |
| `agents/persona_news_check.py` | Recency cron correctness |
| `agents/outcome_to_persona.py::evolve_persona` | The async footgun + score-delta math |
| `resume_agents/g2_io.py::create_resume_build` | Bug #6 would have been caught |
| `api/network.py` | LinkedIn CSV upload safety + ilike escape |
| `agents/job_scout_agent.py::_is_target_geo` | Geo filter regression-safe |
| `dashboard/src/lib/api.ts` | Frontend API surface contracts |

**Recommendation:** Set a 30% module-coverage floor (currently ~10%). Highest-ROI tests are the `upsert_*` round-trip tests in `db/client.py` — they would have prevented 5 of today's 6 bugs single-handedly. **~24 hr to land coverage for the top 5 risky modules.**

---

## 8. The "still bad" list — symptoms the user is feeling

The user has reported each of these in the session. Mapping symptom → cause → fix:

| Symptom user reported | Root cause | Fixed by | Remaining work |
|---|---|---|---|
| "I can see old jobs on /today" | PR D filter (confidence ≥ 50) + no v2 rows | PR #58 + JobScout v2 + Bug #5 | None — fixed |
| "Mobile number missing, fabricated facts on resume" | Writer hallucinated; critics blind to master CV | PR #66 (identity lock) + PR #65 (persona critic) | Test next G2 build, iterate if needed |
| "Resume quality below average despite 5-LLM ensemble" | Optimization was on wrong objective (JD-fit, not source-fidelity) | PR #65 + #66 | FACT_VERIFIER LLM node for body-claim verification (next ship) |
| "PDF download not working" | G2 export hardcoded `resume_pdf_url=None`, endpoint 501'd | PR #64 (on-demand render) | None — fixed |
| "Jobs are US not UAE/KSA/UK/Singapore" | No geo filter, scoring weights too soft | PR #63 (geo filter + scoring re-tier) | None — fixed |
| "I shared 235 resumes but only 5 sections in profile" | `profile_build/` pipeline ingested fewer sections than user has on disk | not fixed | Re-run `profile_build/03_build_master_profile.py` with wider scope, OR confirm rizwan_profile is just embedding cache and master is elsewhere |
| "Heavy on cost, persona's role is unclear" | Persona was input-only, not critic | PR #65 (persona_critic node) | Direct persona injection into WRITER_SYSTEM (next ship, ~30 min) |
| "Simpaisa is in Dubai (wrong location)" | Writer fabricated to match JD | PR #66 (structural splice + prompt) | Verify on next G2 build |

---

## 9. Highest-ROI ordered improvement list (next 2 weeks)

| Rank | Item | Effort | Recurring impact | One-time impact |
|---|---|---|---|---|
| 1 | **Deploy Railway worker + Redis** (§3.1) | 5 min CLI | ~$10/mo infra | Unblocks G3, G4, async G2, JobScout v2 |
| 2 | **Fix Bug #7 (agent_call_log user_id)** (§3.2) | 20 min | 0 | Cost telemetry restored; `/costs/*` endpoints work |
| 3 | **Backfill orphan resume_builds + tighten reaper** (§3.3) | 30 min | Prevents new orphans | Clean dashboard |
| 4 | **365-day URL → 7-day** (§6.3) | 30 min | 0 | Major security win |
| 5 | **Add APOLLO_API_KEY + rotate Perplexity key** (§3.4, §3.5) | 5 min user | $0 | Apollo features come online |
| 6 | **Test G2 with persona_critic + identity lock** (§8) | 30 min trigger + check | 0 | Validates today's work |
| 7 | **Enable Anthropic prompt caching** (§5.1) | 4 hr | $30-80/mo save | 30-40% lower G2 cost |
| 8 | **slowapi rate limiting** (§6.2) | 4 hr | Prevents abuse | Multi-tenant prereq |
| 9 | **APScheduler `coalesce=True` + `max_instances=1`** (§6.5) | 2 hr | Prevents misfire storms | Operational safety |
| 10 | **`ilike()` escape helper** (§6.6) | 30 min | 0 | Eliminates pattern-injection |
| 11 | **Disable Serper (gated behind flag)** (§5.7) | 15 min | Stops wasted credits | Cleaner logs |
| 12 | **G3-1: Opus→Haiku × 3 in G3 config** (§4.1) | 30 min | ~$40/mo save | Zero quality loss expected |
| 13 | **Direct persona.banned injection into WRITER_SYSTEM** (§4.6) | 30 min | 0 | One less indirection from persona → writer |
| 14 | **Verify orphan_reaper is in APScheduler** (§3.3) | 30 min | Prevents future orphans | Operational |
| 15 | **db/client.py upsert_* round-trip tests** (§7) | 4 hr | Prevents Bug #1-7 recurrence | Highest-ROI test investment |

Sum: ~25 hr of engineering + ~$10/mo new infra + several user-action gates. Cumulative impact: **~$80-150/mo recurring save** + restored telemetry + multi-tenant readiness + 3 P0 production gaps closed.

---

## 10. Strategic positioning — what makes this system different

Looking at the system as a whole, the genuine moats (post-fix):

1. **Outcome-conditioned persona evolution** (`agents/outcome_to_persona.py`) — the only piece of the stack that learns from real callback signal. Currently has 0 outcomes — but as users log applied/interviewed/offered/ghosted, persona_version bumps and ATS bank evolves. Nobody else does this.
2. **Multi-source RAG persona pipeline** (Apify + Perplexity + Apollo) — three independent grounding sources cross-checked. Most career tools rely on one (often just a search engine).
3. **Per-target archetype-aware Perplexity discovery** — JobScout v2's 71-target × specific-archetype approach is sharper than generic Indeed/LinkedIn scraping.
4. **7 hallucination safeguards in scout** — URL existence, domain whitelist, JD fingerprint, expiry phrase, cross-source confirmation, freshness, archetype filter — well above industry baseline.
5. **Cite:knowledge_id breadcrumbs** — every G2 bullet can trace back to the company_knowledge row that supported it. Enables outcome credit assignment.
6. **persona_critic + identity lock** (today) — the only stack I'm aware of that has a per-company "would this resume read as written-by-an-insider" gate.

The first 3 quarters of 2026 work should be: 1) close the operational gaps (worker, telemetry, prompt cache), 2) start collecting real outcomes to make the persona evolution wedge actually learn, 3) test the new G2 quality gates on real builds.

---

_Audit compiled 2026-05-12, post-session. Source-of-truth for next sprint planning._
