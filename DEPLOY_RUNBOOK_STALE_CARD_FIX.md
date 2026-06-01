# Deploy Runbook — Adyen Stale-Card Fix + OpenRouter + Buffer

> Branch: `fix/adyen-stale-cards-plus-fallbacks`
> Target: 4–6 hour deploy window
> Risk: **Low** (additive only — no destructive migrations, all defaults safe)

---

## What's in the change

### 1. Adyen stale-card fix (`api/actions.py` + 2 migrations + dashboard)

Three independent forces that decay old cards on `/today`:

| Fix | What | File |
|---|---|---|
| 1. Age penalty | `_rank()` subtracts 1 point per day, capped at 30 | `api/actions.py` |
| 2. Dismiss/snooze | User-driven hide, RLS-enforced | migration 036 + 3 new endpoints |
| 3. Lifecycle penalty | `surface_count` × 2 penalty + dormant hide at 7+ | migration 037 + counter bump |

### 2. OpenRouter auto-fallback (`agents/llm_fallback.py`)

When Anthropic/OpenAI/Google return retriable errors (rate limit, overload, timeout), the same logical request automatically retries through OpenRouter. Zero behavior change when `OPENROUTER_API_KEY` is absent.

### 3. Buffer integration (`integrations/buffer_client.py` + `api/buffer.py` + migration 038)

OPT-IN per tenant. OAuth flow + scheduled posting to LinkedIn via Buffer. Draft must still pass G4's HITL `status='approved'` gate before it can schedule.

---

## Pre-deploy checklist

- [ ] Branch is at `fix/adyen-stale-cards-plus-fallbacks`
- [ ] `python3 -c "import ast; ast.parse(open('api/actions.py').read())"` returns 0
- [ ] `pytest tests/test_actions_stale_card_fix.py tests/test_llm_fallback.py tests/test_buffer_client.py` → 49/49 pass
- [ ] No env-var diffs needed for Fix 1–3 (defaults are safe)
- [ ] If using OpenRouter: `OPENROUTER_API_KEY` set in Railway
- [ ] If using Buffer: `BUFFER_CLIENT_ID`, `BUFFER_CLIENT_SECRET`, `BUFFER_REDIRECT_URI` set in Railway

---

## Step-by-step deploy

### Step 1 — Apply DB migrations to a Supabase branch first

```bash
# Create a Supabase branch (read-only mirror of prod) and grab its connection string.
# Then run only the new migrations:
DATABASE_URL="postgres://...branch...supabase.co:5432/postgres"

psql "$DATABASE_URL" -f db/migrations/2026_05_26_036_job_card_dismissals.sql
psql "$DATABASE_URL" -f db/migrations/2026_05_26_037_jobs_surface_tracking.sql
psql "$DATABASE_URL" -f db/migrations/2026_05_26_038_linkedin_buffer_integration.sql
```

Verify:

```sql
-- Migration 036
SELECT count(*) FROM job_card_dismissals;   -- expect 0
SELECT rowsecurity FROM pg_tables WHERE tablename = 'job_card_dismissals';  -- expect 't'

-- Migration 037 — backfill should have populated created_at for existing rows
SELECT count(*) FROM jobs WHERE first_surfaced_at IS NULL;  -- expect 0
SELECT max(surface_count), min(surface_count) FROM jobs;    -- expect 0, 0

-- Migration 038
SELECT count(*) FROM buffer_oauth_tokens;   -- expect 0
SELECT count(*) FROM linkedin_drafts WHERE buffer_status IS NULL;  -- expect (all rows)
SELECT buffer_autopost_enabled FROM profile_master LIMIT 1;        -- expect false
```

All three migrations are idempotent + transactional — safe to re-run.

### Step 2 — Apply to production Supabase

Same three commands against the prod `DATABASE_URL`. Total runtime: <2 seconds on the current dataset size.

```bash
DATABASE_URL="postgres://prod..." 
psql "$DATABASE_URL" -f db/migrations/2026_05_26_036_job_card_dismissals.sql
psql "$DATABASE_URL" -f db/migrations/2026_05_26_037_jobs_surface_tracking.sql
psql "$DATABASE_URL" -f db/migrations/2026_05_26_038_linkedin_buffer_integration.sql
```

### Step 3 — Set OpenRouter key (optional, recommended)

In Railway → Environment Variables:

```
OPENROUTER_API_KEY=sk-or-...
```

Sign up: https://openrouter.ai (top up $5 to start). When set, the fallback fires only on retriable errors — zero cost in steady-state.

### Step 4 — Set Buffer keys (optional, if you want auto-post)

In Buffer Developer Console (https://buffer.com/developers/apps) create an app; set callback URL to:

```
https://<your-vercel-domain>/buffer/oauth-callback
```

In Railway env:

```
BUFFER_CLIENT_ID=...
BUFFER_CLIENT_SECRET=...
BUFFER_REDIRECT_URI=https://<your-vercel-domain>/buffer/oauth-callback
```

### Step 5 — Deploy backend

```bash
git push origin fix/adyen-stale-cards-plus-fallbacks
# Open PR → merge to main → Railway auto-deploys
```

Watch Railway logs for:

```
INFO actions: dropped N dismissed/snoozed job(s) from /today
INFO actions: hid N dormant job card(s) (surface_count >= 7)
```

— these confirm Fix 2 + 3 are firing.

### Step 6 — Deploy dashboard

```bash
cd dashboard && vercel deploy --prod
```

Or auto-deploy on merge if you have GitHub→Vercel wired up.

### Step 7 — Manual smoke test against prod

| # | Action | Expected |
|---|--------|----------|
| 1 | Open `/today` | Cards render; stale Adyen card should show a "Nd" age pill |
| 2 | Refresh `/today` 3 times | After 3rd refresh, `×3` pill appears on the same cards |
| 3 | Click "×" on Adyen card → "Not interested" | Card disappears immediately (optimistic), confirmed gone on next refresh |
| 4 | Hit `GET /actions/today/dismissed` (via curl with `X-Secret-Key`) | Adyen row appears in dismissals list |
| 5 | DELETE the dismissal | `DELETE /actions/today/dismiss/{adyen_job_id}` returns 200, Adyen reappears |
| 6 | Click "Snooze 3 days" on another card | Returns instantly; card stays hidden until snooze expires |
| 7 | Refresh /today 7+ times on a card without acting | Card disappears (dormant); add `?show_all=true` to URL → reappears |

### Step 8 — Smoke test OpenRouter (optional)

Force a retriable error and confirm fallback fires:

```bash
# Temporarily remove ANTHROPIC_API_KEY in Railway, hit /actions/today,
# check Railway logs for: "LLM fallback firing: anthropic/... → openrouter/..."
# Then put the key back.
```

Or check `agent_call_log` after a normal run for `actual_provider='openrouter'` rows on days where Anthropic rate-limited.

### Step 9 — Smoke test Buffer (optional)

1. Open dashboard `/buffer/connect` (new route — needs your /buffer page; see TODO)
2. Click "Connect Buffer" → OAuth consent → redirect back
3. Verify `buffer_oauth_tokens` has 1 row, `revoked_at IS NULL`
4. Toggle "Auto-post enabled" → confirms `profile_master.buffer_autopost_enabled = true`
5. Open `/linkedin` → approve a draft → click "Schedule to Buffer" (new button — see dashboard TODO)
6. Verify draft row has `buffer_post_id` populated + `buffer_status='pending'` or `'scheduled'`

---

## Rollback plan

### If Fix 1–3 misbehave

The fixes are pure additive — nothing rolls back to. To soft-disable:

```python
# api/actions.py — bump these to zero and redeploy.
CARD_AGE_PENALTY_PER_DAY = 0
CARD_SURFACE_PENALTY_PER_VIEW = 0
CARD_DORMANT_THRESHOLD = 9999
```

`job_card_dismissals` filter still applies — but only to rows the user explicitly created. Pre-existing behavior preserved.

### If OpenRouter fallback misbehaves

Remove `OPENROUTER_API_KEY` from Railway → fallback gate fails closed → behavior reverts to current.

### If Buffer integration misbehaves

```sql
UPDATE profile_master SET buffer_autopost_enabled = false;
```

Existing OAuth tokens stay; users can re-enable when ready. To fully roll back, also:

```bash
# Revoke all OAuth tokens
UPDATE buffer_oauth_tokens SET revoked_at = now() WHERE revoked_at IS NULL;
```

### If migrations need reversal (very unlikely)

```sql
-- Migration 036 reversal (drops user dismissals — destructive)
DROP TABLE IF EXISTS job_card_dismissals;

-- Migration 037 reversal (drops surface counters — non-destructive)
ALTER TABLE jobs DROP COLUMN IF EXISTS first_surfaced_at;
ALTER TABLE jobs DROP COLUMN IF EXISTS last_surfaced_at;
ALTER TABLE jobs DROP COLUMN IF EXISTS surface_count;

-- Migration 038 reversal (drops Buffer tables + columns — destructive)
DROP TABLE IF EXISTS buffer_oauth_tokens;
ALTER TABLE linkedin_drafts
  DROP COLUMN IF EXISTS buffer_post_id,
  DROP COLUMN IF EXISTS buffer_status,
  DROP COLUMN IF EXISTS buffer_scheduled_at,
  DROP COLUMN IF EXISTS buffer_posted_at,
  DROP COLUMN IF EXISTS buffer_error,
  DROP COLUMN IF EXISTS buffer_permalink;
ALTER TABLE profile_master
  DROP COLUMN IF EXISTS buffer_autopost_enabled,
  DROP COLUMN IF EXISTS buffer_default_profile_id;
```

---

## Telemetry to watch (first 48h post-deploy)

### Healthy signal

- `agent_call_log.actual_provider` distribution shows `openrouter` ≤ 5% of total
- `/insights?tab=traces` shows ~0 fallback fires per healthy hour
- `/today` p99 latency unchanged (< 500 ms) — the new filters/joins are indexed
- Dismissals/day starts at 0–5 (organic discovery), settles 1–3/day per user

### Warning signs

- `actual_provider='openrouter'` jumps above 20% → primary provider is having a bad day (or your key is misconfigured)
- `/today` p99 > 1 s → check `idx_dismissals_active` and `idx_jobs_surface_count` exist (`\d+ jobs` in psql)
- Many users dismissing → either the discovery agent is producing noise, or the ranking is wrong (tune `CARD_AGE_PENALTY_PER_DAY`)
- Buffer `buffer_status='failed'` rate > 10% → Buffer API change or token expiry

---

## Known limitations

1. **Dashboard Buffer page not built.** The API is fully wired; you need a `/buffer` page in the dashboard with Connect / Disconnect / Profile picker. ~2 hours of frontend work.
2. **Snooze re-surface is silent.** When a 7-day snooze expires, the card reappears on `/today` without a "you snoozed this" hint. Acceptable for v1; nice-to-have for v1.1.
3. **Surface counter bump is fire-and-forget.** Race conditions can under-count by 1 per concurrent /today call. Non-issue for ranking.
4. **OAuth state storage is client-side.** The dashboard must persist `state` in a session cookie. If the cookie is lost between `start` and `callback`, the OAuth flow fails closed (returns 400). Documented in `api/buffer.py`.

---

## Files changed (single PR)

```
api/actions.py                                         (+~250 lines, patched)
api/server.py                                          (+~10 lines, router include)
api/buffer.py                                          NEW
integrations/__init__.py                               NEW
integrations/buffer_client.py                          NEW
agents/llm_fallback.py                                 NEW
agents/base_agent.py                                   (+~20 lines, fallback wire)
dashboard/src/lib/types/today.ts                       (+~4 lines)
dashboard/src/components/today/TodayActionCard.tsx     (+~130 lines, dismiss UI)
db/migrations/2026_05_26_036_job_card_dismissals.sql   NEW
db/migrations/2026_05_26_037_jobs_surface_tracking.sql NEW
db/migrations/2026_05_26_038_linkedin_buffer_integration.sql NEW
tests/test_actions_stale_card_fix.py                   NEW (22 tests)
tests/test_llm_fallback.py                             NEW (18 tests)
tests/test_buffer_client.py                            NEW (9 tests)
```

Total new code: ~1,800 lines. Total tests added: 49 (all passing).
