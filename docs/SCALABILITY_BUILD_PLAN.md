# jobHunt — Multi-Tenant Build Plan (sequenced)

**Date:** 2026-05-29 · Derived from 4 parallel implementation-planning passes (DB schema · Phase 1 tenant safety · Phase 2 throughput · Phase 3 operability). Companion to [`SCALABILITY.md`](SCALABILITY.md) (the findings); this is the **ordered backlog** to execute them.

> **Safety backbone:** every workstream ships **behind a flag defaulting to current behavior** (`RIZWAN_SINGLE_USER_MODE=1` backend, `NEXT_PUBLIC_SINGLE_USER_MODE` dashboard, plus per-feature flags). The live single-user system stays byte-for-byte unchanged until a flag is flipped. The `.eq("user_id")` predicates are **no-ops on today's single-tenant data** (every row is already the seed user), so tenant-scoping PRs can merge to prod *before* multi-tenant is switched on.

---

## 1. The dependency chain (why order matters)

```
DB-1 composite keys ──┬──► P1 tenant safety ──► P2 throughput (async + caps + queue)
 (kills global UNIQUE)│         │
                      │         └──► (introduces user_id contextvar, consumed by P2)
                      │
P3 operability ───────┴──► (mostly independent; cost-rollup waits on DB-3; can run last/parallel)
```

Three hard rules the four plans agreed on:
1. **DB-1 (composite keys) is the foundation.** Until the global `UNIQUE`s on `companies.name` / `jobs.url` / `company_knowledge(company_name,section)` / `company_personas.company_name` / `rizwan_profile.section` become composite with `user_id`, tenant B's upsert **overwrites** tenant A — and no amount of read-scoping fixes that. It's the first Phase-1 item.
2. **Phase 1 before Phase 2.** Both edit the same files (`api/server.py`, `db/client.py`, `api/auth.py`). Phase 1 lands the *query shape* (auth + `.eq("user_id")`); Phase 2 then makes a mechanical transform on top (wrap in `await aexecute(...)`). Reverse order doubles the churn on the hottest code.
3. **`user_id` arrives via a contextvar introduced in Phase 1** (`agents/run_context.py`, set in the `get_current_user` dependency). Phase 2's budget gate + per-user rate limiter only *read* it — so the agent call path isn't re-plumbed twice.

**Non-negotiable de-collision rule (db/client.py):** Phase 2's async twins must **call** the sync helpers (`return await run_in_threadpool(upsert_job, …)`), never **copy** their bodies — so the DB workstream's `on_conflict` edits and Phase 1's `user_id` edits stay single-sourced and the async PR is append-only.

---

## 2. Two things to settle before building (decisions / unknowns)

| Item | Why it matters | Owner |
|---|---|---|
| **`db/APPLY.sh` CONCURRENTLY support** | Several index migrations need `CREATE INDEX CONCURRENTLY`, which **cannot** run inside a transaction. If APPLY.sh wraps each file in `BEGIN/COMMIT`, those files need a non-txn apply path. **First action before authoring DB-1/DB-3/DB-4.** | check at impl time |
| **Drop `jobs.jd_embedding`?** | Confirmed stored+selected but **never** searched (zero `<=>` queries). Dropping reclaims ~6 KB/row on the largest table — but `DROP COLUMN` is irreversible and the repo has a no-drops norm. **Needs your y/n.** | you |
| **Dashboard auth is greenfield** | The dashboard has **no Supabase auth today** (no `@supabase/*`, no session, no login). Phase 1's per-user JWT requires standing that up first — a mini-project (PR P1-6), not a config tweak. | scope decision |
| **Managed Redis for HA** | Current Redis is a single plugin, likely no AOF → a restart drops queued jobs. Phase 3 cutover needs a managed Redis (Railway managed / Upstash) choice. | you |
| **`agent_call_log` partitioning (DB-6)** | Gated on volume (>100k rows / >1 GB). **Premature at single-user scale — deferred** until the trigger fires. | deferred |

---

## 3. Sequenced backlog (the PRs)

Effort: S ≈ ≤1d · M ≈ 1–3d · L ≈ multi-PR/week. Flag = ships dark behind this until flipped.

### Foundation — DB workstream
| PR | Scope | Dep | Effort/Risk | Flag |
|---|---|---|---|---|
| **DB-1** | Composite-key tenancy: build 5 composite UNIQUE indexes `CONCURRENTLY` → deploy `on_conflict` edits (8 sites across `db/client.py` + `api/server.py:1519` + 2 in `persona_*`) → drop global UNIQUEs. **Also fixes 4 upsert writers that don't set `user_id` today.** | — | M / **High** (ordered deploy; verify constraint names on live first) | — (corectness) |
| **DB-2** | `job_card_dismissals.job_id` bigint→integer + FK to `jobs(id)`; redefine `idx_dismissals_active` to be sargable (drop the `now()` predicate) + push the active filter into the query (`api/actions.py`). | — | S / Low | — |
| **DB-3** | `agent_call_log (user_id, called_at DESC)` index (CONCURRENTLY) + `agent_cost_daily` rollup table + `refresh_agent_cost_daily` RPC + nightly cron + retention (120d). **Feeds P2-6 budget gate + P3-8 cost cards.** | — | M / Low–Med | — |
| **DB-4** | pgvector ivfflat→HNSW for company_knowledge / proof_points / story_bank / rizwan_profile (no RPC change — same `<=>`). | — | S / Low | — |
| **DB-5** | Drop `jobs.jd_embedding` + remove from `_JOBS_COLUMNS`. *Gated on your sign-off (irreversible).* | — | XS / Med | — |
| **DB-6** | `agent_call_log` pg_partman monthly partition + native retention. **Deferred until volume trigger.** | DB-3 | M / Med | deferred |

### Phase 1 — Tenant safety
| PR | Scope | Dep | Effort/Risk | Flag |
|---|---|---|---|---|
| **P1-1** | Backend auth deps: `verify_service_secret` (worker/cron), fail-closed `verify_secret` (today it authorizes the default placeholder secret), service-secret branch in `get_current_user`; introduce `agents/run_context.py` contextvar. | — | S / Low | single-user |
| **P1-2** | Per-request RLS client `get_user_supabase(jwt)` (anon key + user JWT so `auth.uid()` resolves) + FastAPI dependency. Service-role kept for worker/cron. | P1-1 | S–M / Med | single-user |
| **P1-3** | Endpoint scoping **batch A** — jobs/companies/applications/resume\* (~40 endpoints): `Depends(get_current_user)` + `.eq("user_id")` + scope pre-insert lookups. | DB-1, P1-2 | M / Med (wide, mechanical) | no-op single-user |
| **P1-4** | Endpoint scoping **batch B** — personas/profile/costs/misc (~37) + admin→`require_admin`, cron→`verify_service_secret`. | DB-1, P1-2 | M / Med | no-op single-user |
| **P1-5** | CI guardrail: static-scan test fails the build on any tenant-table query lacking a `user_id` predicate. | P1-3/4 | S / Low | — |
| **P1-6** | **Dashboard Supabase Auth (greenfield):** `@supabase/ssr`, client/server/middleware, login route, session refresh `middleware.ts`. | — (parallel) | M–L / Med | — |
| **P1-7** | Proxy + RSC fetchers forward the JWT as `Authorization: Bearer`; stop sending `X-Secret-Key` from the browser. Flag-gated. | P1-6 | S–M / Med | single-user |
| **P1-8** | Two-tenant isolation integration test (seed A+B, assert zero cross-tenant leak via API + RLS). | P1-2/3/4, P1-7 | M / Low | — |
| **P1-9** | Remove RLS escape hatches (`OR auth.uid() IS NULL`) on comp_cache + proof_points (migration). | P1-2/3/4 deployed | S / Med | — |

### Phase 2 — Throughput & isolation (after Phase 1; P2-1/2-4 can start early — no Phase-1 overlap)
| PR | Scope | Dep | Effort/Risk | Flag |
|---|---|---|---|---|
| **P2-0** | Phase-2 settings/flags (pooler, budget, breaker_backend, rate-limit storage) — all default-neutral. | — | S / Low | — |
| **P2-1** | Queue split: `interactive` / `batch` / `cron` queues + routing in `_enqueue_or_dedup` + `railway.toml` worker pools. | P2-0 | M / Med | default drains all |
| **P2-2** | Scheduler-owner gate `RQ_RUN_SCHEDULER` (queue-side analogue of Phase-0's `SCHEDULER_ENABLED`) — RQ scheduler runs on exactly one worker. | P2-1 | S / Low | — |
| **P2-3** | Per-tenant fairness: Redis in-flight cap per `(user_id, queue)`, worker re-defers over cap. | P2-1 | M / Med | — |
| **P2-4** | Redis-backed circuit breaker (`agents/llm_hardening.py` backend switch) — fleet-wide provider backpressure. Fails open on Redis error. | P2-0 | M / Med | breaker_backend |
| **P2-5** | Async seam: additive `a`-twins + `aexecute` + bounded DB semaphore in `db/client.py`. **No callers changed.** | P2-0 | S / Low | — |
| **P2-6** | Per-tenant spend cap: `agents/budget_gate.py` pre-call gate in `BaseAgent.ask()`, raises `BudgetExceeded` (non-retryable). Reads Phase-1 contextvar; cached SUM via DB-3 rollup. | **Phase 1 contextvar**, DB-3 | M / Med | budget_gate_enabled |
| **P2-7** | Per-user Redis rate limiter (slowapi `storage_uri`=Redis + `key_func`=user.id). | **Phase 1 auth**, P2-0 | S / Med | rate_limit storage |
| **P2-8…N** | Handler async conversion, router-by-router hottest-first (`.execute()`→`await aexecute`), each gated on that router's Phase-1 scoping + a CI guard against new blocking calls. | per-router P1, P2-5 | **L** (many small PRs) | per-router |
| **P2-Z** | Connection-budget load test + transaction-pooler (6543) routing behind a flag. | P2-5, P2-8 set | M / Med | db_use_pooler |

### Phase 3 — Operability & cost (mostly independent; P3-1/2/4/5 shippable immediately)
| PR | Scope | Dep | Effort/Risk | Flag |
|---|---|---|---|---|
| **P3-1** | Proxy `maxDuration` + upstream `AbortSignal.timeout` — immediate 504 safety. | — | S / Low | — |
| **P3-2** | Backend observability: `api/observability.py` (Sentry + OTel traces/metrics + JSON logs), wired at top of `server.py`. No-op unless env set. | — | M / Low | env-gated |
| **P3-3** | `/ready` readiness probe (shallow Redis+DB ping), distinct from cheap `/health`. | P3-2 | S / Low | — |
| **P3-4** | Frontend Sentry + `@vercel/analytics` + Speed Insights (RUM). PII-scrubbed. | — | S–M / Low | env-gated |
| **P3-5** | Async/bounded telemetry buffer — get the per-call `agent_call_log` INSERT off the LLM hot path. | — | S–M / Low | — |
| **P3-6** | RQ queue-depth gauge (exports existing `queue_health()`) + queue-staleness alert cron (reuses CostAlerter rail). | P3-2 | M / Low | — |
| **P3-7** | Managed Redis HA/AOF/`noeviction` cutover (IaC) + queue durability. | P3-6 | M (ops) / Med | — |
| **P3-8** | Push cost/analytics aggregation into SQL: user-scoped cost RPCs over the DB-3 rollup; rejection-clustering view; user-scope all `/costs/*`. | DB-3, Phase-1 `user_id` | M–L / Med | — |
| **P3-9** | Long ops → durable queue+poll: new kinds (boss_chat/interview_prep/deep_research) + `useJobRun` hook + 4 components. | P3-1 | L / Med | ?sync=1 fallback |
| **P3-10** | Frontend caching (adopt installed-but-unused SWR / RSC) + table pagination/virtualization. | — | M–L / Low | — |

---

## 4. Critical path & recommended start

**Critical path to multi-tenant:** `DB-1 → P1-1 → P1-2 → P1-3/P1-4 → P1-9`, with `P1-6/P1-7` (dashboard auth) gating the actual `SINGLE_USER=0` flip. Then `P2-5 → P2-8…N` (async conversion) is the long pole. Phase 3 runs alongside.

**Immediately shippable now, zero cross-phase dependency, high value** (good "warm-up" PRs while the foundation lands):
- **P3-1** proxy `maxDuration` (504 safety) · **DB-2** dismissals fix · **DB-3** cost index+rollup · **DB-4** HNSW · **P2-1**+**P2-4** queue split + Redis breaker · **P3-2/3-4/3-5** observability.

**Recommended first build:** **DB-1 (composite keys)** — it's the foundation everything multi-tenant waits on, and the highest-risk DDL, so it benefits most from being done carefully and first. In parallel, the zero-dependency quick wins above can land as separate PRs.

---

## 5. Rough effort

| Workstream | Elapsed (low-risk, many small PRs) |
|---|---|
| DB (DB-1..5, DB-7) | ~1 week (DB-6 deferred) |
| Phase 1 (incl. greenfield dashboard auth) | ~1.5–2 weeks backend/proxy + ~3–5 days dashboard auth (parallel) |
| Phase 2 (async conversion is the long pole) | ~4–6 weeks elapsed, dominated by incremental router conversion |
| Phase 3 | ~3–4 weeks (P3-1/2/4/5 immediate; P3-8 gated on DB-3) |

Each PR is small, flag-guarded, and independently revertible — the timeline is deliberately slow-and-safe, not a big-bang.
