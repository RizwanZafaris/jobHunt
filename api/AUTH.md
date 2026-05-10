# Auth — design and migration cookbook

Single-tenant → multi-tenant SaaS pivot. This document covers the
JWT verification stack landed in `api/auth.py`, `api/users.py`, and
`api/context.py`, plus the recipe for migrating the ~70 existing
endpoints in `api/server.py` to be tenant-scoped.

> **Status.** The middleware exists but is **not yet wired** to any
> endpoint. That is a follow-up PR (see "Migration cookbook" below).
> Today, every endpoint still runs without auth, exactly as before.

---

## 1. The model

```
                ┌─────────────────────────────┐
client request →│ Authorization: Bearer <jwt> │
                └─────────────┬───────────────┘
                              │
                ┌─────────────▼─────────────┐
                │ Depends(get_current_user) │
                └─────────────┬─────────────┘
                              │
       ┌──────────────────────┼─────────────────────────┐
       │                      │                         │
RIZWAN_SINGLE_USER_MODE=1   else: parse Bearer <jwt>    │
       │                      │                         │
return user_001 row     verify_supabase_jwt(token)      │
(auto-provision on      │                               │
first cold start)       │                               │
                        ▼                               │
              claims['sub'] → get_user_by_id           │
                        │                               │
                  exists? ─── yes → return User         │
                        │                               │
                        no → create_user(email=…)       │
                                │                       │
                                └───→ return User ──────┘
```

JWT verification: `python-jose`, HS256, `audience='authenticated'`,
secret from `SUPABASE_JWT_SECRET`. Issuer check is disabled —
Supabase's `iss` claim is project-scoped and we don't want to
hard-code project ids here.

Failure modes:

| Cause | HTTP | `detail` |
|---|---|---|
| `SUPABASE_JWT_SECRET` unset | 500 | `auth_misconfigured` |
| Missing/garbled Authorization header | 401 | `invalid_token` |
| Bad signature, wrong audience, malformed JWT | 401 | `invalid_token` |
| `exp` in the past | 401 | `token_expired` |
| Verified token but `sub` is not a UUID | 401 | `invalid_token` |
| Verified token, no `email` claim, user doesn't exist yet | 401 | `invalid_token` |
| `require_admin` and `is_admin=false` | 403 | `admin_required` |

---

## 2. RIZWAN_SINGLE_USER_MODE — keep self-use working

`RIZWAN_SINGLE_USER_MODE=1` is the **default**. With the flag set,
`get_current_user` short-circuits the JWT path entirely and returns
the seeded Rizwan row (id `00000000-0000-0000-0000-000000000001`,
email `rizwanzaffar.pk@gmail.com`). This means:

- self-use cron jobs, dashboard pings, and curl-without-auth
  continue to work as they did pre-pivot;
- per-tenant DB queries already behave correctly because every row
  ends up `WHERE user_id = '00…001'`;
- we can land middleware + per-endpoint scoping in separate PRs
  without ever breaking production self-use.

**Set `RIZWAN_SINGLE_USER_MODE=0` in a deployed environment** as the
final flip-the-switch step once:

1. every endpoint listed in §4 has been migrated;
2. the dashboard sends `Authorization: Bearer <supabase-jwt>` on every
   request;
3. `SUPABASE_JWT_SECRET` is set in Railway.

Until then, leave it at `1` (or omit it — `1` is the default).

### Optional: in-process JWT cache

Set `JWT_VERIFY_CACHE=1` *together with* `RIZWAN_SINGLE_USER_MODE=0`
to enable a tiny `lru_cache` over verified tokens (keyed by SHA-256
prefix + minute-bucket of `exp`). Useful for dev tooling that fires
many requests with the same token. **Off by default in prod** —
production wants a fresh signature/expiry check on every call.

---

## 3. Migration cookbook — wiring an endpoint

### 3a. The pattern

**Before:**

```python
@app.get("/jobs")
def list_jobs(limit: int = 50):
    return supa.table("jobs").select("*").limit(limit).execute().data
```

**After:**

```python
from api.context import get_current_user
from api.users import User

@app.get("/jobs")
def list_jobs(
    user: User = Depends(get_current_user),
    limit: int = 50,
):
    return (
        supa.table("jobs")
        .select("*")
        .eq("user_id", str(user.id))
        .limit(limit)
        .execute()
        .data
    )
```

Three mechanical edits per endpoint:

1. Add the dependency parameter `user: User = Depends(get_current_user)`.
2. Add `.eq("user_id", str(user.id))` to every `.table(...)` query.
3. For `INSERT` / `UPSERT`: include `"user_id": str(user.id)` in the row payload.

### 3b. Sed-style mechanical sketch

There is no fully reliable regex replacement — the dependency parameter
goes between existing args, and `.eq("user_id", …)` lands in different
places per query. Use these as a starting point and **always** read
each diff.

```bash
# Add the import once at the top of api/server.py:
from api.context import get_current_user, require_admin
from api.users import User

# Per-endpoint: tag each handler signature.
# Manual review required because handlers have varying arg lists.
# Approximate find:   ^def (list_jobs|get_job|...)\(
# Approximate insert at the start of args:
#   user: User = Depends(get_current_user),

# Per-query: scope reads.
# Common case (no other filters):
#   sed -E 's|\.table\("jobs"\)\.select\(([^)]*)\)|.table("jobs").select(\1).eq("user_id", str(user.id))|g'
# Caveat: misses queries already chained with .eq(); double-check by hand.
```

### 3c. Admin-only endpoints

For endpoints that should only run for `is_admin=true` users (cost
dashboards, alert checks, persona backfills), swap the dependency:

```python
@app.post("/personas/backfill-embeddings")
def backfill_personas(_admin: User = Depends(require_admin)):
    ...
```

In single-user mode Rizwan's row has `is_admin=true` (auto-provisioned
that way in `_ensure_rizwan`), so admin endpoints continue to run from
self-use without any extra setup.

### 3d. When the user id alone is enough

For endpoints that don't read other user fields, use the convenience
dependency:

```python
from api.context import get_current_user_id
from uuid import UUID

@app.get("/profile")
def get_profile(user_id: UUID = Depends(get_current_user_id)):
    return supa.table("rizwan_profile").select("*").eq("user_id", str(user_id)).execute().data
```

---

## 4. Endpoints to migrate

Every handler in `api/server.py` that touches a per-tenant table needs
the `Depends(get_current_user)` parameter and a `user_id` filter.
Public endpoints (`/`, `/health`) do not. The full inventory:

| Method | Path | Notes |
|---|---|---|
| GET    | `/` | public — keep as-is |
| GET    | `/health` | public — keep as-is |
| GET    | `/debug/apify-check` | already gated by `verify_secret` — convert to `require_admin` |
| GET    | `/debug/provider-ping` | already gated by `verify_secret` — convert to `require_admin` |
| POST   | `/pipeline/run` | needs `user` |
| POST   | `/pipeline/evaluate` | needs `user` |
| GET    | `/jobs` | needs `user` |
| GET    | `/jobs/{job_id}` | needs `user`, scope by user_id |
| GET    | `/companies` | needs `user` if companies become per-tenant |
| POST   | `/companies/build` | needs `user` |
| POST   | `/interview-prep` | needs `user` |
| GET    | `/digest/latest` | needs `user` |
| POST   | `/boss/audit` | needs `user` |
| POST   | `/boss/chat` | needs `user` |
| POST   | `/networking/strategy` | needs `user` |
| POST   | `/salary/research` | needs `user` |
| POST   | `/salary/evaluate-offer` | needs `user` |
| POST   | `/applications/review` | needs `user` |
| GET    | `/applications/pipeline` | needs `user` |
| GET    | `/resumes/{filename}` | needs `user` (file path under user's namespace) |
| GET    | `/resume-builds/by-job/{job_id}` | needs `user` |
| GET    | `/resume-builds/{build_id}` | needs `user` |
| GET    | `/resume-builds/{build_id}/markdown` | needs `user` |
| PATCH  | `/resume-builds/{build_id}/markdown` | needs `user` |
| POST   | `/resume-builds/{build_id}/feedback` | needs `user` |
| GET    | `/resume-builds/{build_id}/download` | needs `user` |
| GET    | `/pipeline/stats` | needs `user` |
| GET    | `/profile` | needs `user` |
| GET    | `/profile/keywords` | needs `user` |
| GET    | `/profile/sources` | needs `user` |
| PUT    | `/profile` | needs `user` |
| PUT    | `/profile/experience/{exp_id}` | needs `user` |
| GET    | `/profile/recommendations` | needs `user` |
| PUT    | `/profile/recommendations/{rec_id}` | needs `user` |
| POST   | `/profile/recommendations/regenerate` | needs `user` |
| GET    | `/companies/targets` | needs `user` |
| POST   | `/companies/targets` | needs `user` |
| PUT    | `/companies/{company_id}` | needs `user` |
| DELETE | `/companies/{company_id}` | needs `user` |
| GET    | `/companies/{company_name}/knowledge` | needs `user` (knowledge is per-tenant) |
| POST   | `/companies/research` | needs `user` |
| POST   | `/pipeline/run-targets` | needs `user` |
| POST   | `/jobs/reclassify` | needs `user` |
| POST   | `/jobs/{job_id}/generate-resume` | needs `user` |
| POST   | `/jobs/{job_id}/prep-interview` | needs `user` |
| GET    | `/jobs/{job_id}/detail` | needs `user` |
| GET    | `/applications` | needs `user` |
| POST   | `/applications` | needs `user` |
| PUT    | `/applications/{app_id}` | needs `user` |
| GET    | `/resumes/outcomes/by-job/{job_id}` | needs `user` |
| POST   | `/resumes/outcomes` | needs `user` |
| PATCH  | `/resumes/outcomes/{outcome_id}` | needs `user` |
| GET    | `/resumes/outcomes/conversion` | needs `user` |
| POST   | `/personas/synthesize` | likely `require_admin` (cross-tenant) |
| POST   | `/personas/backfill-embeddings` | `require_admin` |
| POST   | `/personas/deep-research` | `require_admin` |
| POST   | `/personas/refresh-news` | `require_admin` |
| POST   | `/personas/deep-research-batch` | `require_admin` |
| GET    | `/personas` | needs `user` (or keep cross-tenant + admin gate) |
| GET    | `/personas/{company_name}` | needs `user` |
| GET    | `/costs/summary` | needs `user` |
| GET    | `/costs/daily` | needs `user` |
| GET    | `/costs/by-provider` | needs `user` |
| GET    | `/costs/by-agent` | needs `user` |
| GET    | `/costs/health` | `require_admin` |
| GET    | `/costs/log-stats` | `require_admin` |
| POST   | `/costs/cleanup` | `require_admin` |
| POST   | `/alerts/check` | `require_admin` |
| POST   | `/alerts/weekly-digest` | `require_admin` |
| GET    | `/alerts/last` | `require_admin` |
| GET    | `/costs/by-resume-build` | needs `user` |
| GET    | `/costs/recent-calls` | needs `user` |

71 handlers in total. The "admin vs per-user" annotation is a starting
point — sanity-check each one when you migrate it.

---

## 5. Auto-provisioning rules

A user row is created the first time we see a verified Supabase JWT
whose `sub` we don't already have in our `users` table:

- `id` ← `claims['sub']` (UUID)
- `email` ← `claims['email']`
- `full_name` ← `claims['user_metadata']['full_name']` (or `name`),
  `NULL` if neither is present
- `plan` ← `'free'` (the table default — we don't override)
- `is_admin` ← `false` (the table default)

We use `UPSERT(on_conflict=email)` so two parallel first-time logins
for the same address don't blow up. The `email` UNIQUE constraint plus
the upsert give us idempotency.

The single Rizwan auto-provision in `_ensure_rizwan` is the lone
exception: it sets `plan='lifetime'` and `is_admin=true`, and pins
`id=RIZWAN_USER_ID`.

---

## 6. Sign-out and revocation

Supabase owns session lifecycle. The client calls
`supabase.auth.signOut()`; the server has nothing to revoke because we
hold no session state. A signed-out user's old JWT remains
cryptographically valid until its `exp` (default 1 hour) — this is the
standard Supabase trade-off. If we ever need hard revocation, the
options are:

- shrink JWT lifetime in Supabase Auth settings (e.g. 5 min);
- pair it with refresh-token rotation (already on by default);
- maintain a server-side blocklist keyed by `jti`.

We are deferring all three — 1-hour windows are fine for v1.

---

## 7. Running locally with auth on

Test the multi-tenant code path against the live Supabase project:

```bash
export RIZWAN_SINGLE_USER_MODE=0
export SUPABASE_JWT_SECRET=<from Supabase Settings > API > JWT Secret>

# Get a JWT for a real Supabase user (e.g. via supabase-js sign-in,
# or curl the /auth/v1/token?grant_type=password endpoint):
TOKEN="eyJhbGciOi..."

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/jobs
```

To go back to self-use:

```bash
unset RIZWAN_SINGLE_USER_MODE   # or set to 1
curl http://localhost:8000/jobs    # works without a token
```

---

## 8. Open assumptions

- We use the **service-role** Supabase client throughout, which
  bypasses RLS. RLS is defined in the migration but enforced only by
  application-level `WHERE user_id = …` filters today. A future PR
  should swap to a per-request user-scoped JWT client so RLS becomes
  a defence-in-depth rather than the only line.
- `verify_iss=False` is intentional. Add issuer pinning if/when we
  publish a stable `iss` from Supabase config.
- Auto-provisioning trusts the email claim from a verified JWT. That
  is safe given the JWT was signed by Supabase, which only issues
  tokens for confirmed addresses.
