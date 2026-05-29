# jobHunt — Scalability Review & Hardening Roadmap

**Date:** 2026-05-29 · **Lens:** what breaks as jobHunt goes from **1 user (today)** to a **multi-tenant SaaS** with thousands of users and concurrent agent workloads.

This is a CTO-level review of the whole system (API, data layer, agent/LLM runtime, queue/worker, frontend, infra). Findings are graded **P0** (scale blocker / correctness), **P1** (serious degradation), **P2** (efficiency / hygiene) and tagged **✅ Fixed** (this pass) or **📋 Recommended** (sequenced below).

> **Why not "fix everything now"?** Several top findings are multi-week architectural migrations (async data layer, full RLS + per-user auth, composite-key tenancy) that would **break the live single-user system** if rushed, or need a topology/cost decision. The responsible move was to ship the safe, high-value fixes immediately (verified + tested) and sequence the rest. See **§4 Roadmap**.

---

## 1. Executive summary — the five things that block multi-tenant scale

1. **Cron double-fires on every API replica.** The 6 APScheduler jobs run *inside* the API process with no leader election. Scale the API to ≥2 replicas and every cron (job scout, boss audit, cost digest…) fires N× — N× LLM spend, duplicate writes/emails. **(✅ Fixed — gated + dedicated service path.)**
2. **Tenant isolation is app-code-only.** The API connects with the Supabase **service-role key, which bypasses RLS**, so isolation depends on every query remembering `.eq("user_id")`. Many core `server.py` endpoints don't. Plus **global `UNIQUE` constraints** (`companies.name`, `jobs.url`) mean a 2nd tenant *overwrites* the 1st's rows. **(📋 Phase 1 — security/correctness.)**
3. **The API blocks its own event loop.** `supabase-py` is synchronous; ~all of the 160+ async handlers call it directly. One slow query stalls every in-flight request on that worker. **(📋 Phase 2 — async data layer.)**
4. **Background throughput is capped at one job at a time.** `WORKER_CONCURRENCY=1` on a single FIFO queue → head-of-line blocking, zero per-tenant fairness; one tenant's batch starves everyone. **(📋 Phase 2 — queue split + autoscale.)**
5. **The failure-recovery path was dead.** RQ retry was documented but never wired, and the orphan reaper crashed every tick on a `NameError` — so transient failures became permanent and stuck jobs were never rescued. **(✅ Fixed — reaper bug + docs.)**

---

## 2. ✅ Fixed in this pass (safe, high-value, tested)

| # | Fix | Files | Why it matters at scale |
|---|-----|-------|--------------------------|
| 1 | **Scheduler scale-guard** — `SCHEDULER_ENABLED` env gate (defaults ON, so single-replica prod is unchanged → no BUG-053 regression) + a real dedicated **`scheduler`** service in `railway.toml`. To scale the API horizontally: deploy the scheduler service and set `SCHEDULER_ENABLED=0` on `api`. | `api/server.py`, `railway.toml` | Removes the #1 blocker to running >1 API replica without N× cron fan-out. |
| 2 | **Orphan-reaper `NameError` fix** — bound the `db` client in `reap_orphans()`; it was referenced before assignment, crashing every tick *before* `find_orphans()` ran. | `api/orphan_reaper.py` | Restores the **only** recovery path for stuck/orphaned jobs (the documented retry mechanism). |
| 3 | **Retry docs corrected** — the docstring claimed RQ-native `Retry` (never wired); recovery is actually the reaper re-enqueuing `queued`/`running` rows. | `api/queue.py` | Stops future devs trusting a retry path that doesn't exist. |
| 4 | **GZip compression** — `GZipMiddleware(minimum_size=1024)`. | `api/server.py` | Cuts egress + TTFB on the fat list/analytics payloads as per-tenant data grows. |
| 5 | **Worker self-heals** — restart policy `ON_FAILURE`→`ALWAYS`. The worker pings Redis on boot and exits if it's down; with a low `ON_FAILURE` retry cap a sustained outage left it **permanently dead**. | `railway.toml` | Worker now recovers automatically when Redis/DB returns. |
| 6 | **Reproducibility + hot-path indexes migration** — captures the `user_recommendation_*` columns that were applied to prod live (never migrated), plus partial indexes for `/today` and `/today/recommended`. Idempotent; ships `CONCURRENTLY` variants for large tables. | `db/migrations/2026_05_29_039_*.sql` | A from-scratch rebuild now matches prod; the two hottest read paths stop scanning all of a tenant's jobs. **Apply with your usual migration step.** |

**Verification:** all changed files compile; `test_admin_scheduler` + app-import smoke pass (10/10); full backend suite shows **no new failures** vs `main` (same 7 pre-existing, environment-dependent failures).

---

## 3. 📋 Findings not yet fixed (prioritized)

### P0 — must fix before a multi-tenant launch

- **Sync DB blocks the event loop.** `db/client.py` builds a synchronous `supabase-py` client; every `.execute()` is a blocking socket call on the loop. **Fix:** move to the async client (`create_async_client` / `await …execute()`) or wrap calls in `run_in_threadpool`. *(L / med-high — broad but mechanical.)*
- **RLS bypassed + unscoped endpoints.** Service-role key ⇒ RLS policies are inert; `server.py` `/jobs`, `/companies`, `/companies/targets`, `/applications` filter on score/phantom but **not `user_id`**. **Fix:** authenticate as the end-user (anon key + JWT) so RLS is enforced; route every list query through `Depends(get_current_user)` + `.eq("user_id")`; add a test that fails on any tenant-table query lacking a `user_id` predicate. *(L / high.)*
- **Global `UNIQUE` constraints break tenancy.** `companies.name`, `jobs.url`, `company_knowledge(company_name,section)`, `company_personas.company_name`, `rizwan_profile.section` are globally unique; the matching `on_conflict` upserts ignore `user_id`, so tenant B's upsert **clobbers** tenant A. **Fix:** make them composite with `user_id` and update every `on_conflict`. *(M / high — DDL on hot tables + coordinated client change.)*
- **Browser auth is a single shared secret.** The dashboard proxy attaches one `X-Secret-Key` to every upstream call; no per-user JWT propagation, so the backend can't tell tenants apart. **Fix:** propagate the Supabase JWT as `Authorization: Bearer`; reserve `X-Secret-Key` for service-to-service. *(L / high — pairs with the two above.)*
- **No per-tenant spend cap that halts.** `CostAlerter` only *alerts*; halting caps exist only per-build (G2/G3). A runaway tenant/loop can spend unbounded $ on shared keys. **Fix:** pre-call budget gate keyed on `user_id` in the LLM router that raises `BudgetExceeded`. *(M / med.)*

### P1 — serious degradation at scale

- **Worker throughput ceiling + no fairness.** `WORKER_CONCURRENCY=1`, single `jobhunt` queue, 5-min G2 builds. **Fix:** split queues by latency class (`interactive` / `batch` / `cron`), separate worker pools, autoscale on queue depth, per-tenant in-flight cap. *(M / med.)*
- **Cost/analytics endpoints load whole tables into Python.** `/costs/summary`, `/costs/by-*`, the analytics funnel `select(...)` 30–90 days of `agent_call_log` with **no `user_id`, no limit**, then aggregate in Python. **Fix:** push `SUM/GROUP BY` into Postgres RPCs/views; scope by `user_id`. *(M / med.)*
- **`agent_call_log` unbounded — no partitioning/retention; cost views full-scan.** Fastest-growing table; partitioning is written but explicitly *not applied*. **Fix:** apply pg_partman monthly range partition on `called_at` now, add retention (drop >90–180d), back cost cards with a daily rollup table; add `(user_id, called_at DESC)` index. *(M / med.)*
- **Per-process circuit breaker / provider-health / rate state.** Each replica trips independently → you keep hammering a 429ing provider with N× doomed calls. **Fix:** externalize breaker + a token-bucket limiter to Redis, keyed per-provider. *(M / med.)*
- **Heavy work runs in the web process.** ~20 `BackgroundTasks` sites (some self-described "~2.5 hours") + the Redis-down in-process daemon-thread fallback run LLM graphs inside the API. **Fix:** route everything through the queue; in multi-tenant, fail loud on Redis-down instead of in-process execution. *(M / med.)*
- **Cron batch jobs run in-process, serially.** The g6 / persona / scout crons execute full graphs on the scheduler's event loop (O(tenants×apps) sequential LLM calls; lost on redeploy). **Fix:** cron should only *enqueue* per-tenant jobs. *(M / med.)*
- **N+1s on the request path.** `/network/people` issues one `_current_employment` query per row (≤100/req); referral-path building does the same per node. **Fix:** batch-fetch with `in_(person_ids)` (pattern already used in `referral_graph.py`). *(S / low.)*
- **Per-row write loop on every `/today`.** `_bump_surface_counts` issues one `UPDATE` per surfaced job. **Fix:** single bulk RPC `UPDATE … WHERE id = ANY(:ids)`. *(S / low.)*
- **Rate limiting is per-IP + in-memory.** Keyed on remote IP (NAT'd tenants collide) with a per-replica memory store (limits = N× intended once scaled). **Fix:** key on `user.id`; back slowapi with the shared Redis (`storage_uri`). *(M / med.)*
- **No connection pooler.** Singleton service-role client; no PgBouncer / transaction pooler (port 6543). Concurrent API replicas + workers will exhaust Postgres `max_connections`. **Fix:** route through Supabase's transaction pooler; load-test the connection budget before raising concurrency. *(M / med.)*
- **Redis is a single point of failure.** One instance, no HA, default plugin likely not AOF-persistent → a restart drops every queued job. **Fix:** managed Redis with AOF + failover; verify `maxmemory-policy noeviction`; alert on queue depth + Redis health. *(M / med.)*
- **Scout queue amplification.** Each discovered job enqueues 2 more (`g5_score` + `legitimacy_check`) onto the single queue. **Fix:** route to a low-priority `batch` pool; batch-score K jobs per LLM call. *(M / med.)*
- **Unbounded `asyncio.gather` fan-outs.** Several agent paths gather one LLM/HTTP call per row with **no `Semaphore`** (others correctly use `Semaphore(3/8)`). **Fix:** bound every gather. *(S / low.)*
- **LangGraph hygiene.** No explicit `recursion_limit` on `ainvoke`; G2 checkpoint `thread_id` keyed only on `job_id` (cross-tenant checkpoint collision once `job_id` isn't globally unique). **Fix:** set `recursion_limit`; namespace `thread_id` with `user_id`. *(S / low.)*
- **LLM-heavy calls round-trip through the Vercel proxy with no `maxDuration`.** BossChat / deep-research / interview-prep can exceed Vercel's function timeout (504 while the backend keeps working) and add a mandatory second hop. **Fix:** move long ops to the durable queue + poll pattern (already used for resume builds); set `maxDuration`. *(M / med.)*
- **No observability anywhere.** No Sentry / OTel / metrics / RUM on backend or frontend; `/health` is static. You'd learn about scale problems from users. **Fix:** OTel traces + metrics + Sentry on API; `@vercel/analytics` + Web Vitals on the dashboard; export RQ queue depth as a gauge with alerts; add a `/ready` readiness probe. *(M / med.)*

### P2 — efficiency / hygiene

- Telemetry write (`agent_call_log` INSERT) runs **synchronously on every LLM call's hot path** → buffer/async it.
- Unbounded `select("*")` list endpoints (no pagination) + `count="exact"` on tenant tables → keyset pagination, narrow columns, planned counts.
- `CREATE INDEX` without `CONCURRENTLY` everywhere (the `BEGIN/COMMIT` migration style structurally prevents it) → use standalone concurrent index migrations for big tables. *(Migration 039 ships the `CONCURRENTLY` variants.)*
- Large in-txn backfills (`2026_05_26_037` full-table `UPDATE jobs`) → batch outside the schema-change txn.
- `rizwan_profile` is structurally single-user (`UNIQUE(section)`); ivfflat `lists` sized for ~10–100 rows; **`jobs.jd_embedding` has no vector index** (stored + selected but no `<=>` query found — decide: add HNSW if you'll search it, or drop the 6 KB/row column). `jobs.id` is `SERIAL int` (2.1B ceiling) on the largest table.
- Frontend: **SWR is installed but unused**; insights tabs re-fan-out 6–9 backend calls on every mount with no cache; tables render full result sets (no pagination/virtualization).
- Docker images carry Playwright/Chromium in **both** API and worker. (Note: the API genuinely uses Playwright via `api/g7.py`, so it can't simply be dropped — consider moving `g7` to the worker tier first.) Multi-stage builds would cut cold-start time.
- No resource requests/limits or autoscaling declared in `railway.toml` (IaC) — capacity posture is undocumented.
- `boss_audit_log` is intentionally un-tenanted + RLS-off with no retention — decide tenancy + add a retention drop.

---

## 4. Roadmap (sequenced)

**Phase 0 — ✅ done (this pass):** scheduler scale-guard, reaper recovery fix, GZip, worker self-heal, reproducibility + hot-path index migration.

**Phase 1 — Tenant safety (do before onboarding a 2nd paying tenant).** Composite-key tenancy (kill global `UNIQUE`s) → per-user JWT auth through the proxy → `user_id` scoping on all `server.py` endpoints → enable/enforce RLS as defense-in-depth → a CI test that fails on any unscoped tenant-table query.

**Phase 2 — Throughput & isolation (do before real concurrent load).** Async data layer (or threadpool offload) → queue split + worker autoscaling + per-tenant fairness → connection pooler (6543) → per-tenant spend caps → externalize circuit breaker + rate limiter to Redis.

**Phase 3 — Operability & cost (do alongside growth).** Observability stack (traces/metrics/Sentry/RUM + queue alerts) → `agent_call_log` partitioning + retention + cost rollups → Redis HA/persistence → push analytics aggregation into SQL → move long ops off the Vercel proxy onto the queue → frontend caching (SWR/RSC) + pagination.

---

## 5. Already done well (so the team doesn't re-litigate)

LLM calls have client timeouts + bounded exponential-backoff retries + an OpenRouter fallback rail with sound retriable-error classification; Anthropic prompt caching auto-applies on ≥4 KB system prompts and OpenAI `prompt_cache_key` on ≥4 KB prompts; per-build cost caps genuinely halt the G2/G3 loops; enqueue is idempotency-keyed; a SIGTERM sweep marks in-flight rows failed on deploy; DB clients are module-level singletons (no per-request churn); referral-graph / jobs_runs / comp_cache migrations are well-indexed (composite + partial); pages are RSC with `revalidate` caching; `/health` is cheap (no DB hit); `.dockerignore` keeps the dashboard out of the backend image.
