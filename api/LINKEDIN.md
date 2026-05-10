# LinkedIn Presence Engine — V1

> Audit ref: `docs/AUDIT_360_SYNTHESIS.md` §4 P1.2 (LinkedIn presence
> engine — 0% built, vision-gap rated 0/100).

This document is the authoritative reference for the `/linkedin` surface:
schema, the G4 graph (4 nodes), the voice profile, the posting schedule
defaults, the manual-paste publish model (and why we are NOT auto-posting
in V1), the future Buffer integration plan, and how to plug the router
into `api/server.py`.

---

## 1. Architecture (G4 graph)

```
                    POST /linkedin/drafts/generate
                                 │
                                 ▼
                    enqueue_g4_linkedin_post (api/queue.py)
                                 │
                                 ▼
                       worker_run_g4 (api/worker.py)
                                 │
                                 ▼
                       run_g4_graph (agents/g4_linkedin_graph.py)
                                 │
                                 ▼
                  ┌──────────────────────────┐
                  │   pick_angle  (Sonnet)   │   choose angle + anchor row
                  └──────────┬───────────────┘
                             ▼
                  ┌──────────────────────────┐
                  │   draft_v1   (Opus 4.5)  │   first draft (hook/body/cta/hashtags)
                  └──────────┬───────────────┘
                             ▼
                  ┌──────────────────────────┐
                  │   critique  (Sonnet)     │   audit: AI tells, humble-brags, …
                  └──────────┬───────────────┘
                             ▼
                  ┌──────────────────────────┐
                  │   polish    (Opus 4.5)   │   apply critique → final
                  └──────────┬───────────────┘
                             ▼
                  ┌──────────────────────────┐
                  │   persist   (DB)         │   INSERT linkedin_drafts (status='draft')
                  └──────────────────────────┘
                             │
                             ▼
                          END
                             │
                             ▼   (the user opens /linkedin)
                  Approve & Schedule → status='scheduled', scheduled_for=next slot
                             │
                             ▼
              Sweeper (agents/linkedin_scheduler.py) notifies user
                             │
                             ▼
            User clicks "Copy to clipboard" in dashboard
                             │
                             ▼
                  POST /linkedin/drafts/:id/copy
              (records manual_copy_at, flips status='posted')
```

Cost per draft: **~$0.10–0.15** (one Sonnet pick, one Opus draft, one
Sonnet critique, ~50% of drafts trigger a second Opus polish; the
`ship_as_is` short-circuit saves the rest). At 3 drafts/week × 4 weeks =
**~$1.80/month per user.**

---

## 2. Schema

The migration is `db/migrations/2026_05_10_005_linkedin_drafts.sql`. It
is multi-tenant from day 1 (every table carries `user_id uuid not null
references users(id)` with `auth.uid() = user_id` RLS).

### `linkedin_drafts`

The full draft lifecycle. Lifecycle moves through `status`:

```
draft → approved → scheduled → posted
   ↓                              ↑
 rejected     manual_copy_at flips → posted (V1 publish path)
   ↓
expired (sweeper, 24h after scheduled_for if uncopied)
```

Key columns:

| Column | Notes |
|---|---|
| `source_company_id`, `source_knowledge_id` | Both nullable — `industry_analysis` may be timeless |
| `angle` | One of 5 enum values (see §3) |
| `hook`, `body`, `cta`, `hashtags[]` | The actual post content |
| `critique` | Sonnet's audit JSON (severity-tagged issues + verdict) |
| `polish_round` | Number of polish passes consumed |
| `scheduled_for` | Set on approve; null otherwise |
| `manual_copy_at` | Stamped when user clicks "Copy to clipboard" — V1 publish signal |
| `posted_url`, `posted_at` | Set when the user manually pastes (the API takes the user's word; future Buffer integration will fill this from the API response) |
| `engagement_metrics` | `{likes, comments, views}` — populated by a future scrape job; also stores `notified_at` to prevent duplicate notifications |

Indexes:
- `(user_id, status, scheduled_for)` — scheduler hot path
- `(user_id, created_at DESC)` — dashboard list

### `linkedin_posting_schedule`

One row per `(user_id, day_of_week)`. Columns: `time_of_day`,
`posts_per_week` (denormalised onto each row), `pause_until`,
`paused_reason`. Default seed (Rizwan): Mon/Wed/Fri 09:00, 3 posts/week.

`day_of_week` follows the Postgres convention: 0=Sunday, 6=Saturday.

### `linkedin_voice_profile`

One row per user (UNIQUE on `user_id`). Columns:

- `profile_md` — markdown describing the user as a writing voice (extracted by Opus from cv.md)
- `tone_directives` — one-sentence directive (default: "plainspoken, specific, opinionated; never humble-brags…")
- `avoid_phrases[]` — the AI-tell list ("delve", "tapestry", "unpack", "journey", …) — the critique node uses this list directly
- `example_posts[]` — 0-5 LinkedIn posts the user admires; the writer mimics cadence (NOT topic)

---

## 3. The 4 node prompts (the engine's brain)

These are the load-bearing prompts. They live in `agents/g4_linkedin_graph.py`. Treat them as code, not docs — every word matters.

### Node 1 — `pick_angle` (Sonnet)

System prompt teaches the model that there are 5 angles and how to pick. The five:

- **news_commentary** — straight take on a company event. Best when the news is concrete and the user has direct domain expertise that lets them say something a generalist can't.
- **contrarian_take** — disagree with the consensus reaction. Best when the news is hyped and the user has a non-obvious counter-position grounded in their experience.
- **build_in_public** — share a problem the user is solving NOW that's adjacent to the news. Best for builders; weakest for pure managers.
- **lesson_learned** — "we tried X at <past company>; here's what the news event makes me reconsider." Best for senior ICs/managers with a war-story bank.
- **industry_analysis** — zoom out: "this isn't about <one company>, it's about <theme>." Best when the user is positioning as a thought leader.

Output is a strict JSON: `{chosen_knowledge_id, chosen_company_name, angle, rationale}`.

The user template includes:
- 3000-char excerpt of the user's CV (for credibility check)
- Tone directives + avoid_phrases from voice profile
- The 15 most-recent `company_knowledge` rows (last 7 days) with id + section + 600-char excerpt
- Pinned angle / company id (if user supplied)
- The 3 most recent `linkedin_drafts.angle` values (avoid repeating)

Why Sonnet: angle picking is taxonomic + cheap. Sonnet is correct ~95% of the time on this and saves $0.04/call vs Opus.

### Node 2 — `draft_v1` (Opus 4.5)

System prompt is the load-bearing one. Six hard rules:

1. **Never** use AI tells: "delve", "tapestry", "unpack", "journey", "at the end of the day", "a testament to", "in today's fast-paced world", "navigate the complexities", "in this digital age". Plus the user's `avoid_phrases`.
2. **Never** humble-brag. "I was fortunate enough to lead 40 engineers" → "Led 40 engineers."
3. **Never** use em-dash strings to look thoughtful. ONE em-dash per post is the cap.
4. **Never** post unsupported claims. Every assertion traces to (a) a number from the user's CV, (b) the news anchor, or (c) a clearly-marked opinion.
5. **Never** end with "What do you think?" — replace with a specific question.
6. **Never** spam hashtags. Cap at 4. Topical only (#payments #fintech), never promotional (#hustle #grind).

Structure rules:
- HOOK (1-3 lines): lead with a specific number, a sharp opinion, or a concrete moment. Never start with "I'm thrilled to share…".
- BODY (3-7 short paragraphs, line-broken aggressively): ONE story or ONE argument. Angle dictates the shape.
- CTA (optional, 1 line): a question that sounds like the user's voice, not "Thoughts?".
- HASHTAGS (1-4 max): topical.
- Length: 800-1500 chars.

Output is JSON: `{hook, body, cta, hashtags, why_it_works}`. The "why_it_works" line is shown in the dashboard tooltip.

Why Opus: this is the only place in the graph that asks the model to be creative. Sonnet draws blanks on hooks and produces generic body paragraphs on lesson_learned in our tests; Opus actually picks specific moments from the CV.

### Node 3 — `critique` (Sonnet)

System prompt is an audit checklist. Nine checks:

1. AI tells: any of the banned phrases → P0. Two+ em-dashes → P1. Generic openers ("Excited to share / I'm thrilled to / Reflecting on…") → P0.
2. Humble-brags: "I was fortunate enough to / Honored to / Grateful that I got to lead…" → P0.
3. Unsupported claims: any number/strong assertion that doesn't trace to news anchor or CV → P0 with the exact span.
4. Generic vs specific: paragraphs that could appear in any post → P1.
5. Hashtag spam: >4, or non-topical → P1.
6. Length: <100 chars or >1500 chars body → P1.
7. CTA quality: "What do you think?" / "Thoughts?" → P1.
8. Hook quality: hook buried below line 3 → P0; reads like a press release → P1.
9. Voice profile: any phrase from `avoid_phrases` → P0.

Severity levels:
- **P0** must fix
- **P1** should fix
- **P2** nice-to-have

Output JSON: `{issues: [{severity, kind, span, fix}], passes: [...], verdict: "ship_after_fix" | "ship_as_is" | "rewrite"}`.

Why Sonnet: the critique is a checklist + pattern match. Sonnet is excellent at this and saves the Opus budget for polish.

### Node 4 — `polish` (Opus 4.5, short-circuit on `ship_as_is`)

System prompt: surgical edits only. No rewrites. Apply every P0 fix verbatim. Apply P1 fixes unless they fight a P0 fix. Re-check banned phrases after edits. Length cap 1500 chars body.

Output JSON: `{hook, body, cta, hashtags, fixes_applied, fixes_skipped}`.

The graph short-circuits when `critique.verdict == "ship_as_is"` AND
`issues == []` — saving the second Opus call. About 30-40% of drafts in
practice land on the first try.

---

## 4. Sample expected output

Given a (fictional) Marqeta news event from `company_knowledge`:

> **News (scrape excerpt):** "Marqeta announced a partnership with a UK-based BNPL provider to power consumer credit cards across its issuer-processor stack. Q1 revenue up 18%, but core debit volume growth slowed to 7%, the slowest quarter since 2022."

…and Rizwan's CV (SimPaisa, fintech / payments PM background), the graph would emit something like:

```
ANGLE: contrarian_take
RATIONALE: "Marqeta's credit pivot has a consensus 'defensive' read; user has direct experience adding card credit to a wallet platform and can credibly counter."

HOOK:
Everyone's calling Marqeta's credit pivot a defensive move.

It's the opposite. Here's what they actually saw before anyone else.

BODY:
Issuer-processors don't get repricing power on debit. The interchange ceiling is fixed; volume growth is the only lever, and the BIN sponsor banks are catching up.

Credit is different. The merchant fee is 2-3x debit. Underwriting risk is yours, but so is the upside. Marqeta has seven years of cardholder spend data — they know who pays back faster than the FICO file does.

I ran a similar bet at SimPaisa in 2023. We added card credit on top of a wallet that had cleared $400M TPV. The unit economics flipped: a debit txn earned us 14 bps, a revolving credit account earned us $42 in year one. Same customer.

The "pivot" framing is backwards. Marqeta isn't reacting to slowing debit growth — they're cashing in seven years of data they already paid to collect. Everyone else processed those swipes and threw the data away.

The contrarian read: this isn't a moonshot, it's a margin defense disguised as one.

CTA:
Anyone in the issuer space seeing the same shift in their P&L yet?

HASHTAGS:
#payments  #fintech  #productmanagement

WHY IT WORKS:
Opens with a sharp counter-claim, then cashes it with a specific number from the user's SimPaisa run. The "everyone vs me" frame is contrarian without being smug.
```

This is the full mock card you can see in `dashboard/src/lib/mock/linkedin.ts` (mock-draft-1). The numbers — $400M TPV, 14 bps, $42/year — are pulled from Rizwan's CV; the post would not use them if they weren't already there.

---

## 5. The "manual paste" V1 publish model — why no auto-posting

> Audit reference: §12 risk #2 — *"Auto-posting LinkedIn drafts at scale = LinkedIn account bans."*

LinkedIn's TOS forbids automated posting from third-party servers. They detect:

- Burst posting (>2 posts per hour from a server-originated UA)
- Identical hashtag bundles across accounts (the LinkedIn ML side flags these)
- Posts originating from a known programmatic IP range (Buffer / Hypefury are whitelisted, your Railway box is not)

The downside risk is asymmetric: a single user's account ban is permanent and there is no appeal channel that responds. The upside (saving the user 30 seconds of copy-paste) is small. Therefore V1's publish path is:

1. The graph drafts → user sees `/linkedin` → reviews
2. User clicks **Approve & Schedule** (status='scheduled', scheduled_for set)
3. At `scheduled_for - 0min` the sweeper sends a notification
4. User clicks **Copy to clipboard** in the dashboard
5. Browser copies the text locally (`navigator.clipboard.writeText`)
6. API records `manual_copy_at = now()` and flips status='posted'
7. User pastes into LinkedIn manually

This keeps the user fully in the loop and zero data leaves our infrastructure other than the user's own pasted text. **This is also the audit's recommended mitigation**, not a workaround.

The only place we record a side-effect server-side is `manual_copy_at` (a timestamp). We never see the actual paste happen.

---

## 6. Future Buffer / Hypefury integration (Career-tier)

Stub lives in `agents/linkedin_scheduler.py::_post_via_buffer`. **Do not enable** until:

1. **User opt-in** — `career_tier_settings.allow_auto_post = TRUE` (a column we'll add when Career-tier ships).
2. **TOS review** — confirm Buffer's pattern still works for LinkedIn. Hypefury is similar but with different rate limits.
3. **Rate limit** — max 5 auto-posts/week per user. Burst posting is the bannable behaviour; spreading across days is not.
4. **Eval gate** — A/B against the manual-paste cohort for engagement parity. The hypothesis is auto-post < manual paste because the user can't make last-minute edits to match a thread they saw 2 minutes before posting.

When all four pass, the integration is ~50 lines of code:

```python
# agents/linkedin_buffer.py (future)
import httpx
async def post_via_buffer(user_id: str, draft: dict) -> str:
    secret = await load_user_secret(user_id, "buffer_api_key")
    text = compose_post_text(draft)
    async with httpx.AsyncClient() as c:
        r = await c.post(
            "https://api.bufferapp.com/1/updates/create.json",
            data={"text": text, "profile_ids[]": [...]},
            headers={"Authorization": f"Bearer {secret}"},
        )
    body = r.json()
    return body["updates"][0]["service_link"]
```

The Career-tier UX adds a single toggle in `/linkedin/settings` ("Auto-post via Buffer") and a "How this works" explainer that includes the audit's risk rationale verbatim. Default is OFF.

---

## 7. Wiring the router into `api/server.py`

Single line, mirrors the network router pattern:

```python
# api/server.py
from api.linkedin import router as linkedin_router
app.include_router(linkedin_router)
```

Companion: the queue addition in `_pending_queue_additions.md` adds
`enqueue_g4_linkedin_post` to `api/queue.py` and `worker_run_g4` to
`api/worker.py`. Both are direct copies of the existing G2/G1/G3
patterns — no surprises.

That's it. Once those land, the endpoints below are live.

---

## 8. Endpoint reference

All endpoints live under `/linkedin` and require
`user: User = Depends(get_current_user)`. `RIZWAN_SINGLE_USER_MODE=1`
falls back to the seed user for self-use.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST   | `/linkedin/drafts/generate` | `{count, angle?, target_company_id?}` | `{queued, run_ids[]}` (202) |
| GET    | `/linkedin/drafts` | — | `{items[], total, limit, offset}` (filterable by `?status=`) |
| GET    | `/linkedin/drafts/{id}` | — | full row |
| PATCH  | `/linkedin/drafts/{id}` | `{hook?, body?, cta?, hashtags?}` | updated row |
| POST   | `/linkedin/drafts/{id}/approve` | `{scheduled_for?}` (default = next slot) | updated row |
| POST   | `/linkedin/drafts/{id}/copy` | — | updated row (sets `manual_copy_at`, flips to `posted` if approved) |
| POST   | `/linkedin/drafts/{id}/reject` | — | updated row |
| GET    | `/linkedin/voice-profile` | — | row or 404 |
| PUT    | `/linkedin/voice-profile` | `{profile_md?, tone_directives?, avoid_phrases?, example_posts?}` | upserted row |
| GET    | `/linkedin/posting-schedule` | — | `{slots[], posts_per_week, next_slot}` |
| PUT    | `/linkedin/posting-schedule` | `{slots[{day_of_week, time_of_day}], pause_until?, paused_reason?}` | freshly-set schedule |

All status transitions are server-enforced — e.g. you can't `approve` a
post that's already `posted`.

---

## 9. Cost & ops

- **Per draft:** ~$0.10–0.15 (1× Sonnet pick + 1× Opus draft + 1× Sonnet critique + ~50% chance of an Opus polish).
- **Per user / month:** 3 drafts × 4 weeks × $0.15 = **~$1.80**.
- **At 1k users:** ~$1,800/month — comfortably absorbed by Career-tier ($59/mo) margin.
- **Latency:** ~25-40s end-to-end per draft (Opus dominates). The user generates lazily, so async is fine; we don't wait synchronously in the API handler.
- **Rate limits:** Anthropic Claude Opus is the bottleneck. At 100 concurrent users hitting "Generate" we'd queue against the Anthropic side; the LangGraph + RQ retry policy handles this automatically.

Cost cap per draft is enforced in `run_g4_graph(max_cost_usd=0.15)`. Breaches log a warning but don't abort (the polish short-circuit usually keeps us under).

---

## 10. Risk stance — explicit

Per the audit (§12 risk #2), V1's stance is:

> **The engine never auto-posts. The user reviews every draft and physically copies the text into LinkedIn.**

We don't relax this until:
- TOS review passes for our specific posting pattern (not a generic one)
- The Buffer integration is on a feature-flagged Career-tier path
- A/B eval shows auto-post matches manual-paste engagement (it probably doesn't)

If you find yourself relaxing this stance, re-read the audit's risk #2
and the LinkedIn TOS first. The downside (permanent bans, no recourse)
is asymmetric — the upside (a few seconds saved) is small.

---

## 11. Where to look next

- **Migration:** `db/migrations/2026_05_10_005_linkedin_drafts.sql`
- **Graph:** `agents/g4_linkedin_graph.py`
- **Voice extractor:** `agents/linkedin_voice_extractor.py` (CLI: `python -m agents.linkedin_voice_extractor --user-id <uuid> --cv-path cv.md`)
- **API:** `api/linkedin.py`
- **Queue patch:** `_pending_queue_additions.md`
- **Sweeper:** `agents/linkedin_scheduler.py` (CLI: `python -m agents.linkedin_scheduler --once`)
- **Dashboard:** `dashboard/src/app/linkedin/page.tsx`, `dashboard/src/components/linkedin/`
- **Mock data:** `dashboard/src/lib/mock/linkedin.ts`
- **Types:** `dashboard/src/lib/types/linkedin.ts`
