# Interview Studio (Phase 3)

The Interview Studio is the user-facing surface for everything that happens
between **Applied ✓** and **Offer / Reject**. It binds three things together:

1. The **G3 prep pack** (already shipped) — likely questions, STAR stories,
   company hooks, red flags, salary notes.
2. A **chat tutor** that walks the user through the pack at their chosen
   depth (basics → intermediate → advanced) and cites the pack inline.
3. An **outcome → persona evolution loop** that turns each round outcome
   into measurable credit on the company knowledge rows that fed the
   resume that produced the application.

This is the **#1 unfilled wedge** in the audit
(`docs/AUDIT_360_SYNTHESIS.md` §4 P1.3 outcome-conditioned RAG;
§3 P1.5 persona evolution measured).

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  /applications/[id]/interview-studio       (Next.js Server Component)    │
│                                                                          │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │ PrepMaterial    │  │ TutorChat        │  │ OutcomeLogger            │ │
│  │  (40%)          │  │  (35%)           │  │  (25%)                   │ │
│  │                 │  │  ConceptLadder   │  │   round# / type          │ │
│  │  likely Qs      │  │   basics ←→      │  │   passed yes/no/?        │ │
│  │  STAR stories   │  │   intermediate   │  │   questions chips        │ │
│  │  company hooks  │  │   ←→ advanced    │  │   feedback notes         │ │
│  │  red flags      │  │                  │  │   "Save outcome"         │ │
│  │  salary notes   │  │   bubbles        │  │                          │ │
│  │                 │  │   shimmer        │  │                          │ │
│  │  "Ask tutor →"  │──→  prefill input   │  │                          │ │
│  │  "Move to →"    │──→  set ladder      │  │                          │ │
│  └─────────────────┘  └────────┬─────────┘  └────────────┬─────────────┘ │
└───────────────────────────────┼─────────────────────────┼───────────────┘
                                │                         │
                fetchInterviewStudio /  tutorChat /  logOutcome /
                                │                         │
                       (dashboard/src/lib/api/studio.ts)
                                │                         │
┌───────────────────────────────▼─────────────────────────▼───────────────┐
│  api/interview_studio.py — FastAPI router (/interview-studio/{app_id})   │
│                                                                          │
│  GET  /                              load_application + prep + outcomes  │
│                                      + last 50 tutor messages            │
│  POST /tutor-chat                    agents/interview_tutor.tutor_respond│
│                                      (persists user + tutor messages)    │
│  POST /log-outcome                   INSERT interview_outcomes →         │
│                                      agents/outcome_to_persona.credit_   │
│                                      outcome (inline, pure DB)           │
│  POST /build-prep-pack               api.queue.enqueue_g3_interview_prep │
└─────────────────────┬────────────────────────────┬───────────────────────┘
                      │                            │
                      │                            │
┌─────────────────────▼─────────┐  ┌───────────────▼─────────────────────┐
│ agents/interview_tutor.py     │  │ agents/outcome_to_persona.py        │
│ (Claude Opus 4.5, 1 call)     │  │                                     │
│                               │  │  credit_outcome(id, kind)           │
│  TutorState → tutor_respond → │  │    1. load outcome row              │
│  TutorResult                  │  │    2. trace → resume_build →        │
│                               │  │       cited knowledge_ids           │
│  cap: $0.20/turn              │  │    3. INSERT knowledge_outcome_     │
│                               │  │       credits (delta capped ±0.5)   │
│                               │  │    4. UPDATE company_knowledge.     │
│                               │  │       outcome_score (clamped 0..1)  │
│                               │  │                                     │
│                               │  │  evolve_persona(persona_id)         │
│                               │  │    1. snapshot → persona_versions   │
│                               │  │    2. Opus 4.5 proposes ATS-bank /  │
│                               │  │       success / failure deltas      │
│                               │  │    3. UPDATE company_personas       │
│                               │  │       persona_version++             │
└───────────────────────────────┘  └─────────────────────────────────────┘
```

## The Tutor System Prompt

The exact prompt baked into `agents/interview_tutor.py::TUTOR_SYSTEM_PROMPT`
(formatted with `round_type`, `company`, `concept_level`):

> You are an interview tutor for a senior fintech / payments PM. The user
> is preparing for {round_type} at {company}. Their starting concept level
> is {concept_level} (basics → intermediate → advanced). Walk them through
> the topic at the requested depth. Cite the prep pack's STAR stories and
> company hooks. If they want to escalate ('move to advanced'), step up.
> If they're stuck, drop down.
>
> You have access to the candidate's prep pack — likely questions, STAR
> stories, company hooks, red-flag questions, and salary-negotiation notes
> — produced by the G3 graph. Use them. When you reference a question or
> a STAR story, name it explicitly so the user can find it in the left
> pane.
>
> Operating rules:
>
> 1. ANCHOR EVERYTHING. Every answer must cite at least one item from the
>    prep pack. If nothing fits, say so plainly and suggest the user fill
>    the gap with their own example.
> 2. RESPECT THE LADDER (basics: plain language + worked example;
>    intermediate: tradeoffs; advanced: spar + contrarian take).
> 3. ESCALATE / DE-ESCALATE EXPLICITLY when the user's depth shifts.
> 4. NEVER FAKE EVIDENCE.
> 5. KEEP IT TIGHT — max 4 short paragraphs or a worked-example walkthrough.
> 6. END WITH A NEXT QUESTION (one specific follow-up).
>
> Output strict JSON: `{response, suggested_next_question,
> concept_level_recommended, cited_section}`.

## Credit-Assignment Math

The audit's hard rules — duplicated verbatim in
`agents/outcome_to_persona.py`:

| Event                               | Δ per cited knowledge row |
|-------------------------------------|---------------------------|
| Interview round passed              | `+0.05`                   |
| Interview round failed              | `-0.02`                   |
| Resume — recruiter responded        | `+0.04`                   |
| Resume — recruiter rejected         | `-0.01`                   |
| Offer received                      | `+0.10`                   |

Hard caps:

- Per-event `delta` is bounded `[-0.5, +0.5]` (the column CHECK).
- Aggregate `outcome_score = clamp(0.5 + mean(delta_i), 0, 1)`.
- The `0.5` anchor is "no signal yet" (Beta(α=1, β=1) prior expectation).

The aggregate is recomputed after every credit insert and written back to
`company_knowledge.outcome_score` + `outcome_credit_count`.

When `search_company_knowledge_v2` ships (separate migration), retrieval
re-ranks with `score = 0.7 * cosine + 0.3 * (outcome_score - 0.5)` per
audit §4 P1.3.

## Outcome → Persona Evolution Flow

```
[user logs interview round outcome]
        │
        │  POST /interview-studio/{app_id}/log-outcome
        ▼
INSERT interview_outcomes (UNIQUE on (application_id, round_number))
        │
        │  inline call: credit_outcome(outcome_id, 'interview')
        ▼
   load outcome row
        │
        │  trace via application_id → resume_build_id (most recent)
        ▼
   load resume_build.agent_transcript
        │
        │  parse `cite:knowledge_id=<uuid>` tokens
        │  fallback: top-5 most recent company_knowledge for company
        ▼
   for each knowledge_id:
      INSERT knowledge_outcome_credits (delta = ±0.05/0.04/0.02/0.01/0.10)
      UPDATE company_knowledge.outcome_score = clamp(0.5 + mean(deltas), 0, 1)
        │
        │  (returns credit_summary to UI: "n knowledge rows updated")
        ▼
       …time passes…
        │
        │  Sunday cron OR manual trigger:
        │  python -m agents.outcome_to_persona --persona-id <uuid>
        ▼
evolve_persona(persona_id):
   load company_personas + recent knowledge_outcome_credits joined to
        rows for that company
   snapshot CURRENT persona to persona_versions (with diff_summary)
   Opus 4.5 proposes new ats_keyword_bank / success / failure
   UPDATE company_personas
   persona_version++
```

## Migration apply order

**Migration 008 must apply AFTER 007** (the most recent applied
migration). Apply order:

```
001 multi_tenancy
  → 002 status_enum
  → 003 jobs_runs
  → 004 referral_graph
  → 005 linkedin_drafts
  → 006 linkedin_drafts_image_brief
  → 007 jobs_posting_closed_at
  → 008 outcome_credits   ← THIS PHASE
```

Hard prerequisite: 001 must have populated `user_id` on
`interview_outcomes`, `resume_outcomes`, `company_knowledge`,
`company_personas`, and `interview_prep` (it does — backfilled to
`user_001`).

## server.py wire-up

The Phase 3 contract forbids touching `api/server.py`. The required
include line is in **`_pending_server_includes_phase3.txt`** at the repo
root:

```python
from api.interview_studio import router as interview_studio_router  # noqa: E402
app.include_router(interview_studio_router)
```

Rizwan applies this manually after migration 008 lands.

## Files

| File | Lines | Purpose |
|---|---|---|
| `db/migrations/2026_05_10_008_outcome_credits.sql` | 280 | 3 new tables + 2 columns on company_knowledge |
| `agents/interview_tutor.py` | 290 | Tutor chat agent (Opus 4.5, $0.20/turn cap) |
| `agents/outcome_to_persona.py` | 470 | credit_outcome + evolve_persona + CLI |
| `api/interview_studio.py` | 360 | FastAPI router (4 endpoints) |
| `dashboard/src/lib/types/interview-studio.ts` | 165 | TS types mirroring Pydantic shapes |
| `dashboard/src/lib/api/studio.ts` | 110 | API client (proxy/direct dual-mode) |
| `dashboard/src/app/applications/[id]/interview-studio/page.tsx` | 75 | Server component entry |
| `dashboard/src/components/interview-studio/StudioClient.tsx` | 165 | Three-pane shell |
| `dashboard/src/components/interview-studio/PrepMaterial.tsx` | 285 | Prep pack collapsible sections |
| `dashboard/src/components/interview-studio/ConceptLadder.tsx` | 60 | Three-rung selector |
| `dashboard/src/components/interview-studio/TutorChat.tsx` | 240 | Chat UI with shimmer + suggested chip |
| `dashboard/src/components/interview-studio/OutcomeLogger.tsx` | 270 | Round outcome form + chip input |
| `_pending_server_includes_phase3.txt` | 25 | Wire-up instructions |
| `api/INTERVIEW_STUDIO.md` | this file | Architecture doc |
