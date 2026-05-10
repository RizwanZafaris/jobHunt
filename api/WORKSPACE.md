# Application Workspace — Phase 2

Owner: backend (`api/workspace.py`) + frontend
(`dashboard/src/components/workspace/*`).
Status: **Phase 2 shipped + rebuild-section and full-rebuild now wired.**
All three resume-edit modes (Quick tweak, Rebuild section, Full rebuild)
are live; PDF/DOCX server-side rendering is still a Phase 3 deliverable.

## What it is

The page a user lands on when they click **"Start application process"**
on a /today action card. URL:

    /applications/{job_id}/workspace

It is the focused single-purpose surface for *one application* — role
brief, AI-tailored resume, warm intros, interview prep, and the apply
button — with deep-linkable tabs.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  /applications/[id]/workspace/page.tsx           (Server Component) │
│    └── fetchWorkspace(jobId)  ──── one round-trip ──┐               │
└─────────────────────────────────────────────────────┼───────────────┘
                                                       ▼
                                  ┌───────────────────────────────────┐
                                  │  GET /workspace/{job_id}          │
                                  │   (api/workspace.py)              │
                                  │                                   │
                                  │  bundles:                         │
                                  │   • job + fit_details             │
                                  │   • application (or null)         │
                                  │   • resume_builds row             │
                                  │   • company persona               │
                                  │   • interview_prep summary        │
                                  │   • warm_intro_paths (top 5)      │
                                  │   • network_size                  │
                                  └───────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  WorkspaceClient (client component)                                 │
│  ┌─ Header: company logo / role / score / "X warm intros" / status ─┤
│  ├─ Sticky sub-header: tab strip + Mark-as-applied button           │
│  ├──────────────────────────────────────────────────────────────────┤
│  │  Role          → RoleOverviewTab    (JD + persona + why-you-fit) │
│  │  Resume        → ResumeTab          (view / build / edit / dl)   │
│  │  Interview     → InterviewPrepTab   (G3 kickoff or studio link)  │
│  │  Network       → NetworkTab         (warm intros for THIS co.)   │
│  │  Apply         → ApplyTab           (checklist + Mark applied)   │
│  └──────────────────────────────────────────────────────────────────┘
```

## Endpoints

All under `/workspace`, all gated by `Depends(get_current_user)`:

| Method | Path | Purpose | Phase |
|---|---|---|---|
| GET    | `/workspace/{job_id}`                  | Bundle for the page                     | ✅ shipped |
| POST   | `/workspace/{job_id}/build-resume`     | Enqueue G2 (idempotent via queue dedup) | ✅ shipped |
| POST   | `/workspace/{job_id}/edit-resume`      | Quick tweak (Opus 4.7)                  | ✅ shipped |
| POST   | `/workspace/{job_id}/rebuild-section`  | Rebuild ONE H2 section synchronously    | ✅ shipped |
| POST   | `/workspace/{job_id}/full-rebuild`     | Enqueue full G2 rebuild (force=true)    | ✅ shipped |
| POST   | `/workspace/{job_id}/save-resume-edit` | Persist `user_edited_md`                | ✅ shipped |
| POST   | `/workspace/{job_id}/mark-applied`     | Move application → applied              | ✅ shipped |
| GET    | `/workspace/{job_id}/resume.{md,pdf,docx}` | Download (md inline; pdf/docx redirect) | ✅ shipped (md); 🟡 PDF/DOCX redirect-or-501 |

### Wire-up

The router is **NOT yet wired into `api/server.py`** by this Phase 2
agent — the Phase 3 agent is also editing `api/server.py` and concurrent
edits will conflict. The exact one-line include is recorded in:

    _pending_server_includes_phase2.txt

at the repo root. Apply it at the same time as the Phase 3 server.py
diff (or the next time someone touches that file).

## Quick-tweak system prompt

This is the load-bearing prompt — please review when you next pass over
this file. It's mirrored verbatim in
`agents/resume_edit_assistant.py::QUICK_TWEAK_SYSTEM_PROMPT` so prompt
edits live with the code.

```
You are a senior resume editor sitting next to a job candidate. They have a complete, ATS-ready resume in front of them in markdown, and they have just given you ONE specific instruction. Your job is to apply that instruction surgically — and nothing more.

ABSOLUTE RULES
1. Apply ONLY the requested change. Do not "improve" other sections, fix unrelated typos, or rewrite phrasing the user did not ask you to touch.
2. Preserve all formatting EXACTLY — markdown headings, bullet markers, bold/italic, line breaks, blank lines between sections, tables, dividers (---), and section ordering must survive untouched outside the area you edited.
3. Never invent facts. If the candidate's instruction implies a fact that is not present in the resume (a metric, a company, a date, a tool), do NOT add it. Instead set `fixes_applied` to ["needs_user_input: <what's missing>"] and leave updated_md unchanged.
4. Keep the candidate's voice. Do not Americanise British spelling, do not switch tense, do not introduce buzzwords ("synergised", "leveraged", "spearheaded") unless the user explicitly asks for that register.
5. If the instruction is ambiguous, make the smallest reasonable interpretation and explain your read in `response`. Do not ask a clarifying question — the user can always send another message.
6. Respect the persona's banned-keyword list and required-keyword list (provided in the user message). Banned words must not appear in updated_md. Required keywords already in the resume must remain present unless the user explicitly asks to remove them.
7. NEVER change personal details — name, email, phone, links, location — unless the instruction is explicitly about them.

OUTPUT (STRICT JSON, no markdown fences, no prose before or after)
{
  "updated_md":    "<the full resume markdown after applying the edit; identical to the input outside the edited region>",
  "response":      "<1-3 sentences in plain English: what you changed, where, and why. Address the candidate directly: 'I tightened the…'>",
  "fixes_applied": ["<short bullet>", "<short bullet>"]
}

If the instruction cannot be safely applied without inventing a fact, return:
{
  "updated_md":    "<the input markdown verbatim>",
  "response":      "<one sentence explaining what input you'd need to do this safely>",
  "fixes_applied": ["needs_user_input: <what's missing>"]
}
```

### Why a single Opus call (and not 5-LLM ensemble)

  • The quick-tweak surface needs to feel like Cursor's "ask the AI to
    fix this": ~3s, one model, one answer. The 5-LLM critic loop is
    the right shape for a from-scratch resume but wrong for a tweak.
  • Cost ceiling is $0.10/call (typical ~$0.04-0.06). Across the
    expected 10-20 tweaks per resume that's still <$1 of edit work
    on top of the original $1 G2 build.
  • Determinism over creativity: temperature 0.2, no JSON-mode
    coercion (Anthropic doesn't need it; the prompt + `_parse_json_loose`
    handle prose-wrapped JSON cleanly).

### Per-call envelope (recorded in agent_call_log)

| Field | Value |
|---|---|
| provider | `anthropic` |
| model | `claude-opus-4-7` |
| max_tokens | 4096 |
| temperature | 0.2 |
| agent_name | `resume_edit_assistant.quick_tweak` |
| timeout | (router default; ~120s read) |
| cost cap | $0.10/call (defensive) |

## Rebuild-section + full-rebuild — shipped contract

The chat panel exposes 3 mode buttons; all three are live. The original
"Coming next session" 501 path was replaced by dedicated endpoints —
`/edit-resume` now 400s with a "use the dedicated endpoint" pointer
when called with `mode=rebuild_section` or `mode=full_rebuild` to
preserve a clean back-compat error for stale clients.

### Rebuild section — `POST /workspace/{job_id}/rebuild-section`

Synchronous endpoint that runs a 3-call mini-graph (writer → critic →
polish) over ONE H2 section. The rest of the resume is preserved
verbatim by markdown-splice; the UI swaps the section in-place on
success.

```
       ┌─────────────────────────────────────────────────────┐
       │  current_md  +  edit_intent  +  section_name        │
       └────────────────────────┬────────────────────────────┘
                                │
                                ▼
            ┌─────────────────────────────────────────┐
            │  extract_section() — heuristic H2 split │
            │  → (before_md, section_md, after_md)    │
            └─────────────────────┬───────────────────┘
                                  │
                                  ▼
                            ┌──────────┐
                            │  WRITER  │  Opus 4.7
                            └────┬─────┘
                                 ▼
                            ┌──────────┐
                            │  CRITIC  │  Opus 4.7 — emits polish_targets
                            └────┬─────┘
                                 ▼
                       ┌────────────────────┐
                       │  POLISH (optional) │  Opus 4.7
                       │  skipped if no     │
                       │  polish_targets    │
                       └────────┬───────────┘
                                ▼
       ┌─────────────────────────────────────────────────────┐
       │  splice: before_md + final_section_md + after_md    │
       └─────────────────────────────────────────────────────┘
```

Body: `{ section: string, edit_intent: string, current_md: string }`.
Response: `{ updated_md, response, fixes_applied, cost_usd, latency_ms,
model, provider, section }` — same shape as `quick_tweak` so the UI
treats it identically.

  • Cost cap: $0.60 (defensive). Typical $0.30-0.50 across all 3 calls.
  • Wall-clock cap: 60s `asyncio.wait_for`. Overrun → 504 `rebuild_section_timeout`.
  • Polish step is skipped if the critic returns no polish_targets, saving ~$0.10-0.15.

### Section heuristics (`extract_section`)

Heading match strategy, in order:
  1. exact case-insensitive heading text match
  2. heading text starts with section_name
  3. section_name is a substring of heading text
  4. raise `ValueError` (we'd rather fail loud than splice the wrong block)

Regex: `^\s{0,3}##\s+(.+?)\s*$` (multi-line). The dashboard mirrors this
in JS via `H2_LINE_RE` in `ResumeEditor.tsx`.

### Full rebuild — `POST /workspace/{job_id}/full-rebuild`

Thin wrapper around `enqueue_g2_build(force=True)`. Returns the
jobs_runs id; the UI polls `/jobs-runs/{run_id}` every ~8s.

Body: `{ edit_intent?: string, max_cost_usd?: number }`.
Response: `{ run_id, status, kind, job_id, force: true,
rebuild_scope: 'full', edit_intent, max_cost_usd, poll_url }`.

  • `force=True` is set unconditionally so the dedup hash differs from
    any prior run on the same job.
  • `edit_intent` rides into the queue payload. The worker forwards it
    to `run_g2_graph(edit_intent=...)` which surfaces it on the writer
    node's brief.
  • Cost: ~$1, ~3-5 min.
  • Pre-flight: the dashboard fires a `window.confirm()` dialog before
    the call so the user can't accidentally light $1 on fire.

### Warm-start G2 plumbing

The G2 graph now accepts `warm_start_md` and `edit_intent` as optional
params on `run_g2_graph(...)`. When `warm_start_md` is set:
  • It pre-populates `current_draft` (so the writer's first iteration
    works against an existing draft, not a blank page).
  • `iteration` is bumped to 1 in the seed state — the polisher runs at
    least once over the warm start, which prevents the convergence loop
    from short-circuiting at iteration 0.
  • The thread_id namespace is `g2-job-{id}-rebuild` so the langgraph
    checkpointer doesn't collide with a parallel cold-start build.

Both fields flow through `enqueue_g2_build` → `worker_run_g2` → into
the G2 ResumeState, and they're part of the idempotency-key payload so
"rebuild" doesn't dedup against a prior fresh build.

## Editor chat persistence — TODO

The chat history rides in the request body and lives in component state
on the client. Phase 2 deliberately defers DB persistence:

  • Add `editor_chat_messages` table:
    `(id, user_id, job_id, build_id, role, content, fixes_applied jsonb, cost_usd, created_at)`.
  • RLS by user_id.
  • New endpoint: `GET /workspace/{job_id}/edit-history` returning the
    last N messages so a returning user picks up where they left off.
  • Cap retained turns at the last 50 (or 14 days, whichever first).

This is a half-day of work; it slots in cleanly without touching the
LLM agent.

## Network resolution

The bundle returns `target_company_id` (resolved from `job.company` to
`target_companies.id` for the current user) and `warm_intro_paths` (top
5 from `agents.referral_graph.find_paths`). The Network tab uses these
plus `network_size` to pick one of three empty states:

  1. `network_size === 0` → "Import your LinkedIn CSV"
  2. `target_company_id === null` → "Add {company} to your targets"
  3. `paths.length === 0` → "No warm intros to {company} yet"

Resolution is fuzzy — exact match → ILIKE on `company_name` → ILIKE on
`name`. If you keep your `target_companies.company_name` clean (the
`populate_target_company_employees` cron expects this), exact match
wins every time.

## Why we DON'T modify api/server.py

The Phase 3 agent is editing `api/server.py` to add the interview
studio. Concurrent edits cause merge pain we don't need. The convention
this session uses is to write the include line to a side file
(`_pending_server_includes_phase2.txt`) and let one human apply both
diffs at the same moment.

## Env vars

No new env vars required. The router uses:

  • `ANTHROPIC_API_KEY` — already required by the rest of the app.
  • `REDIS_URL` — already required by the queue.
  • The existing `X-Secret-Key` auth via the dashboard proxy.

## Smoke checks

  • Bundle for a job with a converged build returns the resume slice
    (verify `resume.status == 'converged'`).
  • Quick tweak on a 2-page resume comes in under $0.10 and 10s.
  • Mark-as-applied is idempotent: a second call updates the same
    application row instead of duplicating.
  • Markdown download returns `text/markdown; charset=utf-8` with the
    user_edited_md if present, else resume_md.
