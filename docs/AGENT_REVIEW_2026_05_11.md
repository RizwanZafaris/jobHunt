# Agent + Prompt Review — Every Major Agent Except JobScout (2026-05-11)

**Scope:** every load-bearing agent, graph, and prompt in `agents/`,
`resume_agents/`, `interview_agents/`, `evals/`, and `config/` EXCEPT
`agents/job_scout_agent.py` (already reviewed; v2 rewrite authorized).
~10 k LOC of Python read line-by-line.

**Companion docs read first:**
[`docs/FLOW_REVIEW_2026_05_11.md`](FLOW_REVIEW_2026_05_11.md) ·
[`docs/AUDIT_2026_05_10.md`](AUDIT_2026_05_10.md) ·
[`docs/AUDIT_360_SYNTHESIS.md`](AUDIT_360_SYNTHESIS.md) ·
[`docs/SPRINT_1_STATUS.md`](SPRINT_1_STATUS.md).
Findings in those docs are NOT duplicated here unless reframed with a
new pin or sharper severity.

---

## 1. Executive summary

Five things stand out across the whole agent layer:

1. **Opus is the default for almost everything**, including pure routing
   / classification / merge / orchestrator steps that absolutely do not
   need it. Eight load-bearing nodes default to `claude-opus-4-5` or
   `claude-opus-4-7` where Sonnet (or Haiku) would match accuracy and
   cut cost 5-20× — see §5 right-sizing table.
   ([`config/settings.py:26-100`](../config/settings.py),
   [`agents/interview_tutor.py:43`](../agents/interview_tutor.py),
   [`agents/resume_edit_assistant.py:70-81`](../agents/resume_edit_assistant.py),
   [`agents/outcome_to_persona.py:76`](../agents/outcome_to_persona.py),
   [`agents/intro_email_agent.py:47`](../agents/intro_email_agent.py),
   [`agents/linkedin_voice_extractor.py:35`](../agents/linkedin_voice_extractor.py))
2. **Cost caps are advertised at the worker level but enforced only
   inside G2's orchestrator and G3's mock-loop.** Two recovery vectors
   are missing: (a) the worker-side cumulative cap that AUDIT §4
   Critical-2 calls for, (b) any cap at all for G4 LinkedIn / persona
   deep-research / intro_email / cost_alerter / persona_synthesizer.
   `agents/g4_linkedin_graph.py:1003-1007` logs a warning but keeps
   going on over-budget, and `agents/persona_deep_research.py` has no
   cap at all. ([`api/worker.py:103-176`](../api/worker.py))
3. **Prompt-quality is uneven.** The strongest prompts on the system
   (G4 draft_v1, resume_edit_assistant.quick_tweak, evals/judge,
   interview_tutor) have explicit banned-phrase lists, specificity
   guards, and schema enforcement. The weakest (`BossAgent._generate_digest`,
   `RizwanAgent.generate_cover_email`, `CompanyAgent.build_resume_as_recruitment_expert`,
   `PersonaSynthesizer.SYNTHESIZER_SYSTEM`) have no anti-AI-tell
   discipline, no max-token guard tied to context, and accept any prose
   the model wants to emit. The audit's "delve / tapestry / unpack /
   journey" rule is enforced in G4 + voice_extractor + resume_edit
   but NOT in G2 writer / polisher / cover_email — those nodes are the
   ones whose output the user actually sees. ([`resume_agents/g2_nodes.py:411-426`](../resume_agents/g2_nodes.py),
   [`agents/boss_agent.py:195-220`](../agents/boss_agent.py))
4. **Three modules have outright wrong async discipline.**
   `agents/outcome_to_persona.py:621-630` calls `asyncio.run()` from
   inside what is likely an already-async caller (the api endpoint
   wraps it); when the loop is already running we fall through to
   `asyncio.new_event_loop` which is a textbook footgun on RQ workers
   — the new loop has no shared connections, no SDK clients, and stack
   traces become unreadable. Same pattern is repeated in
   `evolve_persona`. `agents/referral_graph.py:_build_graph` does
   sync Supabase calls (`.execute()` is blocking I/O) inside what's
   called by an async FastAPI handler. `agents/job_validator.py:135-168`
   uses `httpx.Client` (sync) instead of `AsyncClient` and is
   intended to run on an APScheduler cron alongside other async work.
5. **The eval harness model is wrong.** `evals/judge.py:37` hard-codes
   `JUDGE_MODEL = "claude-opus-4-5"` (no version suffix), but the rest
   of the codebase uses `claude-opus-4-5-20251101`. `PRICING_PER_1M`
   ([`agents/llm_router.py:43-73`](../agents/llm_router.py)) has both
   entries, but a prefix-match means the prefix `claude-opus-4-5` is
   ambiguous when newer dated revisions ship — pin it explicitly.
   Worse, the judge prompt has no cache_control hint despite being
   the longest reused prompt in the system (≈2 kB, run on every PR).

The system is otherwise architecturally sound. Multi-tenant, durable,
ATS-graded, and outcome-conditioned. What's missing is mostly cost
discipline and a third of the prompts that need to be rewritten
before this can be sold to user #2.

---

## 2. Per-agent review

### 2.1 G2 Resume Builder Graph (`resume_agents/`)

**Purpose:** 12-node LangGraph that takes (job_id, company) and emits a
tailored resume + cover email via a 5-LLM ensemble with critic-merge,
cost cap, and persona quality gate.

**Current models + cost.** Per-build cost cap $5.00
([`config/settings.py:64`](../config/settings.py)),
observed at $0.97 (Marqeta) — $4.99 (cost_capped Adyen). Six LLMs:
Gemini 2.5 Pro (insider_expert, meta_critic) ·
Claude Opus 4.5 (advocate, writer, orchestrator, polisher, cover_email) ·
DeepSeek-R1 (ats_critic_a) · Kimi K2.5 (ats_critic_b). 8 LLM calls
per converged build minimum, 14+ on max iterations.

**Prompt-quality assessment.**
- `INSIDER_EXPERT_FALLBACK_SYSTEM` ([`g2_nodes.py:101-109`](../resume_agents/g2_nodes.py)):
  Crisp, measurable (≥70% quantification, ≥70% ATS coverage, "no
  responsible for"). Good.
- `ADVOCATE_SYSTEM` ([`g2_nodes.py:265-276`](../resume_agents/g2_nodes.py)):
  Adequate — "be collaborative but firm" is fluffier than the rest.
  No banned-phrase list. **Score 6.**
- `META_CRITIC_SYSTEM` ([`g2_nodes.py:325-340`](../resume_agents/g2_nodes.py)):
  Crisp. The "if past transcripts are sparse, fall back to 1-3
  generic ATS warnings" instruction is exactly the right discipline.
  **Score 8.**
- `WRITER_SYSTEM` ([`g2_nodes.py:411-426`](../resume_agents/g2_nodes.py)):
  This is the most user-visible output, yet it has NO banned-phrase
  list. "NEVER use first person, NEVER use 'responsible for'" is
  the entire anti-AI-tell discipline. Compare to G4's draft_v1_system
  which has 6 explicit banned phrases. **Score 5.**
- `ATS_CRITIC_SYSTEM` ([`g2_nodes.py:519-539`](../resume_agents/g2_nodes.py)):
  Excellent. Specifies exact JSON schema, scoring dimensions, "Strict
  JSON only. No prose." **Score 9.**
- `ORCHESTRATOR_SYSTEM` ([`g2_nodes.py:778-791`](../resume_agents/g2_nodes.py)):
  Tight. 4 lines, 3 rules, exact JSON. **Score 9.**
- `POLISHER_SYSTEM` ([`g2_nodes.py:918-930`](../resume_agents/g2_nodes.py)):
  Adequate but weighting is opaque ("fit 40%, ats 20%, impact 20%,
  narrative 10%, polish 10%") with no definition of what each axis
  means. **Score 6.**
- `COVER_EMAIL_SYSTEM` ([`g2_nodes.py:994-998`](../resume_agents/g2_nodes.py)):
  4 lines, generic. No banned-phrase list, no length guard, no anti-
  AI-tell discipline. **Score 4.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 1 | High | `WRITER_SYSTEM` has no banned-phrase list. G4 has one; G2 should mirror it. The writer is the load-bearing prompt. | [`g2_nodes.py:411-426`](../resume_agents/g2_nodes.py) |
| 2 | High | `COVER_EMAIL_SYSTEM` uses the polisher model (Opus 4.5) for what is conceptually a Sonnet-class task. 4-7 sentences of value-first prose does not require Opus. | [`g2_nodes.py:994-1036`](../resume_agents/g2_nodes.py) |
| 3 | High | `_run_ats_critic` retry doubles `max_tokens` (line 585) — the audit already flagged this as wasteful (C10). Confirmed unfixed. | [`g2_nodes.py:576-621`](../resume_agents/g2_nodes.py) |
| 4 | Medium | `meta_critic_node` dumps the full past-transcripts JSON to 50k chars (line 367). Audit C6 — confirmed unfixed. | [`g2_nodes.py:367`](../resume_agents/g2_nodes.py) |
| 5 | Medium | The cost-cap check ([`g2_nodes.py:814-838`](../resume_agents/g2_nodes.py)) happens AFTER the writer node runs each iteration, which means a single $1.50 writer pass can blow a $1.00 cap without the orchestrator getting a chance to gate. Move the pre-iteration check to a state-edge guard. | [`g2_graph.py:54-57`](../resume_agents/g2_graph.py) |
| 6 | Medium | `polisher_node` (Opus 4.5, 4500 max_tokens, line 954) re-emits the full resume markdown every iteration. With prompt caching off (audit C1) this is the single most expensive node per build. | [`g2_nodes.py:933-988`](../resume_agents/g2_nodes.py) |
| 7 | Medium | `entry_node`'s `load_past_transcripts(n=settings.g2_meta_critic_lookback)` (default 5) at [`g2_nodes.py:61-63`](../resume_agents/g2_nodes.py) pulls full agent_transcript JSONB for the last 5 builds — can be megabytes. Cap to last 3 turns per build. | [`g2_io.py`](../resume_agents/g2_io.py) |
| 8 | Low | Hardcoded "fintech / payments" framing in `INSIDER_EXPERT_FALLBACK_SYSTEM` (line 101) — non-fintech personas (if anyone ever uses this for SaaS / hardware / consulting) will get bad cold-start advice. Make it persona-domain aware. | [`g2_nodes.py:101-109`](../resume_agents/g2_nodes.py) |
| 9 | Low | `_canonicalize_company` ([`g2_run.py:182-216`](../resume_agents/g2_run.py)) does a full SELECT on `companies WHERE is_target=true` on every invocation. Cache it for the process lifetime — companies don't churn. | [`g2_run.py:194-205`](../resume_agents/g2_run.py) |
| 10 | Low | `export_node`'s pandoc subprocess ([`g2_nodes.py:1168-1170`](../resume_agents/g2_nodes.py)) uses a 30s timeout. On a slow Railway worker this can occasionally trip even on 2-page resumes — bump to 60s. | [`g2_nodes.py:1168`](../resume_agents/g2_nodes.py) |

**What's good and shouldn't change.**
- Two-fan-out parallelism (entry → expert+advocate, writer → critic_a+critic_b) is the right shape; LangGraph waits on the join cleanly.
- `merge_critique_node` ([`g2_nodes.py:699-772`](../resume_agents/g2_nodes.py))
  takes the MIN score, dedupes fixes, and unions missing_keywords —
  defence in depth that is genuinely solid.
- The cite:knowledge_id breadcrumb dual-encoded as both regex AND
  structured `cited_knowledge_ids` ([`g2_nodes.py:232-252`](../resume_agents/g2_nodes.py))
  is best-in-class. The audit's praise was warranted.
- `_run_ats_critic` sentinel-on-error pattern
  ([`g2_nodes.py:590-637`](../resume_agents/g2_nodes.py)) lets the other
  critic carry the merge. Worth keeping.

---

### 2.2 G3 Interview Prep Graph (`interview_agents/`)

**Purpose:** 7-node LangGraph that takes (application_id, round_type) and
emits a prep pack (top 20 questions, matched STAR stories, mock answer +
critic score, company hooks, salary notes).

**Current models + cost.** Per-prep cost cap $3.00
([`config/settings.py:111`](../config/settings.py)). 5 LLM nodes,
~$0.30-0.80 per converged prep. Claude Opus 4.5 default across
behavioral / domain / star_matcher / mock_interviewer. DeepSeek-R1
for mock_critic. Gemini 2.5 Pro for technical_predictor (grounding).

**Prompt-quality assessment.**
- `BEHAVIORAL_PREDICTOR_FALLBACK_SYSTEM` ([`g3_nodes.py:116-128`](../interview_agents/g3_nodes.py)):
  Has a clear fallback list but no banned-phrase / specificity guard.
  **Score 6.**
- `TECHNICAL_PREDICTOR_SYSTEM` ([`g3_nodes.py:214-226`](../interview_agents/g3_nodes.py)):
  Uses grounded search. Crisp instruction (concrete > "design a
  system") and explicit JSON shape. **Score 8.**
- `DOMAIN_PREDICTOR_SYSTEM` ([`g3_nodes.py:298-312`](../interview_agents/g3_nodes.py)):
  Best of the three predictors — gives explicit category examples
  (Visa/MC = interchange/scheme rules; Stripe/Adyen = settlement/MoR).
  **Score 8.**
- `STAR_MATCHER_SYSTEM` ([`g3_nodes.py:506-517`](../interview_agents/g3_nodes.py)):
  Excellent. 4-tier match scale, JSON-strict. **Score 8.**
- `MOCK_INTERVIEWER_SYSTEM` ([`g3_nodes.py:682-691`](../interview_agents/g3_nodes.py)):
  Solid (STAR+R, framework-driven, `[CANDIDATE TO INSERT: ...]`
  placeholder). **Score 7.**
- `MOCK_CRITIC_SYSTEM` ([`g3_nodes.py:693-710`](../interview_agents/g3_nodes.py)):
  Five axes with weights, exact JSON. Good. **Score 9.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 11 | High | `behavioral_predictor` uses Opus 4.5 ([`config/settings.py:84`](../config/settings.py)). This is a list-generation task with structured JSON output — Haiku 4.5 will match it for ~20× less cost. Same with `domain_predictor` (line 90) and `star_story_matcher` (line 93). | [`config/settings.py:84-93`](../config/settings.py) |
| 12 | Medium | `mock_interview_loop_node` ([`g3_nodes.py:713-906`](../interview_agents/g3_nodes.py)) is a single-node internal loop. This is the right call architecturally, but the inner loop has no early-stop on plateau — if iter 1 scores 75 and iter 2 scores 76, it still runs. Add `if iter > 0 and score_delta < 5: break`. | [`g3_nodes.py:887-891`](../interview_agents/g3_nodes.py) |
| 13 | Medium | `_safe_parse_question_list` ([`g3_nodes.py:404-437`](../interview_agents/g3_nodes.py)) silently returns `[]` on parse failure. The downstream `merge_questions_node` will quietly produce a 0-question prep pack. Should raise / surface a flag. | [`g3_nodes.py:415`](../interview_agents/g3_nodes.py) |
| 14 | Medium | `compile_prep_pack_node` ([`g3_nodes.py:912-1048`](../interview_agents/g3_nodes.py)) hardcodes `salary_notes_lines` ([`g3_nodes.py:940-946`](../interview_agents/g3_nodes.py)) — generic, not company-specific, not persona-aware. Either pull from persona's salary_signals or call salary_research_agent. | [`g3_nodes.py:940-946`](../interview_agents/g3_nodes.py) |
| 15 | Low | The Jaccard dedupe at 0.7 threshold ([`g3_nodes.py:476-477`](../interview_agents/g3_nodes.py)) — empirically this should be tested; 0.7 may merge legitimately distinct questions ("Tell me about a failure" vs "Tell me about a setback"). Build an eval set. | [`g3_nodes.py:476`](../interview_agents/g3_nodes.py) |
| 16 | Low | `mock_interview_loop` always rehearses the SINGLE highest-importance question ([`g3_nodes.py:747-750`](../interview_agents/g3_nodes.py)). For a 2-round prep this means the candidate gets 1 mock answer total. Pick the top-3 importance and rotate. | [`g3_nodes.py:747`](../interview_agents/g3_nodes.py) |

**What's good and shouldn't change.**
- Three-way parallel fan-out at entry mirrors G2's pattern.
- `merge_questions_node` source-weighting (technical > domain >
  behavioral; [`g3_nodes.py:466`](../interview_agents/g3_nodes.py))
  is a principled tiebreaker.
- Cold-start star_story path bails before the LLM call when story
  bank is empty ([`g3_nodes.py:543-567`](../interview_agents/g3_nodes.py))
  — saves ~$0.40 per cold-start prep. Defended.

---

### 2.3 G4 LinkedIn Engine (`agents/g4_linkedin_graph.py`)

**Purpose:** 6-node graph that picks an angle, drafts a post, critiques,
polishes, briefs the image, persists. Output is always `status='draft'`
— no auto-post (audit risk #2 mitigation).

**Current models + cost.** Cost target $0.15 per draft (line 925);
no hard cap, only a warning at line 1003-1007. Sonnet (pick_angle,
critique, image_brief) + Opus 4.7 (draft_v1, polish).

**Prompt-quality assessment.**
- `PICK_ANGLE_SYSTEM` ([`g4_linkedin_graph.py:136-160`](../agents/g4_linkedin_graph.py)):
  Specific, measurable, 5 well-defined angles, explicit JSON. **Score 9.**
- `DRAFT_V1_SYSTEM` ([`g4_linkedin_graph.py:292-320`](../agents/g4_linkedin_graph.py)):
  Best prompt in the codebase. Explicit banned-phrase list (6 phrases),
  hard rules numbered 1-6, length cap, voice profile integration,
  exact JSON shape. **Score 10.**
- `CRITIQUE_SYSTEM` ([`g4_linkedin_graph.py:425-453`](../agents/g4_linkedin_graph.py)):
  9-point audit checklist, P0/P1/P2 severity, exact JSON. **Score 10.**
- `POLISH_SYSTEM` ([`g4_linkedin_graph.py:538-558`](../agents/g4_linkedin_graph.py)):
  Tight, prescriptive. "Surgical edits only. Do not introduce new claims".
  **Score 9.**
- `IMAGE_BRIEF_SYSTEM` ([`g4_linkedin_graph.py:674-715`](../agents/g4_linkedin_graph.py)):
  Reasonably specific. Biases toward `reference_news_image` to avoid
  AI-generated tells. **Score 8.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 17 | High | NO hard cost cap. `max_cost_usd` is just a log threshold at the runner level ([`g4_linkedin_graph.py:1003-1007`](../agents/g4_linkedin_graph.py)). A bad LLM call could burn $5 on one draft with no abort. Add the G2-style pre-call check. | [`g4_linkedin_graph.py:925, 1003`](../agents/g4_linkedin_graph.py) |
| 18 | Medium | `image_brief_node` ([`g4_linkedin_graph.py:739-822`](../agents/g4_linkedin_graph.py)) uses `router.ask(provider="anthropic", model=SONNET_MODEL, ..., user=user_msg, response_format="json", ..., graph="g4", node_name="image_brief")` — but `LLMRouter.ask`'s signature is `(provider, model, system, messages, ...)`. The `user=`, `response_format=`, `graph=`, `node_name=` kwargs don't exist; the call ROUTES THROUGH `**provider_kwargs` and Anthropic will reject `graph=` and `node_name=` because they aren't valid Anthropic params. **This node has never run successfully** unless there's silent fallback to the except branch (line 809) which produces a None image_brief. | [`g4_linkedin_graph.py:766-777`](../agents/g4_linkedin_graph.py) |
| 19 | Medium | `pick_angle` ([`g4_linkedin_graph.py:218`](../agents/g4_linkedin_graph.py)) reads the last 3 angles for diversity but the user_id filter is correct, no cross-tenant leak. The `.limit(3)` is fine but you might want to weight by recency-decay rather than hard exclude — Phase 2 polish. | [`g4_linkedin_graph.py:208-219`](../agents/g4_linkedin_graph.py) |
| 20 | Low | Hardcoded `SONNET_MODEL = "claude-sonnet-4-6"` and `OPUS_MODEL = "claude-opus-4-7"` at lines 126-127 rather than pulling from settings. Hard to swap for eval without code edit. | [`g4_linkedin_graph.py:126-127`](../agents/g4_linkedin_graph.py) |
| 21 | Low | No retry on `polish_node` failure — falls back to draft_v1 verbatim ([`g4_linkedin_graph.py:626-639`](../agents/g4_linkedin_graph.py)). Acceptable but should log a metric so we can detect failure-rate trends. | [`g4_linkedin_graph.py:626`](../agents/g4_linkedin_graph.py) |

**What's good and shouldn't change.**
- 4-node sequential layout with explicit reasoning (lines 29-39) for
  why not loop. Solid call.
- Short-circuit on `verdict == "ship_as_is"` ([`g4_linkedin_graph.py:591-601`](../agents/g4_linkedin_graph.py))
  saves the polish Opus call when the critic agrees. Good cost discipline.
- Voice profile auto-defaults ([`g4_linkedin_graph.py:952-965`](../agents/g4_linkedin_graph.py))
  so a brand-new user can generate a draft without first running the
  voice extractor. Good DX.
- The `cost_usd_total: Annotated[float, add]` ([`g4_linkedin_graph.py:106`](../agents/g4_linkedin_graph.py))
  reducer pattern — LangGraph adds across parallel branches. Subtle
  but correct.

---

### 2.4 PersonaDeepResearch (`agents/persona_deep_research.py`)

**Purpose:** Cold-start persona builder. Fires 8 Apify queries → aggregates
markdown → Gemini long-context synthesis → writes 13 knowledge sections +
persona row.

**Current models + cost.** Apify $0.05-0.15 + Gemini 2.5 Pro ~$0.05 =
~$0.20/company × 70 ≈ $14 one-time. Daily news refresh ~$0.005/company
× 70 × 30 ≈ $10/month.

**Prompt-quality assessment.**
- `SYNTH_SYSTEM` ([`persona_deep_research.py:297-309`](../agents/persona_deep_research.py)):
  Strong. "No generic fintech boilerplate. If you write 'scale +
  impact' you've failed". Quality self-assessment tier (high/medium/low)
  is concrete. **Score 9.**
- `SYNTH_USER_TEMPLATE` ([`persona_deep_research.py:311-348`](../agents/persona_deep_research.py)):
  Excellent. Field-by-field schema with format hints. **Score 9.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 22 | High | **NO cost cap.** `synthesize_persona` ([`persona_deep_research.py:366-408`](../agents/persona_deep_research.py)) makes an 8000-token Gemini call with no abort. If Gemini's pricing changes or the synth times out and retries, a single company's persona build can run unbounded. | [`persona_deep_research.py:380-401`](../agents/persona_deep_research.py) |
| 23 | High | The synth has a single retry path via fallback to Claude ([`persona_deep_research.py:382-407`](../agents/persona_deep_research.py)), but if BOTH Gemini AND Claude fail the synth raises — the company is left with NO persona row and the Apify scrape data is lost. Should at minimum persist the scraped markdown as raw `company_knowledge` rows. | [`persona_deep_research.py:408`](../agents/persona_deep_research.py) |
| 24 | Medium | The fallback model is `settings.boss_agent_model` (Claude Opus 4.5). Should be Sonnet for this — Opus pricing on a 80k-token input is $1.20 just for the synthesis fallback. | [`persona_deep_research.py:385`](../agents/persona_deep_research.py) |
| 25 | Medium | `gather_research` ([`persona_deep_research.py:168-208`](../agents/persona_deep_research.py)) fires all 8 Apify queries in parallel with no concurrency limit. Apify rate-limits at 30 requests/minute on free tier; bursting 8 simultaneously can trip the limiter. Add `asyncio.Semaphore(4)`. | [`persona_deep_research.py:191-192`](../agents/persona_deep_research.py) |
| 26 | Low | The Apify metadata extraction at `metadata.url`, `searchResult.url` ([`persona_deep_research.py:204-205`](../agents/persona_deep_research.py)) is brittle — the actual Apify response shape is nested under `metadata: {url: ...}`, not dotted keys. Test with `assert sources[0].url != ""`. | [`persona_deep_research.py:204`](../agents/persona_deep_research.py) |

**What's good and shouldn't change.**
- The 8-query bundle covers exactly the right surface (overview, news,
  funding, culture, tech, leadership, competitors, interview process)
  — comprehensive without being wasteful.
- Parallel embedding via `asyncio.gather` ([`persona_deep_research.py:466`](../agents/persona_deep_research.py))
  is the right shape; 13 sequential round-trips would be slow.
- Batch upsert with per-row fallback ([`persona_deep_research.py:486-515`](../agents/persona_deep_research.py))
  is exactly the resilience pattern this needs.

---

### 2.5 PersonaSynthesizer (`agents/persona_synthesizer.py`)

**Purpose:** Weekly refresh from outcomes + transcripts. Bumps
`persona_version`. Runs Sunday 03:00 GST.

**Current models + cost.** Gemini 2.5 Pro (long context) with
Claude Opus 4.5 fallback. Audit C4 estimated $40-80/month savings if a
skip-gate is added.

**Prompt-quality assessment.**
- `SYNTHESIZER_SYSTEM` ([`persona_synthesizer.py:59-92`](../agents/persona_synthesizer.py)):
  Solid rules ("If 3+ resumes that got interviews share a bullet
  structure → that's a success_pattern"). Strict JSON schema.
  **Score 7.** Missing: no banned-phrase list, no max-token guard
  on the LLM call.

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 27 | High | The skip-gate at [`persona_synthesizer.py:224-235`](../agents/persona_synthesizer.py) only fires when there's an `existing` persona AND `last_synthesized_at`. **A cold-start company will synthesize every Sunday** even with zero new data, because `existing` is None. Adds $0.05-0.10/cold company/week. | [`persona_synthesizer.py:223-235`](../agents/persona_synthesizer.py) |
| 28 | Medium | The user message at [`persona_synthesizer.py:417-434`](../agents/persona_synthesizer.py) trims knowledge_text to 30k chars and transcripts JSON to 50k chars. With Gemini 2.5 Pro's 1M-token window this is fine, but the Claude fallback path uses the same trim and Opus's context is 200k — should pass the full thing. | [`persona_synthesizer.py:418-429`](../agents/persona_synthesizer.py) |
| 29 | Medium | The `_has_new_data` check at [`persona_synthesizer.py:353-380`](../agents/persona_synthesizer.py) walks every outcome/transcript twice with try/except per timestamp. Replace with one SQL count comparing `MAX(logged_at)` to `last_synthesized_at`. | [`persona_synthesizer.py:359-380`](../agents/persona_synthesizer.py) |
| 30 | Low | `_log_summary` ([`persona_synthesizer.py:436-457`](../agents/persona_synthesizer.py)) inserts into `boss_audit_log` synchronously inside an async path. Wrap with `asyncio.to_thread` or use the async supabase client. | [`persona_synthesizer.py:453-457`](../agents/persona_synthesizer.py) |

**What's good and shouldn't change.**
- Cold-start fallback ([`persona_synthesizer.py:132-146`](../agents/persona_synthesizer.py))
  iterates target companies if no personas exist. Reasonable.
- "Bail if no outcomes AND no knowledge AND no existing"
  ([`persona_synthesizer.py:238-245`](../agents/persona_synthesizer.py))
  prevents wasted Gemini calls.

---

### 2.6 OutcomeToPersona (`agents/outcome_to_persona.py`)

**Purpose:** Credit-assigns outcome events back to cited knowledge_ids,
recomputes `outcome_score`. Also evolves persona ATS bank / patterns.

**Current models + cost.** `EVOLVE_MODEL = "claude-opus-4-5-20251101"`
with $1.00 hard cap. credit_outcome is pure SQL (no LLM cost).

**Prompt-quality assessment.**
- `EVOLVE_SYSTEM_PROMPT` ([`outcome_to_persona.py:420-441`](../agents/outcome_to_persona.py)):
  Best prompt for persona evolution in any career-ops codebase I've
  seen. 6 explicit rules, evidence-required, list-cap, "Banned
  keywords are sacred — change at most one per evolution". **Score 10.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 31 | Critical | `evolve_persona` calls `asyncio.run(_ask())` ([`outcome_to_persona.py:622`](../agents/outcome_to_persona.py)) — if the caller is already inside an event loop (a FastAPI handler, an RQ worker that uses `_run_async`), this raises RuntimeError. The fall-through at line 626 creates a brand new event loop, which is a textbook footgun — the new loop has no shared Anthropic client, no shared httpx pool, and exceptions become opaque. This function should be `async def evolve_persona`. The callers can `await` it. | [`outcome_to_persona.py:621-630`](../agents/outcome_to_persona.py) |
| 32 | High | EVOLVE_MODEL is Opus 4.5 ([`outcome_to_persona.py:76`](../agents/outcome_to_persona.py)). This is a "propose minimal JSON edits" task, not a deep-reasoning task. Sonnet matches accuracy at 5× less. | [`outcome_to_persona.py:76`](../agents/outcome_to_persona.py) |
| 33 | Medium | The credit row insert at [`outcome_to_persona.py:303`](../agents/outcome_to_persona.py) is NOT idempotent — two calls for the same outcome_id will insert two credit rows. The docstring acknowledges this (line 332-333) but doesn't enforce it. Add a unique partial index on `(outcome_event_id, knowledge_id)`. | [`outcome_to_persona.py:303`](../agents/outcome_to_persona.py) |
| 34 | Medium | The 0.5 prior + arithmetic mean ([`outcome_to_persona.py:258-279`](../agents/outcome_to_persona.py)) means a single +0.05 outcome (interview pass) shifts the score to ~0.55, then a single -0.02 (interview fail) brings it to 0.515. The clamp at [0, 1] is reasonable but a Beta-distribution prior with weight=10 would handle small-sample variance better. | [`outcome_to_persona.py:258-279`](../agents/outcome_to_persona.py) |
| 35 | Low | The fallback at [`outcome_to_persona.py:209-222`](../agents/outcome_to_persona.py) credits the top-5 knowledge rows when transcript citations are missing. This is reasonable but should be tagged in `metadata: {"credit_source": "fallback_top_k"}` so the dashboard can show "credited via heuristic" vs "credited via citation". | [`outcome_to_persona.py:222`](../agents/outcome_to_persona.py) |

**What's good and shouldn't change.**
- Per-event ±0.5 cap + aggregate [0, 1] clamp
  ([`outcome_to_persona.py:67-73`](../agents/outcome_to_persona.py))
  matches the audit's risk-6 mitigation.
- Dual citation recovery (cite:knowledge_id regex → fallback top-k)
  is exactly the right pattern.
- The `_snapshot_persona_to_versions` before mutation
  ([`outcome_to_persona.py:501-530`](../agents/outcome_to_persona.py))
  gives the dashboard's eventual persona-evolution timeline a complete
  audit trail.

---

### 2.7 InterviewTutor (`agents/interview_tutor.py`)

**Purpose:** 3-level concept ladder chat tutor. Wraps a single Opus 4.5
call into a state shape callable from `api/interview_studio.py`.

**Current models + cost.** Opus 4.5, max 1500 output tokens, $0.20 cap per
turn ([`interview_tutor.py:41-43`](../agents/interview_tutor.py)).

**Prompt-quality assessment.**
- `TUTOR_SYSTEM_PROMPT` ([`interview_tutor.py:98-129`](../agents/interview_tutor.py)):
  Top-3 prompt in the codebase. 6 numbered operating rules, ladder
  semantics (basics/intermediate/advanced), explicit "NEVER FAKE
  EVIDENCE", strict JSON schema, "Always emit valid JSON". **Score 10.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 36 | High | Opus 4.5 for a tutoring chat call where the average response is 800-1500 tokens. Sonnet would match teaching quality and cut cost from ~$0.10 to ~$0.02 per turn. With the audit's #5 (synchronous outcome → credit), tutor cost adds up fast. | [`interview_tutor.py:43`](../agents/interview_tutor.py) |
| 37 | Medium | The cost-cap check at [`interview_tutor.py:348`](../agents/interview_tutor.py) (`cost_capped = bool(result.cost_usd and result.cost_usd > TUTOR_MAX_COST_USD)`) is POST-HOC — by the time you know the call was over-budget the money is already spent. Pre-check the input token estimate against the cap. | [`interview_tutor.py:348`](../agents/interview_tutor.py) |
| 38 | Medium | `TUTOR_HISTORY_TURNS = 8` ([`interview_tutor.py:45`](../agents/interview_tutor.py)) sends the last 16 messages. On a 10-turn conversation this means re-tokenizing 8 previous turns × ~500 chars each = ~4k tokens per turn. With prompt caching this drops to ~$0.001; without it, $0.07. Caching matters here. | [`interview_tutor.py:45, 196-211`](../agents/interview_tutor.py) |
| 39 | Low | The error reply at [`interview_tutor.py:333-346`](../agents/interview_tutor.py) is opaque to the user ("I hit a snag reaching the model. Try again in a moment"). Should also surface the model + error class for support. | [`interview_tutor.py:333-346`](../agents/interview_tutor.py) |

**What's good and shouldn't change.**
- `_format_prep_pack_brief` ([`interview_tutor.py:133-187`](../agents/interview_tutor.py))
  trims each list section to top-8 with 240-char per item.
  Reproducible, bounded prompt size.
- The concept_level coercion ([`interview_tutor.py:214-229`](../agents/interview_tutor.py))
  defaults to the previous level on parse failure — graceful.

---

### 2.8 ResumeEditAssistant (`agents/resume_edit_assistant.py`)

**Purpose:** Three editing modes on Workspace — quick_tweak (single Opus
call, $0.05/3s), rebuild_section (writer→critic→polish on one H2 section,
$0.40-0.60/30-60s), full_rebuild (enqueues G2).

**Current models + cost.** Opus 4.7 across all three internal nodes.
Cost caps: $0.10 quick_tweak, $0.60 rebuild_section.

**Prompt-quality assessment.**
- `QUICK_TWEAK_SYSTEM_PROMPT` ([`resume_edit_assistant.py:86-111`](../agents/resume_edit_assistant.py)):
  Top-tier. 7 absolute rules, fact-fabrication guard, voice preservation,
  exact JSON shape, fallback shape for "cannot safely apply". **Score 10.**
- `REBUILD_SECTION_WRITER_SYSTEM` ([`resume_edit_assistant.py:347-384`](../agents/resume_edit_assistant.py)):
  Equally good. Heading-preservation rule, length parity, fact-fabrication
  guard, persona banned-keyword respect. **Score 10.**
- `REBUILD_SECTION_CRITIC_SYSTEM` ([`resume_edit_assistant.py:386-404`](../agents/resume_edit_assistant.py)):
  Crisp. Specifies improvements/regressions/polish_targets separately
  so the polish stage has a clean list. **Score 9.**
- `REBUILD_SECTION_POLISH_SYSTEM` ([`resume_edit_assistant.py:406-422`](../agents/resume_edit_assistant.py)):
  Solid. Strict JSON. **Score 9.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 40 | High | Three Opus 4.7 calls per `rebuild_section` ([`resume_edit_assistant.py:79-81`](../agents/resume_edit_assistant.py)). Writer needs Opus; critic and polish are conceptually Sonnet tasks. Cuts $0.60 to $0.25. | [`resume_edit_assistant.py:79-81`](../agents/resume_edit_assistant.py) |
| 41 | Medium | The H2 regex ([`resume_edit_assistant.py:238`](../agents/resume_edit_assistant.py)) only matches ATX headings. If anyone ever writes Setext (underline) headings the splitter falls through to "no H2 sections found". Acceptable for now (cv.md uses ATX) but document the constraint in the docstring. | [`resume_edit_assistant.py:238`](../agents/resume_edit_assistant.py) |
| 42 | Medium | `_parse_or_repair` ([`resume_edit_assistant.py:836-854`](../agents/resume_edit_assistant.py)) raises ValueError on malformed JSON, which the FastAPI layer (presumably) turns into 502. For quick_tweak this is correct ("retry the instruction"). For rebuild_section it means losing the writer + critic work. Add a "best-effort recover the rebuilt section as markdown" fallback. | [`resume_edit_assistant.py:846-854`](../agents/resume_edit_assistant.py) |
| 43 | Low | The 60s timeout ([`resume_edit_assistant.py:78`](../agents/resume_edit_assistant.py)) wraps the whole 3-call pipeline. Critic + polish are sequential after writer — a single slow Opus call can blow this without leaving the writer's output recoverable. Persist the writer's draft to a temp row before critic. | [`resume_edit_assistant.py:78`](../agents/resume_edit_assistant.py) |

**What's good and shouldn't change.**
- The cost cap is enforced at EVERY stage with explicit `raise CostCapExceeded`
  ([`resume_edit_assistant.py:498-502, 607-611`](../agents/resume_edit_assistant.py)).
  Best cost discipline in the codebase.
- Heading re-attachment on polish failure ([`resume_edit_assistant.py:637-641`](../agents/resume_edit_assistant.py))
  is exactly the splice-integrity defence this needs.
- The pre-call empty-input guards ([`resume_edit_assistant.py:159-162, 467-472`](../agents/resume_edit_assistant.py))
  prevent wasted Opus calls.

---

### 2.9 IntroEmailAgent (`agents/intro_email_agent.py`)

**Purpose:** Drafts (a) warm-intro request to the introducer or (b) direct
LinkedIn outreach to the target. Used by the Network surface.

**Current models + cost.** Opus 4.7 default, $0.05 cap per call.

**Prompt-quality assessment.**
- `INTRO_EMAIL_SYSTEM` ([`intro_email_agent.py:55-77`](../agents/intro_email_agent.py)):
  Specific: 90-140 word body, NO em-dashes, NO "hope this finds you
  well", "make it forward-able" with concrete one-liner. **Score 8.**
- `TARGET_OUTREACH_SYSTEM` ([`intro_email_agent.py:79-102`](../agents/intro_email_agent.py)):
  Solid. 60-110 words. "One soft ask: 'Would you be open to a 15-min
  chat'". **Score 8.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 44 | High | Opus 4.7 for a 90-140-word email draft is overkill. Sonnet 4.6 matches or exceeds at <10% cost. | [`intro_email_agent.py:47`](../agents/intro_email_agent.py) |
| 45 | Medium | NO banned-phrase list. G4 has 6 specific banned phrases plus the audit's "delve/tapestry/unpack/journey" list. Cold emails are exactly where AI tells get noticed. | [`intro_email_agent.py:55-77`](../agents/intro_email_agent.py) |
| 46 | Low | The "happy to send a separate forwardable note" sign-off at [`intro_email_agent.py:67`](../agents/intro_email_agent.py) is hard-coded in the prompt. Some users may want to disable. Make it a voice_profile field. | [`intro_email_agent.py:67`](../agents/intro_email_agent.py) |

**What's good and shouldn't change.**
- Two distinct modes (request to introducer vs direct outreach) with
  different word counts and tone — captures the real-world distinction.
- "Cite the mutual ONCE early" rule ([`intro_email_agent.py:90-91`](../agents/intro_email_agent.py))
  is the exact discipline that prevents "and oh by the way, our mutual
  friend Bob…" awkwardness.
- Structured error return ([`intro_email_agent.py:260-276`](../agents/intro_email_agent.py))
  on draft failure / cost-cap — UI renders the error state cleanly.

---

### 2.10 ReferralGraph (`agents/referral_graph.py`)

**Purpose:** NetworkX path-finder + LinkedIn CSV import + manual intro
edges + target_company_employees refresh.

**Current models + cost.** NO LLM (pure code).

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 47 | High | `_build_graph` ([`referral_graph.py:151-188`](../agents/referral_graph.py)) does sync Supabase calls inside what's called from async FastAPI handlers. `db.table().execute()` is blocking I/O. Wrap with `asyncio.to_thread` or migrate to `supabase-py>=2.x` async client. | [`referral_graph.py:151-188`](../agents/referral_graph.py) |
| 48 | Medium | `import_linkedin_csv` ([`referral_graph.py:587-640`](../agents/referral_graph.py)) does per-row UPSERT loops — 500-row imports take ~30s. Batch with a single `.upsert(payload_list)`. | [`referral_graph.py:587-640`](../agents/referral_graph.py) |
| 49 | Medium | `_jaccard` ([`referral_graph.py:795-807`](../agents/referral_graph.py)) is a cheap Python fallback for pg_trgm. The audit's network ranking will be flaky on edge cases — "Stripe" vs "Stripes Brewing" depends on token overlap, not embedding similarity. Use a pg RPC for production-grade matching. | [`referral_graph.py:795-807`](../agents/referral_graph.py) |
| 50 | Low | The geometric mean strength ([`referral_graph.py:349-355`](../agents/referral_graph.py)) is the right call but the `product *= max(s, 1e-6)` clamp means a single 0.0 edge effectively zeros the path. A short note in the docstring would help future readers. | [`referral_graph.py:349-355`](../agents/referral_graph.py) |
| 51 | Low | No caching — every `/network/paths/{company}` request rebuilds the in-memory graph from scratch. For 500-person networks this is ~200ms of Supabase round-trips. Add a per-process LRU keyed on `(user_id, graph_version)`. | [`referral_graph.py:131-139`](../agents/referral_graph.py) |

**What's good and shouldn't change.**
- The edge-weight model `weight = 1 - strength` makes Dijkstra return the
  path with the highest cumulative trust — correct algorithmic choice.
- Edge upsert idempotency on `(user_id, src, dst, kind)`
  ([`referral_graph.py:493-497`](../agents/referral_graph.py))
  matches the unique partial index. Defended.
- `ensure_me_node` ([`referral_graph.py:247-269`](../agents/referral_graph.py))
  is race-tolerant via "get or insert".

---

### 2.11 LLMRouter (`agents/llm_router.py`)

**Purpose:** Single ask() / ask_json() entry point across 5 providers.
Cost telemetry, retry-on-format, JSON parsing.

**Current models + cost.** Pricing table at [`llm_router.py:43-73`](../agents/llm_router.py).

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 52 | Critical | NO prompt caching ([`llm_router.py:300-348`](../agents/llm_router.py)) — already flagged in audit C1. Confirmed unfixed. With the long persona system_prompt_template (~3 kB) replayed on every G2 call, this is $30-80/month at current usage. | [`llm_router.py:300`](../agents/llm_router.py) |
| 53 | High | `_default_log_callback` ([`llm_router.py:546-566`](../agents/llm_router.py)) writes a row to Supabase for EVERY call, synchronously, inside what's intended as an async hot path. For G2's 8-14 calls per build × 100 builds/day = 800-1400 sync DB writes/day on the hot path. Audit C11 — confirmed unfixed. | [`llm_router.py:546-566`](../agents/llm_router.py) |
| 54 | High | `_parse_json_loose` ([`llm_router.py:573-597`](../agents/llm_router.py)) has no retry, no schema validation. Audit §4-9 — confirmed unfixed. Every node that uses it has its own try/except + sentinel pattern (g2/g3/g4 each do this independently); centralize into `ask_json` with retry on malformed JSON. | [`llm_router.py:573`](../agents/llm_router.py) |
| 55 | Medium | The `_resolve_temperature` whitelist ([`llm_router.py:121-135`](../agents/llm_router.py)) only knows about kimi-k2 forcing temperature=1. New reasoning models (e.g. future deepseek-r2) will hit a 400 silently. Read this from the pricing table metadata. | [`llm_router.py:121-135`](../agents/llm_router.py) |
| 56 | Medium | `infer_provider` ([`llm_router.py:156-172`](../agents/llm_router.py)) raises `ValueError` on unknown models. This bubbles up to a 500 in production. Cap at provider="auto" with a warning. | [`llm_router.py:156`](../agents/llm_router.py) |
| 57 | Low | DeepSeek + Moonshot 180s timeout ([`llm_router.py:502-507`](../agents/llm_router.py)) is correct for reasoning models but applies to ALL DeepSeek/Moonshot calls — chat-model variants will hold the connection open. Per-model timeout config. | [`llm_router.py:502-507`](../agents/llm_router.py) |

**What's good and shouldn't change.**
- Per-provider lazy client instantiation — multi-provider router with
  zero startup cost on providers you don't use.
- The deepseek-reasoner-doesn't-support-json-mode whitelist
  ([`llm_router.py:138-153`](../agents/llm_router.py)) prevents an
  entire class of bug.
- `kw.update(kwargs)` passthrough on Anthropic ([`llm_router.py:320`](../agents/llm_router.py))
  means callers can pass `cache_control` once it's wired — the
  surgery is small.

---

### 2.12 CostAlerter (`agents/cost_alerter.py`)

**Purpose:** Daily threshold + Sunday digest. Slack → SendGrid → stdout
fallback chain.

**Current models + cost.** NO LLM (model="claude-haiku-4-5-20251001" is a
stub for BaseAgent contract).

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 58 | High | The idempotency check ([`cost_alerter.py:449-464`](../agents/cost_alerter.py)) does an `.ilike("digest_content", "%cost-alerter:daily%")` scan of boss_audit_log every time `check_daily_spend` fires. With 365 days of audit logs this is a full table scan. Use a dedicated `cost_alerts` table keyed on `(alert_kind, date)` with a unique constraint. | [`cost_alerter.py:449-464`](../agents/cost_alerter.py) |
| 59 | Medium | `_aggregate_today` ([`cost_alerter.py:172-200`](../agents/cost_alerter.py)) does `gte("called_at", today_iso)` and then sums in Python. On a 5k-call day this pulls 5k rows. Use a Postgres `SUM(cost_usd) GROUP BY provider` RPC. | [`cost_alerter.py:172-200`](../agents/cost_alerter.py) |
| 60 | Low | The Slack message format ([`cost_alerter.py:281-304`](../agents/cost_alerter.py)) uses `*bold*` and emoji prefixes (⚠ 🐢) which renders fine in Slack but not in the SendGrid fallback. Two formatters would be cleaner. | [`cost_alerter.py:281-304`](../agents/cost_alerter.py) |

**What's good and shouldn't change.**
- The 3-tier fallback chain (Slack → SendGrid → stdout) means alerts
  ALWAYS surface somewhere.
- Idempotency via "already alerted today" check — won't spam.
- 7-day digest formatter ([`cost_alerter.py:306-362`](../agents/cost_alerter.py))
  surfaces top-5 spenders, error rate, cost_capped count. Operationally
  useful.

---

### 2.13 BossAgent (`agents/boss_agent.py`)

**Purpose:** Nightly orchestrator (21:00 GST). Audits freshness, refreshes
≤5 stale companies, generates daily digest, logs to boss_audit_log.

**Current models + cost.** Claude Opus 4.5 for digest generation.

**Prompt-quality assessment.**
- `_generate_digest` system ([`boss_agent.py:195`](../agents/boss_agent.py)):
  *"You are the BossAgent. Write a crisp daily digest for Rizwan's job
  hunt system."* — 11 words. **Score 3.** No banned-phrase list, no
  measurable instruction, no length cap, no anti-AI-tell discipline.
  The model is Opus and the prompt has zero guidance.
- The user prompt is workmanlike but no schema enforcement
  ([`boss_agent.py:196-220`](../agents/boss_agent.py)).

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 61 | High | `_generate_digest` ([`boss_agent.py:175-227`](../agents/boss_agent.py)) uses Opus 4.5 for what is effectively a templating job. Use Haiku 4.5 (or just a Python f-string template — the LLM is genuinely unnecessary here). | [`boss_agent.py:222`](../agents/boss_agent.py) |
| 62 | Medium | `_get_pipeline_summary` ([`boss_agent.py:162-173`](../agents/boss_agent.py)) does NOT filter by user_id — pulls ALL applications. Will break multi-tenancy once that ships. | [`boss_agent.py:166`](../agents/boss_agent.py) |
| 63 | Medium | `_refresh_stale_companies` ([`boss_agent.py:101-117`](../agents/boss_agent.py)) hard-codes a max of 5 per night with `asyncio.sleep(2)` between calls. Sequential. Should run 3-5 in parallel via `asyncio.gather` with a semaphore. | [`boss_agent.py:101-117`](../agents/boss_agent.py) |
| 64 | Medium | `_send_digest` ([`boss_agent.py:229-259`](../agents/boss_agent.py)) saves to a local file `output/reports/daily_digest_{date}.txt` — on Railway this is ephemeral storage that vanishes on redeploy. Replace with Supabase Storage. | [`boss_agent.py:233-241`](../agents/boss_agent.py) |
| 65 | Low | The audit's #19 — "boss_agent iterates all users without is_active filter" — confirmed. Not a bug today (single user) but a footgun once SaaS lands. | [`boss_agent.py:142-160`](../agents/boss_agent.py) |

**What's good and shouldn't change.**
- The 5-companies-per-night throttle prevents Apify rate-limit traps.
- log_boss_audit on every run — operational audit trail.

---

### 2.14 CompanyAgent (`agents/company_agent.py`)

**Purpose:** Per-company expert. Researches, stores 13 knowledge sections,
reviews resume vs JD, runs gap-fill dialogue, builds final brief.

**Current models + cost.** Claude Opus 4.5 throughout.

**Prompt-quality assessment.**
- `_research_company` system ([`company_agent.py:193-195`](../agents/company_agent.py)):
  3 lines. **Score 4.** No banned-phrase list, no specificity guard.
- `build_resume_as_recruitment_expert` system ([`company_agent.py:332-349`](../agents/company_agent.py)):
  Adequate framing ("Head of an Expert Recruitment Agency"). **Score 6.**
  Has "You never invent" but no specificity floor.
- `review_resume_against_jd` system ([`company_agent.py:465-468`](../agents/company_agent.py)):
  Generic. "Be direct, honest, and specific". **Score 5.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 66 | High | The whole module is OLD CODE — preceded by `persona_deep_research.py` and `persona_synthesizer.py`. Most of its functionality is duplicated. `build_resume_as_recruitment_expert` ([`company_agent.py:314-427`](../agents/company_agent.py)) overlaps with G2's `insider_expert_node`. This is dead-code maintenance debt. | entire file |
| 67 | High | `_discover_company_urls` ([`company_agent.py:247-298`](../agents/company_agent.py)) fires 10 Serper queries per company with sequential `asyncio.sleep(0.3)`. Parallelizable. | [`company_agent.py:276-294`](../agents/company_agent.py) |
| 68 | Medium | `_scrape_page` ([`company_agent.py:300-312`](../agents/company_agent.py)) caps at 5000 chars and strips with BeautifulSoup — fine, but no protection against tracking pixels / JS-rendered SPAs. Half the careers pages on the target list are React apps. Switch to playwright-based scraping (or rely on Apify which does this). | [`company_agent.py:300-312`](../agents/company_agent.py) |
| 69 | Medium | `respond_to_rizwan_evidence` ([`company_agent.py:516-574`](../agents/company_agent.py)) is the legacy "gap dialogue" pattern that G2 replaces. Should be marked deprecated. | [`company_agent.py:516`](../agents/company_agent.py) |
| 70 | Low | All `ask_claude` calls in this module are the old back-compat shim ([`base_agent.py:132-152`](../agents/base_agent.py)) — should migrate to `self.ask()` for typed results + tool support. | various |

**What's good and shouldn't change.**
- `_canonicalize` ([`company_agent.py:73-113`](../agents/company_agent.py))
  is duplicated in g2_run.py:182 — the duplication is intentional to
  avoid heavy imports. Keep both copies in sync.

---

### 2.15 RizwanAgent (`agents/rizwan_agent.py`)

**Purpose:** Legacy user-side agent. Holds Rizwan's hardcoded profile,
responds to gap questions, generates cover emails.

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 71 | High | **Lines 32-81** hard-code Rizwan's experience as a Python string constant. This is the worst-case anti-pattern for a SaaS pivot — every other user's profile lives in `profile_master`. The only purpose of this constant is as a fallback when `search_rizwan_profile` returns nothing. Delete it; use the master profile renderer. | [`rizwan_agent.py:32-81`](../agents/rizwan_agent.py) |
| 72 | High | `generate_cover_email` ([`rizwan_agent.py:242-282`](../agents/rizwan_agent.py)) hardcodes "Currently in Dubai", "PMP, PMI-ACP, CSPO, CSM certified", "$1B+ TPV" in the user prompt at lines 270-273. Same anti-pattern as #71. | [`rizwan_agent.py:242-282`](../agents/rizwan_agent.py) |
| 73 | Medium | `system` prompt at line 256: *"You are Rizwan Zafar, writing a cover email..."* — this is fine for personal use but the entire agent's name should be `UserAgent` not `RizwanAgent`. | [`rizwan_agent.py:255-258`](../agents/rizwan_agent.py) |
| 74 | Medium | `reflect_on_application` ([`rizwan_agent.py:284-311`](../agents/rizwan_agent.py)) is unused dead code as far as I can find. Delete or wire. | [`rizwan_agent.py:284-311`](../agents/rizwan_agent.py) |

**What's good and shouldn't change.**
- The semantic search of profile + story bank
  ([`rizwan_agent.py:178-194`](../agents/rizwan_agent.py))
  is the right shape. Keep when generalizing.

---

### 2.16 PerplexitySearch (`agents/perplexity_search.py`)

**Purpose:** Three wrappers: weekly recency_check (sonar), monthly
strategic_posture (sonar-pro), ad-hoc verify_claim (sonar).

**Current models + cost.** Sonar = $1/$1 per M tokens + $0.005/request.
Sonar-Pro = $3/$15 per M. Estimated $5/month total.

**Prompt-quality assessment.**
- `recency_check` system ([`perplexity_search.py:225-249`](../agents/perplexity_search.py)):
  Best disambiguation prompt in the codebase. Explicit allowed/banned
  domains, "if you find fewer than 3 on-topic results, say so — never
  pad with off-topic citations". **Score 10.**
- `strategic_posture` system ([`perplexity_search.py:271-276`](../agents/perplexity_search.py)):
  Tight. **Score 8.**
- `verify_claim` system ([`perplexity_search.py:298-305`](../agents/perplexity_search.py)):
  Adequate. **Score 7.** Should specify "MUST cite at least one
  source from the news/regulator domain types".

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 75 | Medium | The 1-retry pattern ([`perplexity_search.py:160-179`](../agents/perplexity_search.py)) retries on 429/5xx but does NOT respect Retry-After headers. Add. | [`perplexity_search.py:163-169`](../agents/perplexity_search.py) |
| 76 | Low | `verify_claim` parses "VERDICT: true|false|uncertain" from prose ([`perplexity_search.py:317-322`](../agents/perplexity_search.py)) — fragile if Perplexity returns capitalization or "VERDICT: True" / "True." variants. Use response_format=json_object when available. | [`perplexity_search.py:317-322`](../agents/perplexity_search.py) |
| 77 | Low | The flat `SONAR_REQUEST_FEE_USD = 0.005` ([`perplexity_search.py:60`](../agents/perplexity_search.py)) is added to every call. Perplexity's pricing changed in 2025 to charge per actual search; this estimate may be high or low. Document where the value came from in a comment that includes the doc-fetch date. | [`perplexity_search.py:54-60`](../agents/perplexity_search.py) |

**What's good and shouldn't change.**
- "We DO NOT fall back to a stub" stance ([`perplexity_search.py:28-31`](../agents/perplexity_search.py))
  matches the audit's preference for surfacing misconfig rather than
  silently no-op'ing.

---

### 2.17 ApolloEnrich (`agents/apollo_enrich.py`)

**Purpose:** Apollo.io firmographic + open-jobs + people search wrappers.

**Current models + cost.** NO LLM. Apollo credits ($9-18/month).

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 78 | Medium | `enrich_company_into_knowledge` ([`apollo_enrich.py:231-334`](../agents/apollo_enrich.py)) writes to company_knowledge but doesn't embed the row. Knowledge rows without embeddings break the pgvector RPC for that row. Either embed inline or queue for backfill. | [`apollo_enrich.py:316`](../agents/apollo_enrich.py) |
| 79 | Low | The 30s timeout default at [`apollo_enrich.py:75`](../agents/apollo_enrich.py) is fine but `_post` doesn't expose per-call timeout override — useful for the batched `enrich_organizations` paths. | [`apollo_enrich.py:117-150`](../agents/apollo_enrich.py) |
| 80 | Low | The `ApolloPlanBlocked` exception ([`apollo_enrich.py:102-110`](../agents/apollo_enrich.py)) is exactly the right pattern — caller can map to HTTP 402 — but no other agent inherits this discipline. Worth promoting to a base pattern. | [`apollo_enrich.py:102-110`](../agents/apollo_enrich.py) |

**What's good and shouldn't change.**
- Typed exceptions (`ApolloPlanBlocked`, `ApolloRateLimited`) with clear
  semantics. The audit praise was warranted.

---

### 2.18 JobValidator (`agents/job_validator.py`)

**Purpose:** HTTP HEAD/GET revalidator for JD URLs. Marks closed/redirect/error.

**Current models + cost.** NO LLM.

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 81 | High | **Sync httpx in an async-cron context.** `validate_url` uses `httpx.Client` (line 141) not `AsyncClient`. APScheduler can call it in a thread but the rest of the codebase has assumed everything cron-triggered is async. | [`job_validator.py:135-168`](../agents/job_validator.py) |
| 82 | Medium | `_classify_response` ([`job_validator.py:103-132`](../agents/job_validator.py)) only inspects the first 8000 chars of body. Many SPAs return a 10-50 kB loading shell with the actual content rendered post-load. Either run Playwright or accept the false-negative rate. | [`job_validator.py:127-128`](../agents/job_validator.py) |
| 83 | Medium | The "closed body phrases" list ([`job_validator.py:81-90`](../agents/job_validator.py)) is in English only. Greenhouse/Lever ATSes serve some pages in Spanish/French/German. Audit's #10 (status enum drift) was Spanish — same risk here. | [`job_validator.py:81-90`](../agents/job_validator.py) |
| 84 | Low | The "Mozilla/5.0 ... Chrome/124.0" UA at line 60-63 is a year-old fingerprint. Cloudflare WAFs flag stale UAs. Refresh periodically. | [`job_validator.py:60-63`](../agents/job_validator.py) |

**What's good and shouldn't change.**
- HEAD-first probe with GET fallback when HEAD is 405/501 — cheap by default.
- Reopen detection ([`job_validator.py:228-231`](../agents/job_validator.py))
  clears `posting_closed_at` if a previously-closed listing comes back.

---

### 2.19 Evals Harness (`evals/`)

**Purpose:** LLM-as-judge harness using Claude Opus 4.5 for 5-axis resume
scoring. Regression check on golden set.

**Current models + cost.** `claude-opus-4-5` (unversioned — see Critical #5).

**Prompt-quality assessment.**
- `JUDGE_SYSTEM_PROMPT` ([`judge.py:52-87`](../evals/judge.py)):
  Top-3 prompt in the codebase. 5 axes with scoring guides for 0/6/10,
  explicit "do not reward effort or generosity", "Output ONLY valid
  JSON". **Score 10.**

**Concrete findings.**

| # | Severity | What | File:line |
|---|---|---|---|
| 85 | High | `JUDGE_MODEL = "claude-opus-4-5"` ([`judge.py:37`](../evals/judge.py)) — unversioned. Will silently resolve to whatever Anthropic returns from the prefix match in `PRICING_PER_1M`. Pin to the dated revision. | [`judge.py:37`](../evals/judge.py) |
| 86 | Medium | The `_normalize` step at [`judge.py:236-241`](../evals/judge.py) coerces "8.5" or "9/10" into a clamped int. Fine, but a coerced 8.5→8 silently loses signal. Better: surface a parse-warning flag in the result. | [`judge.py:236-241`](../evals/judge.py) |
| 87 | Medium | NO prompt caching despite this being the most-replayed prompt in the codebase (every PR regression run). The judge system prompt is ~2 kB. Audit C1 applies doubly here. | [`judge.py:135-147`](../evals/judge.py) |
| 88 | Low | `JUDGE_TEMPERATURE = 0.0` ([`judge.py:39`](../evals/judge.py)) — Anthropic's docs note that temperature=0 is NOT deterministic (it's just low-variance). Document this in a comment. | [`judge.py:39`](../evals/judge.py) |

**What's good and shouldn't change.**
- The 5-axis scoring (ats_keyword_coverage, evidence_specificity,
  persona_fit, hallucination_check, length_discipline) is the right
  scaffold.
- Fail-loud on malformed JSON ([`judge.py:226-233`](../evals/judge.py))
  — better than a silent-passing eval.

---

## 3. Cross-cutting findings

Patterns that show up in 3+ agents.

### 3.1 Error handling discipline

**Inconsistent.** Three classes of pattern:

1. **Sentinel-on-failure** (G2 critics, G3 predictors, G4 image_brief):
   logs warning, returns a degraded result so the graph carries on.
   Best practice. Keep.
2. **Re-raise with traceback** (LLMRouter, persona_deep_research synth
   fallback): bubbles up to caller. Right when callers can do something
   useful.
3. **Silent swallow** (cost_alerter, intro_email's BaseAgent contract):
   try/except Exception with no surfacing. Hides bugs.

**Recommendation:** every catch-all should EITHER log at WARNING with the
exception class name OR re-raise. The audit's #17 ("generic exception
handler returns `str(exc)`") is a related concern.

### 3.2 Observability gaps

- **Cost tracking** — wired uniformly via `LLMResult.cost_usd` and
  `_default_log_callback`. Best in class.
- **Latency tracking** — measured in `LLMRouter.ask` (line 259), recorded
  per transcript turn. Solid.
- **Per-graph spans / traces** — NONE. No OTel, no Langfuse, no
  Helicone. Debugging a slow G2 build means reading the agent_transcript
  blob in Supabase by eye.
- **Per-prompt cache-hit rate** — irrelevant today since caching is off
  (audit C1) but once it's on, NO metric exposes the hit rate.

**Recommendation:** Langfuse integration as a single env var addition
(`LANGFUSE_PUBLIC_KEY`). The `_default_log_callback` shape is already
a perfect span hook.

### 3.3 Async/await usage

Three concrete bugs:

1. `outcome_to_persona.evolve_persona` calls `asyncio.run()` inside a
   sync function called by async code (Critical #5/Finding #31).
2. `referral_graph._build_graph` does sync Supabase `.execute()` calls
   inside an async-called path (Finding #47).
3. `job_validator.validate_url` uses `httpx.Client` (sync) but is
   intended for an async APScheduler cron (Finding #81).

There are also several sync DB writes inside async paths
(persona_synthesizer._log_summary, llm_router._default_log_callback,
boss_agent.log_boss_audit) — not blocking enough to cause issues today
but technically wrong.

### 3.4 Schema-validation discipline on LLM outputs

- **Best:** `evals/judge.py:_normalize` (raises on missing axis,
  coerces score to clamped int).
- **Good:** G2 `_run_ats_critic` (parse fail → sentinel critique),
  G3 `_safe_parse_question_list` (parse fail → empty list).
- **Acceptable:** G4 `pick_angle`, `draft_v1`, `polish` (try/except
  with degraded result).
- **Missing entirely:** PersonaSynthesizer (parses synth output with
  bare `_parse_json_loose`, no schema check — a missing field silently
  blanks that field in the upsert), BossAgent._generate_digest (no
  validation at all — accepts whatever prose the LLM returns).

**Recommendation:** Add a `pydantic` schema for each load-bearing
LLM-JSON return shape. The router's `ask_json` can take an optional
`schema=` arg and raise `LLMSchemaMismatch` on validation failure.

### 3.5 Cost cap enforcement at the worker level

**Audit §4 Critical-2 — confirmed unfixed.** The worker
([`api/worker.py:103-176`](../api/worker.py)) accepts a `max_cost_usd`
from payload but only forwards it to `run_g2_graph(max_cost_usd=...)` —
there's no per-worker cumulative-spend check. If the orchestrator's
post-call check ([`g2_nodes.py:879-885`](../resume_agents/g2_nodes.py))
fails to fire (a single writer call exceeds the cap on its own,
or a critic burns through reserve), the cap is silently violated.

**Additionally:** G4, persona_deep_research, persona_synthesizer,
boss_agent, and cost_alerter have NO cost cap at all. G4's $0.15
target is a log-only threshold (#17).

**Recommendation:** wrap every `router.ask()` in a process-scoped
spending tracker keyed on `jobs_runs.id`. Hard-abort when cumulative
exceeds `payload.max_cost_usd`. This is ~30 LOC in `agents/llm_router.py`.

### 3.6 JSON parsing robustness

`_parse_json_loose` is used in **9 places** across 7 files
(`g2_nodes`, `g3_nodes`, `g4_linkedin_graph`, `outcome_to_persona`,
`persona_synthesizer`, `persona_deep_research`, `resume_edit_assistant`,
`linkedin_voice_extractor`, `interview_tutor`, `evals/judge`).

Every caller has its own try/except + retry policy. **No central
discipline.** The audit's #4 finding ("ask_json should retry with a
'respond with strict JSON only' reminder, then fall back to a sentinel")
is the right shape — centralize once.

---

## 4. Top 12 findings ranked by impact / effort

Same format as `docs/FLOW_REVIEW_2026_05_11.md` Part 5.

| Rank | What | Effort | Impact | File:line |
|---|---|---|---|---|
| 1 | **`evolve_persona` async footgun** — convert to `async def`, await it everywhere | 30 LOC | Fixes a production crash waiting to happen | [`outcome_to_persona.py:621-630`](../agents/outcome_to_persona.py) |
| 2 | **Worker-level cost cap** — track cumulative spend per `jobs_runs.id`, hard-abort | 30 LOC in `llm_router.py` + 10 in `worker.py` | Closes audit §4 Critical-2; protects against $50 G2 runs | [`api/worker.py:103-176`](../api/worker.py), [`agents/llm_router.py`](../agents/llm_router.py) |
| 3 | **Right-size 8 Opus nodes to Sonnet/Haiku** — see §5 table | 8-line env defaults | $40-100/mo savings, neutral quality (gate via golden eval) | [`config/settings.py:26-100`](../config/settings.py), [`agents/interview_tutor.py:43`](../agents/interview_tutor.py), [`agents/outcome_to_persona.py:76`](../agents/outcome_to_persona.py), [`agents/resume_edit_assistant.py:79-81`](../agents/resume_edit_assistant.py), [`agents/intro_email_agent.py:47`](../agents/intro_email_agent.py) |
| 4 | **Anthropic prompt caching** — wrap system + persona block at ≥1024 tok | 15 LOC | -40% Anthropic cost across G2/G3/G4/tutor/edit (audit C1 confirmed unfixed) | [`agents/llm_router.py:300`](../agents/llm_router.py) |
| 5 | **WRITER_SYSTEM + COVER_EMAIL_SYSTEM banned-phrase list** — mirror G4's discipline | 20 LOC | The most user-visible LLM output finally has anti-AI-tell guards | [`resume_agents/g2_nodes.py:411, 994`](../resume_agents/g2_nodes.py) |
| 6 | **Image-brief node broken kwargs** — likely never works in production | 5 LOC | Image briefs actually persist; one less degraded-path scenario | [`agents/g4_linkedin_graph.py:766-777`](../agents/g4_linkedin_graph.py) |
| 7 | **`_default_log_callback` sync DB write** — wrap in `to_thread` or async client | 10 LOC | Saves 200ms per LLM call on hot path; audit C11 confirmed unfixed | [`agents/llm_router.py:546-566`](../agents/llm_router.py) |
| 8 | **Skip-gate for cold-start synth** — don't refresh new personas weekly | 15 LOC | $20-40/mo savings; audit C4 partially addresses but cold-start hole remains | [`agents/persona_synthesizer.py:223-235`](../agents/persona_synthesizer.py) |
| 9 | **Centralize `_parse_json_loose` retry + schema** — single `router.ask_json(schema=...)` | 40 LOC | Removes 9 duplicate try/except blocks; closes audit §4-9 | [`agents/llm_router.py:573`](../agents/llm_router.py) |
| 10 | **Cap critic retry max_tokens** — confirmed unfixed audit C10 | 1 LOC | Caps critic blow-up cost | [`resume_agents/g2_nodes.py:585`](../resume_agents/g2_nodes.py) |
| 11 | **Eval judge model pin** — `claude-opus-4-5-20251101` not `claude-opus-4-5` | 1 LOC | Stable regression scoring across Anthropic model rollovers | [`evals/judge.py:37`](../evals/judge.py) |
| 12 | **Delete `RIZWAN_EXPERIENCE_CONTEXT` hardcode** — render from profile_master | 50 LOC | SaaS-pivot unblocked for user #2; multi-tenancy correctness | [`agents/rizwan_agent.py:32-81, 270-273`](../agents/rizwan_agent.py) |

**Top 5 alone (≈ 100 LOC) unlock $40-100/mo savings + a likely production
crash fix + the anti-AI-tell guards on the most-visible prompts.**

---

## 5. Suggested model right-sizing

Extends `docs/FLOW_REVIEW_2026_05_11.md` Part 4 with NEW swaps not in
that table. All assume a golden-eval pass before flip.

| # | Node | Current | Proposed | Why | Est. monthly $ |
|---|---|---|---|---|---|
| M1 | **G3 behavioral_predictor** | claude-opus-4-5 | claude-haiku-4-5 | List-generation w/ JSON schema — classification, not reasoning | $5-15 |
| M2 | **G3 domain_predictor** | claude-opus-4-5 | claude-sonnet-4-6 | Domain-specific list but JSON-strict — Sonnet matches | $4-10 |
| M3 | **G3 star_matcher** | claude-opus-4-5 | claude-sonnet-4-6 | Match-and-tag task — Sonnet handles semantic match fine | $3-8 |
| M4 | **G2 cover_email** | claude-opus-4-5 | claude-sonnet-4-6 | 4-7 sentences of value-first prose; Sonnet matches voice | $5-12 |
| M5 | **G2 orchestrator** | claude-opus-4-5 | claude-haiku-4-5 | Pure routing decision with strict JSON schema | $4-10 |
| M6 | **resume_edit critic** | claude-opus-4-7 | claude-sonnet-4-6 | Critic scores + emits polish_targets — Sonnet matches | $5-12 |
| M7 | **resume_edit polish** | claude-opus-4-7 | claude-sonnet-4-6 | Surgical edit applied per critic list — Sonnet matches | $4-10 |
| M8 | **InterviewTutor turns** | claude-opus-4-5 | claude-sonnet-4-6 | 800-1500 token chat reply; Sonnet matches teaching quality | $15-40 |
| M9 | **IntroEmailAgent** | claude-opus-4-7 | claude-sonnet-4-6 | 60-140 word email; Sonnet's voice is fine | $1-3 |
| M10 | **PersonaSynthesizer Claude fallback** | claude-opus-4-5 | claude-sonnet-4-6 | Fallback path; Gemini's the primary; Sonnet is fine | $2-5 |
| M11 | **PersonaDeepResearch Claude fallback** | claude-opus-4-5 | claude-sonnet-4-6 | Same shape as M10 | $1-3 |
| M12 | **evolve_persona** | claude-opus-4-5 | claude-sonnet-4-6 | Propose-minimal-edits task with cap rules | $3-8 |
| M13 | **BossAgent digest** | claude-opus-4-5 | claude-haiku-4-5 OR Python template | Templating job — Opus is hilarious overkill here | $2-5 |
| **Total** | | | | | **$54-141/mo** |

These are NEW swaps not yet captured in the audit's Tier-1/Tier-2 cost
table. Cumulative with audit C1-C5 (prompt caching + max_tokens cap +
critic gating + persona skip + orchestrator-Haiku) the system can run
at **$60-80/mo all-in** at current usage rather than $200+.

---

## 6. Prompt quality scoring + bottom 3

Score 1-10 across:
- **S** = Specificity (measurable instructions?)
- **A** = Anti-AI-tell (banned phrases?)
- **O** = Output schema (exact JSON + parse-validation?)
- **F** = Failure modes (what if input is missing?)
- **C** = Cost discipline (max_tokens, demand brevity?)

| Prompt | File:line | S | A | O | F | C | Avg |
|---|---|---|---|---|---|---|---|
| G4 DRAFT_V1_SYSTEM | [`g4:292-320`](../agents/g4_linkedin_graph.py) | 10 | 10 | 10 | 9 | 9 | **9.6** |
| evals/judge JUDGE_SYSTEM | [`judge.py:52-87`](../evals/judge.py) | 10 | 9 | 10 | 9 | 9 | **9.4** |
| OUTCOME EVOLVE_SYSTEM | [`outcome:420-441`](../agents/outcome_to_persona.py) | 10 | 8 | 10 | 9 | 9 | **9.2** |
| G4 CRITIQUE_SYSTEM | [`g4:425-453`](../agents/g4_linkedin_graph.py) | 10 | 10 | 10 | 8 | 8 | **9.2** |
| resume_edit QUICK_TWEAK | [`edit:86-111`](../agents/resume_edit_assistant.py) | 10 | 7 | 10 | 9 | 9 | **9.0** |
| InterviewTutor TUTOR_SYSTEM | [`tutor:98-129`](../agents/interview_tutor.py) | 10 | 6 | 10 | 9 | 9 | **8.8** |
| Perplexity recency system | [`perp:225-249`](../agents/perplexity_search.py) | 10 | 6 | 8 | 10 | 9 | **8.6** |
| PersonaDeepResearch SYNTH_SYSTEM | [`research:297-309`](../agents/persona_deep_research.py) | 10 | 7 | 10 | 8 | 7 | **8.4** |
| G2 ATS_CRITIC_SYSTEM | [`g2:519-539`](../resume_agents/g2_nodes.py) | 9 | 5 | 10 | 8 | 9 | **8.2** |
| G2 ORCHESTRATOR_SYSTEM | [`g2:778-791`](../resume_agents/g2_nodes.py) | 9 | 5 | 10 | 8 | 9 | **8.2** |
| G2 META_CRITIC_SYSTEM | [`g2:325-340`](../resume_agents/g2_nodes.py) | 8 | 5 | 9 | 9 | 8 | **7.8** |
| G3 DOMAIN_PREDICTOR | [`g3:298-312`](../interview_agents/g3_nodes.py) | 9 | 5 | 9 | 7 | 8 | **7.6** |
| G3 STAR_MATCHER_SYSTEM | [`g3:506-517`](../interview_agents/g3_nodes.py) | 9 | 5 | 10 | 8 | 6 | **7.6** |
| G3 MOCK_INTERVIEWER | [`g3:682-691`](../interview_agents/g3_nodes.py) | 8 | 4 | 6 | 7 | 7 | **6.4** |
| G2 POLISHER_SYSTEM | [`g2:918-930`](../resume_agents/g2_nodes.py) | 6 | 4 | 10 | 7 | 7 | **6.8** |
| PersonaSynthesizer SYSTEM | [`synth:59-92`](../agents/persona_synthesizer.py) | 7 | 4 | 9 | 6 | 6 | **6.4** |
| G2 ADVOCATE_SYSTEM | [`g2:265-276`](../resume_agents/g2_nodes.py) | 6 | 3 | 5 | 6 | 6 | **5.2** |
| IntroEmailAgent INTRO_EMAIL_SYSTEM | [`intro:55-77`](../agents/intro_email_agent.py) | 8 | 3 | 8 | 5 | 7 | **6.2** |
| G2 WRITER_SYSTEM | [`g2:411-426`](../resume_agents/g2_nodes.py) | 7 | 3 | 5 | 5 | 6 | **5.2** |
| CompanyAgent research system | [`co:193-195`](../agents/company_agent.py) | 4 | 0 | 8 | 4 | 6 | **4.4** |
| CompanyAgent recruitment_expert | [`co:332-349`](../agents/company_agent.py) | 6 | 0 | 8 | 5 | 5 | **4.8** |
| G2 COVER_EMAIL_SYSTEM | [`g2:994-998`](../resume_agents/g2_nodes.py) | 4 | 0 | 0 | 3 | 5 | **2.4** |
| RizwanAgent cover_email | [`rizwan:255-258`](../agents/rizwan_agent.py) | 4 | 2 | 0 | 3 | 5 | **2.8** |
| BossAgent digest | [`boss:195`](../agents/boss_agent.py) | 2 | 0 | 0 | 2 | 2 | **1.2** |

### Bottom 3 that most need rewriting

1. **BossAgent `_generate_digest` system** ([`boss_agent.py:195`](../agents/boss_agent.py))
   — 11 words. Calls Opus. No schema. **Rewrite or remove the LLM call entirely** — this is templating, not generation.

2. **G2 COVER_EMAIL_SYSTEM** ([`g2_nodes.py:994-998`](../resume_agents/g2_nodes.py))
   — 4 lines, no banned-phrase list, no JSON schema, no length cap. Cover emails are 4-7 sentences of high-stakes prose that the user sends to a real person. This is the system prompt G4 already wrote for its post drafts; copy that structure.

3. **G2 WRITER_SYSTEM** ([`g2_nodes.py:411-426`](../resume_agents/g2_nodes.py))
   — The single most consequential prompt in the system (it writes the actual resume the user submits) and it has 2 weak anti-pattern rules ("never use first person", "never use 'responsible for'"). No banned-phrase list. No measurable quantification floor. No fact-fabrication guard. The audit's audit-360-synthesis Part 6 ("delve / tapestry / unpack / journey") rule is enforced in G4's draft_v1 but NOT here.

---

## 7. What's already great and the system should defend

Five specific decisions worth keeping (with file:line):

1. **5-LLM router with pluggable cost telemetry.**
   [`agents/llm_router.py`](../agents/llm_router.py). Lazy clients,
   cost+latency tracked in `LLMResult`, optional callback hook for
   `agent_call_log`. The audit's praise (§Part 2.1) was right — this
   is the spine.

2. **Cite-knowledge breadcrumb dual encoding.**
   [`resume_agents/g2_nodes.py:232-252`](../resume_agents/g2_nodes.py).
   Both `cite:knowledge_id=<uuid>` regex AND structured
   `cited_knowledge_ids` array. Defence in depth — when
   `outcome_to_persona.credit_outcome` parses, either format works.
   First-time format drift won't break credit assignment.

3. **G4 banned-phrase discipline.**
   [`agents/g4_linkedin_graph.py:295`](../agents/g4_linkedin_graph.py).
   "NEVER use these phrases: 'delve', 'tapestry', 'unpack', 'journey',
   'at the end of the day', 'a testament to'…". This is the *only*
   place in the codebase where the anti-AI-tell discipline is in the
   prompt itself, with explicit phrases. Promote to G2 writer + cover
   email + intro email immediately.

4. **resume_edit_assistant cost cap discipline.**
   [`agents/resume_edit_assistant.py:498-502, 607-611`](../agents/resume_edit_assistant.py).
   `raise CostCapExceeded` at every stage with cumulative cost
   tracked. The only module in the codebase that gets this right.
   Promote pattern to G2/G3/G4.

5. **PersonaSynthesizer's "trust outcomes over published intel".**
   [`agents/persona_synthesizer.py:71-75`](../agents/persona_synthesizer.py).
   "If recruitment-intel says X but outcomes contradict X → trust
   outcomes, note the override". This is exactly the audit's wedge
   (#3 outcome-conditioned RAG). Keep the rule literal.

6. **Apify rag-web-browser 201-acceptance.**
   [`agents/persona_deep_research.py:148-149`](../agents/persona_deep_research.py).
   The Apify API returns 201 (not 200) on success. The wrapper
   accepts both — small thing, but the kind of detail that means the
   pipeline actually works.

7. **G2's persona-quality gate.**
   [`resume_agents/g2_run.py:42-156`](../resume_agents/g2_run.py).
   Refuses builds against low-quality personas without `force=true`.
   Saves ~$5 per blocked build. The verdict / quality / message return
   shape is exactly right for the API to surface cleanly.

8. **The Perplexity disambiguation prompt.**
   [`agents/perplexity_search.py:225-249`](../agents/perplexity_search.py).
   Whitelist of allowed domains, blacklist of travel.state.gov / .edu
   immigration / consular sources. Solves the Visa-as-payments vs
   Visa-as-travel-document problem cleanly. The
   `docs/FLOW_REVIEW_2026_05_11.md` Part 6 #4 praise was right.

---

## Appendix A — Confirmed-unfixed audit findings

The 2026-05-10 audit listed 21 numbered concerns. From a fresh read on
2026-05-11, the following are confirmed STILL OPEN:

- C1 Anthropic prompt caching off — confirmed at [`llm_router.py:300-348`](../agents/llm_router.py)
- C2 max_tokens=8000 on reasoning critics — confirmed at [`g2_nodes.py:574`](../resume_agents/g2_nodes.py)
- C3 ensemble ATS critic always runs both — confirmed at [`g2_graph.py:97-100`](../resume_agents/g2_graph.py)
- C4 persona synth runs unconditionally — confirmed (partial gate exists but cold-start path doesn't gate; [`persona_synthesizer.py:223-235`](../agents/persona_synthesizer.py))
- C5 Opus on orchestrator/polisher — confirmed at [`config/settings.py:50-51`](../config/settings.py)
- C6 meta_critic dumps 50k chars — confirmed at [`g2_nodes.py:367`](../resume_agents/g2_nodes.py)
- C7 RAG fetch always 5 chunks — confirmed at [`g2_nodes.py:147`](../resume_agents/g2_nodes.py)
- C10 critic retry doubles max_tokens — confirmed at [`g2_nodes.py:585`](../resume_agents/g2_nodes.py)
- C11 verbose agent_call_log payload — confirmed at [`llm_router.py:546-566`](../agents/llm_router.py) (sync write but not full payload — partial)
- §4 Critical-2 worker-side cost cap — confirmed at [`api/worker.py:103-176`](../api/worker.py)
- §4 #9 `_parse_json_loose` no retry/schema — confirmed at [`llm_router.py:573-597`](../agents/llm_router.py)
- §4 #19 boss_agent has no is_active filter — confirmed at [`boss_agent.py:142-160`](../agents/boss_agent.py)

The audit's other 9 concerns are either already-fixed (#1 SINGLE_USER
bypass, #2 worker cap-at-ingress, #3 idempotency key) or out-of-scope
for this review (#11-#21 in API surface and dashboard layer).
