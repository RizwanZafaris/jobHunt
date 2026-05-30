# Phase 4 — Google OAuth + Supabase Auth setup (your config, before the frontend)

This is the **config you (the owner) do once** so the Phase-4 login/onboarding
frontend works end-to-end on its first Vercel preview. I (Claude) never handle
secrets — you paste keys into Supabase / Google / Vercel dashboards directly,
exactly like the Redis URL.

Estimated time: **~20–30 min.** Do the steps in order; later steps need IDs from
earlier ones.

> Throughout: **DASHBOARD_URL** = your Vercel production dashboard URL
> (e.g. `https://dashboard-<yourproj>.vercel.app` — the one that currently 200s).
> **SUPABASE_PROJECT** = your Supabase project (the same one the backend uses).

---

## Step 1 — Get your Supabase project's callback URL

1. Supabase dashboard → your project → **Authentication → Providers**.
2. Click **Google** (don't enable yet). Note the **Callback URL (for OAuth)** it
   shows — it looks like:
   ```
   https://<PROJECT_REF>.supabase.co/auth/v1/callback
   ```
   Copy it. **Google needs this exact string** in Step 2. (This is Supabase's
   callback, NOT your app's — Supabase brokers the OAuth handshake.)

---

## Step 2 — Create the Google OAuth app (Google Cloud Console)

1. Go to <https://console.cloud.google.com/> → create (or pick) a project, e.g.
   "jobHunt".
2. **APIs & Services → OAuth consent screen**:
   - User type: **External** → Create.
   - App name: `jobHunt` · User support email: your email.
   - Scopes: the defaults (`email`, `profile`, `openid`) are enough — no extra
     scopes needed. Save.
   - Test users: while the app is in **Testing** mode, add the Google emails
     allowed to sign in (yours + anyone testing). (You can **Publish** later for
     open signups; for first validation, Testing mode is fine.)
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Name: `jobHunt web`.
   - **Authorized JavaScript origins**: add
     - `https://<PROJECT_REF>.supabase.co`
     - your `DASHBOARD_URL` (production)
     - *(optional, for preview testing)* the specific Vercel preview URL once you
       have it — Google doesn't allow wildcard subdomains, so add the exact
       preview host you'll click through, OR just test on the production URL.
   - **Authorized redirect URIs**: add the **Supabase callback URL from Step 1**:
     ```
     https://<PROJECT_REF>.supabase.co/auth/v1/callback
     ```
   - Create → copy the **Client ID** and **Client secret**.

---

## Step 3 — Enable Google in Supabase

1. Supabase → **Authentication → Providers → Google** → toggle **Enabled**.
2. Paste the **Client ID** and **Client secret** from Step 2.
3. Save.
4. Supabase → **Authentication → URL Configuration**:
   - **Site URL**: your `DASHBOARD_URL` (production).
   - **Redirect URLs** (allow-list — add each on its own line):
     - `DASHBOARD_URL/auth/callback`
     - *(for preview testing)* `https://*-<yourproj>.vercel.app/auth/callback`
       — Supabase DOES allow wildcards here (unlike Google), so preview deploys
       can complete the round-trip. Confirm the pattern matches your preview
       host format.
   - `/auth/callback` is the route the Phase-4 frontend will add (it exchanges
     the OAuth `code` for a session). It does not exist yet — that's P4-2.

---

## Step 4 — Vercel env vars (dashboard project)

Vercel → your **dashboard** project → **Settings → Environment Variables**. Add
(Production + Preview both, so preview deploys work):

| Variable | Value | Notes |
|---|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://<PROJECT_REF>.supabase.co` | already used by the Phase-1 server client |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase → Settings → API → **anon/public** key | publishable; safe in the browser |
| `NEXT_PUBLIC_SINGLE_USER_MODE` | `0` | **flips multi-tenant ON for this project.** ⚠ see warning below |

> ⚠ **Do NOT set `NEXT_PUBLIC_SINGLE_USER_MODE=0` on your live production
> dashboard until the P4-2 frontend is merged AND you've signed in once** — with
> it `0` and no login UI yet, the proxy would stop sending `X-Secret-Key` and
> your dashboard would 401. **Safe path:** set it `0` only on the **preview**
> environment (or a separate preview project) to test P4-2, and flip production
> last. The frontend is built so production stays byte-for-byte unchanged while
> the flag is unset/`1`.

The backend (Railway) already has `SUPABASE_ANON_KEY` from Phase 1; if not, add
it there too (the per-request RLS client needs it in multi-tenant mode).

---

## Step 5 — Apply migration 045 (onboarding columns)

When ready (Supabase SQL editor, or the Supabase MCP), apply:
```
db/migrations/2026_05_31_045_user_onboarding.sql
```
Additive + idempotent; backfills the owner as already-onboarded so your live
dashboard never routes you into onboarding. Safe to apply now.

---

## Step 6 — Tell me "config done"

Once Steps 1–4 are set (5 optional-but-recommended), I build **P4-2**:
- `/login` + `/signup` pages (Google button → Supabase OAuth)
- `/auth/callback` route (code → session exchange)
- session-refresh **middleware** (gated on `NEXT_PUBLIC_SINGLE_USER_MODE` so prod
  is unchanged while the flag is off)
- route protection (unauthenticated → `/login` in multi-tenant mode)
- first-run **onboarding** flow writing through `POST /me/onboarding` (P4-1)
- sign-out

I'll `next build` (type-check) locally, push to a branch, and you confirm on the
**Vercel preview URL** before any merge to main. Production stays on
single-user mode until you explicitly flip it.

---

## What you do vs. what I do (summary)

| | You | Me |
|---|---|---|
| Google OAuth app + consent screen | ✅ | |
| Enable Google in Supabase + redirect URLs | ✅ | |
| Vercel env vars (keys) | ✅ | |
| Apply migration 045 | ✅ | |
| Login/signup/callback/middleware/onboarding code | | ✅ |
| `next build` type-check + push branch | | ✅ |
| Click through the Vercel preview to confirm | ✅ | |
| Merge to main + flip prod to multi-tenant | ✅ | |

Secrets (client secret, anon key) only ever go into Google/Supabase/Vercel
dashboards — never into the repo or this chat.
