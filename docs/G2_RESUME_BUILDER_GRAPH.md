# G2 — Resume Builder Graph (Design)

**Status**: Design · awaiting Phase 0 merge before implementation
**Phase**: 1 (after `multi-llm` Phase 0 lands)
**Owner**: Rizwan
**Last updated**: 2026-05-09

---

## 1. Why this graph

The current resume-build flow in `pipeline._process_single_job()` is a sequence:

```
CompanyAgent.build_resume_as_recruitment_expert  (Claude Opus, single shot)
  → RizwanAgent (gap dialogue, max 6 turns)
  → CompanyAgent.build_final_resume_brief
  → ResumeBuilderAgent (DOCX render)
  → RizwanAgent.generate_cover_email
```

Three problems:

1. **No adversarial loop**. The "ATS critic" step doesn't exist — the resume is a single-shot Claude output with no convergence test.
2. **No memory across builds**. Building resume #4 for Mastercard learns nothing from resumes #1–3 for Mastercard, even when they were rejected for the same reason.
3. **Single-model concentration**. ~95% Claude Opus. No model diversity = same blind spots every time.

G2 fixes all three:

| Problem | G2 mechanism |
|---|---|
| No adversarial loop | Writer ↔ ATS Critic loop with hard ATS-score gate (≥95) |
| No memory | Meta-Critic node reads last 5 transcripts for THIS company; success/failure patterns persisted in `company_personas` |
| Single-model concentration | 5 providers across 8 nodes — Claude only where Claude is genuinely best |

---

## 2. State schema

```python
from typing import Annotated, TypedDict
from operator import add

class ResumeState(TypedDict, total=False):
    # ─── Inputs (set at entry) ──────────────────────────────────────────
    job: dict                       # full job row from jobs table
    job_id: int
    company_name: str
    company_persona: dict           # row from company_personas (or None)
    master_resume_md: str           # from rizwan_profile / cv.md
    past_transcripts: list[dict]    # last 5 resume_builds for this company
    resume_build_id: str            # uuid, set on entry

    # ─── Agent outputs (accumulated) ────────────────────────────────────
    expert_notes: str               # Insider Expert (Gemini 2.5 Pro + grounding)
    advocate_notes: str             # Advocate (Claude Opus 4.5)
    meta_critic_warnings: list[str] # Meta-Critic (Gemini 2.5 Pro)
    current_draft: str              # Writer (Claude Opus 4.5)
    critic_a: dict                  # ATS Critic A (DeepSeek-R1)
    critic_b: dict                  # ATS Critic B (Kimi K2)
    merged_critique: dict           # union(A, B) + dedupe

    # ─── Final output ───────────────────────────────────────────────────
    final_resume_md: str
    final_score: int
    final_breakdown: dict
    cover_email_md: str
    resume_docx_url: str
    resume_pdf_url: str

    # ─── Audit / control ────────────────────────────────────────────────
    transcript: Annotated[list, add]   # append-only — every node turn
    iteration: int                     # writer-critic loop counter
    converged: bool                    # orchestrator decision
    cost_usd_total: float
    latency_ms_total: int
```

LangGraph's `Annotated[list, add]` reducer lets multiple nodes append to `transcript` without conflicts.

---

## 3. Node-by-node spec

### 3.1 entry_point
**Pure code, no LLM.** Loads:
- `job` row from `jobs`
- `company_persona` row from `company_personas` (NULL on cold start)
- `master_resume_md` from `rizwan_profile` table or `cv.md` fallback
- `past_transcripts` = last 5 `resume_builds.agent_transcript` for `WHERE company_name = company AND status = 'converged'`

Creates a `resume_builds` row with `status='running'` and seeds `resume_build_id`.

### 3.2 insider_expert (parallel branch A)
**Provider**: `google` · **Model**: `gemini-2.5-pro` · **Tools**: `google_search` grounding

Reads: `job`, `company_persona`, `master_resume_md`
Outputs: `expert_notes` — top 5 keywords, 3 cultural signals, 3 things to drop, exact summary line

```python
system = (company_persona['system_prompt_template'] if company_persona
          else INSIDER_EXPERT_TEMPLATE.format(company=...))
tools = [{"type": "google_search"}]
```

Why Gemini: 1M context fits all 13 sections of `company_knowledge` + JD + master resume in one shot, and native Google grounding pulls fresh news without a separate Serper call.

### 3.3 advocate (parallel branch B)
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Reads: `job`, `master_resume_md`, `past_transcripts` (for "what worked before for this candidate")
Outputs: `advocate_notes` — 3-5 strongest achievements, anything to defend, career-arc framing

Why Claude: narrative reasoning + maintaining the candidate's voice.

### 3.4 meta_critic
**Provider**: `google` · **Model**: `gemini-2.5-pro`

Reads: `past_transcripts` (the 5 prior builds for this company)
Outputs: `meta_critic_warnings` — recurring complaints from past ATS Critics

```
Last 3 Mastercard resumes were flagged for missing 'tokenization'
Last 4 builds had ≥1 bullet without quantification — flag this earlier
Last 2 cover emails referenced 'crypto' which Mastercard de-emphasizes
```

Why Gemini: long context fits 5 transcripts (~50k tokens). This is the single most-distinguishing node in G2.

### 3.5 writer
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Reads: all of `expert_notes`, `advocate_notes`, `meta_critic_warnings`, plus `merged_critique` if `iteration > 0`
Outputs: `current_draft` (markdown)

Strict rules baked into prompt: action-verb start, ≥70% quantified, no first-person, no "responsible for", never fabricate.

Why Claude: this is the one node where you don't compromise. Executive resume prose.

### 3.6 ats_critic_a + ats_critic_b (run in parallel)
**Critic A**: `deepseek` · `deepseek-reasoner` (R1)
**Critic B**: `moonshot` · `kimi-k2`

Both receive: `current_draft` + `job.description_md`
Both output JSON:
```json
{
  "ats_score": 0-100,
  "keyword_coverage": [{"term": "...", "in_resume": true|false}],
  "missing_keywords": [...],
  "parseability_issues": [...],
  "skim_test_pass": true|false,
  "quantification_rate": 0.0-1.0,
  "specific_fixes": ["concrete edit 1", "concrete edit 2", ...]
}
```

Then a tiny code-only **merge_critique** step:
- `ats_score` = `min(A, B)` (be strict)
- `missing_keywords` = `union(A, B)`
- `specific_fixes` = `union(A, B)` deduped by similarity
- Stored as `merged_critique` in state

Why ensemble here: ATS scoring is the noisiest step; two reasoning models from different vendors catch more parsing/coverage failures than any single model.

### 3.7 orchestrator
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Decision rules:
- If `iteration >= settings.g2_max_iterations` (default 3): force converged
- If `merged_critique.ats_score >= settings.g2_target_ats_score` (default 95) AND `len(specific_fixes) <= 2`: converged
- Otherwise: not converged → loop back to writer

Outputs: `converged` (bool), `iteration` (incremented), `next_question` (for writer)

### 3.8 polisher
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Reads: `current_draft`, `merged_critique`, `expert_notes`
Outputs: `final_resume_md`, `final_score` (0-100 self-assessed), `final_breakdown`

Self-score weights: fit 40%, ATS 20%, impact 20%, narrative 10%, polish 10%.

### 3.9 cover_email
**Provider**: `anthropic` · **Model**: `claude-opus-4-5-20251101`

Reads: `final_resume_md`, `expert_notes` (for company hooks), `job`
Outputs: `cover_email_md`

### 3.10 export_and_persist
**Pure code, no LLM.**
- Markdown → DOCX via pandoc subprocess
- Markdown → PDF via reportlab (or skip if pandoc has wkhtmltopdf)
- Upload to Supabase Storage `resumes/` bucket → signed URL
- Update `resume_builds` row: `status='converged'`, `resume_md`, `cover_email_md`, URLs, `cost_usd_total`, `latency_ms_total`, `agent_transcript`

---

## 4. Graph topology

```python
from langgraph.graph import StateGraph, END

g = StateGraph(ResumeState)
g.add_node("entry",          entry_point_node)
g.add_node("insider_expert", insider_expert_node)
g.add_node("advocate",       advocate_node)
g.add_node("meta_critic",    meta_critic_node)
g.add_node("writer",         writer_node)
g.add_node("ats_critic_a",   ats_critic_a_node)
g.add_node("ats_critic_b",   ats_critic_b_node)
g.add_node("merge_critique", merge_critique_node)
g.add_node("orchestrator",   orchestrator_node)
g.add_node("polisher",       polisher_node)
g.add_node("cover_email",    cover_email_node)
g.add_node("export",         export_and_persist_node)

g.set_entry_point("entry")

# Parallel branch: expert + advocate run concurrently
g.add_edge("entry", "insider_expert")
g.add_edge("entry", "advocate")

# Both feed the meta_critic (LangGraph waits for both)
g.add_edge("insider_expert", "meta_critic")
g.add_edge("advocate",       "meta_critic")

g.add_edge("meta_critic", "writer")

# Parallel critic ensemble
g.add_edge("writer", "ats_critic_a")
g.add_edge("writer", "ats_critic_b")
g.add_edge("ats_critic_a", "merge_critique")
g.add_edge("ats_critic_b", "merge_critique")

g.add_edge("merge_critique", "orchestrator")

# Conditional: loop or converge
g.add_conditional_edges(
    "orchestrator",
    lambda state: "polisher" if state["converged"] else "writer",
    {"writer": "writer", "polisher": "polisher"},
)

g.add_edge("polisher",     "cover_email")
g.add_edge("cover_email",  "export")
g.add_edge("export",       END)

graph = g.compile(checkpointer=postgres_saver)
```

---

## 5. Checkpointing

Use `langgraph-checkpoint-postgres` against the existing Supabase Postgres. Thread id = `resume_build_id`. Lets us:

- Resume a build mid-flight if the API server crashes
- Re-run individual nodes during prompt iteration without re-running the whole graph
- Audit-replay any historical build deterministically

Migration: `langgraph-checkpoint-postgres` creates its own schema (`langgraph` namespace by default) — separate from our app tables.

---

## 6. Cost analysis (per build)

| Node | Provider | Tokens (in/out) | Cost |
|---|---|---|---|
| Insider Expert | Gemini 2.5 Pro | 30k / 4k | $0.058 |
| Advocate | Claude Opus 4.5 | 8k / 2k | $0.270 |
| Meta-Critic | Gemini 2.5 Pro | 50k / 3k | $0.078 |
| Writer × 1.5 avg iters | Claude Opus 4.5 | 12k × 1.5 / 4k × 1.5 | $0.720 |
| ATS Critic A × 1.5 iters | DeepSeek-R1 | 6k × 1.5 / 2k × 1.5 | $0.012 |
| ATS Critic B × 1.5 iters | Kimi K2 | 6k × 1.5 / 2k × 1.5 | $0.009 |
| Orchestrator × 1.5 iters | Claude Opus 4.5 | 4k × 1.5 / 1k × 1.5 | $0.203 |
| Polisher | Claude Opus 4.5 | 8k / 4k | $0.420 |
| Cover Email | Claude Opus 4.5 | 6k / 2k | $0.240 |
| **Total per build** | | | **~$2.01** |

At your current cadence (~3 high-score builds/week), **~$25/month**. Manual gating at score ≥ 85 keeps this in check.

---

## 7. Dependencies on Phase 0

This design assumes:

- ✅ `agents/llm_router.py` — multi-provider dispatch
- ✅ `config/settings.py` G2_* slots
- ✅ `db/multi_llm_schema.sql` for `resume_builds`, `company_personas`, `agent_call_log`
- ⏳ `langgraph` + `langgraph-checkpoint-postgres` installed (in `requirements.txt`)
- ⏳ Persona auto-synthesis from existing `company_knowledge` (Phase 1.5 — can ship empty initially; falls back to a generic INSIDER_EXPERT_TEMPLATE when persona is NULL)

---

## 8. Ship plan

| Step | What | Verification |
|---|---|---|
| 1 | Apply `db/multi_llm_schema.sql` to Supabase | Tables exist via Supabase dashboard |
| 2 | `pip install -r requirements.txt` (LangGraph + google-genai) | `python -c "import langgraph, google.genai"` |
| 3 | Add `resume_agents/g2_graph.py` with the 12 nodes above | `from resume_agents.g2_graph import build_g2_graph; build_g2_graph()` compiles |
| 4 | Wire `_process_single_job()` to invoke G2 graph behind a feature flag (`USE_G2_GRAPH=true`) — keep legacy path until validated | Both code paths work; flag-controlled |
| 5 | Run G2 against 3 historical jobs (Mastercard / Stripe / Tabby) — compare outputs to legacy baseline | Side-by-side dashboard view |
| 6 | Add dashboard UI to log `resume_outcomes` | Form on `/jobs/[id]` page |
| 7 | After 10+ builds: enable persona synthesis weekly cron | `company_personas.last_synthesized_at` updates |

---

## 9. Open questions (for later resolution)

1. **PDF rendering** — pandoc-only or add wkhtmltopdf to Dockerfile? (Today: pandoc generates DOCX, no PDF.)
2. **Meta-Critic on cold start** — when `len(past_transcripts) == 0`, does the node skip or fall back to a static "common pitfalls" prompt? Current design: skip, write empty warnings.
3. **Cover-email convergence** — should it have its own critic loop, or trust Claude single-shot? Current design: single-shot.
4. **Streaming UX** — should the dashboard stream agent transcripts live via SSE so the user can watch the build? Nice-to-have.
5. **Failure recovery** — if a node fails twice, fall back to a cheaper model or fail the build? Current design: fail the build, leave `status='failed'`, user retries.
