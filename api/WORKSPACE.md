# Application Workspace — Phase 2

Owner: backend (`api/workspace.py`) + frontend
(`dashboard/src/components/workspace/*`).
Status: **Phase 2 shipped.** Quick-tweak resume editor live; rebuild-section
and full-rebuild are stubs that 501 with a clear "next session" message.

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

## Rebuild-section + full-rebuild — TODO contract

The chat panel exposes 3 mode buttons. Phase 2 ships only the first.
The other two are present but disabled with a "Coming next session"
tooltip; the API returns 501 with a structured payload so the UI can
toast the message cleanly.

### Rebuild section (planned)

  • Mode key: `rebuild_section`.
  • Cost target: ~$0.50, ~2-3 min.
  • Backend: re-invokes the G2 LangGraph at the writer + critic nodes
    only, with a `warm_start_md` parameter pointing at the current
    user_edited_md. Persona gate is preserved. We need a new
    `resume_agents/g2_run.py::run_g2_section_rebuild()` entry that
    short-circuits the polisher pass once the section is stable.
  • Section identification: extract from instruction (e.g. "rebuild the
    Experience section") via a single Sonnet classifier, or take an
    explicit `section_id` from the UI if we add a dropdown.
  • Output: same `EditResumeResponse` shape so the UI doesn't branch.

### Full rebuild (planned)

  • Mode key: `full_rebuild`.
  • Cost target: ~$1, ~5 min.
  • Backend: this is just a thin wrapper around the existing
    `enqueue_g2_build(force=True)`. No new agent code. The UI will need
    to switch to the same poll loop the "Build my resume" button uses.
  • Pre-flight ask: a confirm dialog ("This costs ~$1 and 5 minutes.
    Use Quick tweak for surgical edits.") to prevent accidental triggers.

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
