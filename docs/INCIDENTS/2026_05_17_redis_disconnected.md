# Incident — Railway worker queue offline (Redis disconnected)

## Summary

The Railway service `jobHunt` cannot reach Redis. `GET /admin/worker-status`
reports `redis_ping: false`, and the Railway logs show
`Error 111 connecting to localhost:6379. Connection refused` on every
`/admin/worker-status` request. The result: RQ-queued background jobs
(G2 resume builds, G6 follow-ups, G9 deep-research, G11, etc.) cannot be
dispatched. The embedded APScheduler still runs the 6 cron jobs in-process,
but anything that goes through `api.queue.enqueue_*` lands in a queue that
nothing consumes.

This is an infrastructure gap, not a code regression — it has likely been
true since the project was set up on Railway. The B1/B7 work made the
problem visible (`/admin/worker-status` is the first endpoint to expose
it), but the underlying cause is that **no Redis service is provisioned
on the Railway project, and no `REDIS_URL` is set on the `jobHunt`
service**.

## Timeline (UTC)

| Time              | Event |
|-------------------|-------|
| project setup     | Single Railway service `jobHunt` deployed. `railway.toml` declares `api` + `worker` services but the worker was never created. No Redis plugin attached. |
| 2026-05-16 (B1)   | `POST /admin/requeue-stuck` + `GET /admin/worker-status` shipped (PR #127). Diagnostic now surfaces `redis_ping: false`. |
| 2026-05-17 (audit)| QA pass confirms: `/admin/worker-status` returns `redis_ping=false`, no `REDIS_URL` env var set, no Redis service in the Railway project. |

## Root cause

`api/queue.py` connects to Redis via the standard `REDIS_URL` env var,
falling back to `redis://localhost:6379` when unset. On Railway, no
Redis plugin is attached to the `Job calm` project, and the `jobHunt`
service has no `REDIS_URL` in its variables. The fallback `localhost:6379`
fails because nothing in the Railway container listens on that port.

The scheduler tasks (APScheduler embedded in the API process via B26's
lifespan) run inside the same FastAPI process and do **not** go through
Redis, so the daily cron jobs continue to fire. But any code path that
calls `enqueue_g2_build`, `enqueue_g6_followup`, etc. silently fails
under the hood.

## Affected user-visible behaviour

- `POST /jobs/{id}/generate-resume` → enqueues a build, returns 200, but
  the worker never picks it up. The dashboard `/workspace/{id}` page
  polls forever for a `resume_build_id` that stays `queued`.
- `POST /follow-ups/{application_id}/send` (when wired) → same.
- Any `BackgroundTasks.add_task(enqueue_*)` path → same.

What *does* work (because it bypasses Redis):
- Synchronous resume builds (the legacy `_run_g2_inline` path)
- All `/admin/*` diagnostic endpoints
- All Supabase reads/writes (dashboards, LinkedIn drafts, persona data)
- LLM calls via `agents/llm_hardening` (also synchronous)
- The 6 APScheduler crons (job_scout, boss_agent, persona_synthesis,
  cost_alert, cost_digest, g6_followup_cadence)

## Fix

Two paths. Pick one based on whether you want background-job parallelism.

### Option A — Attach Redis to the existing Railway service (simple, ~5 min)

1. Railway dashboard → `Job calm` project → "+ New" → Database → Redis
2. Redis service should auto-inject `REDIS_URL` into the `jobHunt` service.
   If not, add `REDIS_URL=${{Redis.REDIS_URL}}` to `jobHunt` variables.
3. Redeploy `jobHunt`.
4. Verify: `curl -H "X-Secret-Key: $S" $URL/admin/worker-status`
   → `redis_ping: true`, `queue_depth: <N>`.

This is the cheapest fix. The same `jobHunt` process handles both API
requests and the RQ worker loop (Railway will need `start_worker_thread`
or similar to be invoked at process start — see `main.py` for the
`--api` / `--worker` flags. Currently `start command = "python main.py --api"`
which doesn't spawn the worker thread).

### Option B — Provision the dedicated worker service from railway.toml

`railway.toml` already declares a `worker` service:

```toml
[[services]]
name = "worker"
[services.variables]
START_MODE = "worker"
```

Railway's TOML-driven multi-service mode was deprecated in 2024; the
recommended path is now the Railway dashboard. Create a second service
in the `Job calm` project that:

1. Builds the same `RizwanZafaris/jobHunt` repo
2. Uses `Dockerfile.worker` (already in the repo)
3. Has `START_MODE=worker` set
4. Shares the same Redis from Option A

This gives the horizontal scaling that `railway.toml`'s comments
promise. Not required for correctness — Option A is sufficient.

## Prevention

- Add `REDIS_URL` to the documented required env vars in `README.md`
  setup instructions and `.env.example`.
- Add a startup check in `api/server.py`: if `REDIS_URL` is unset or
  unreachable, log a HIGH warning that "background queue is offline —
  /admin/requeue-stuck cannot recover stalled jobs". This is a
  development-affordance, not a hard fail (the system stays useful for
  read paths and synchronous flows).
- Wire the `pr-job-scan` workflow into CI (see [tools/pr_job_scan](../../tools/pr_job_scan/)) so this class of operational gap shows up in deployment audits.

## Related findings (same audit, no action needed in this PR)

- `feat/pr-job-scan` Vercel preview is in **CANCELED** state with no build
  logs. The branch was pushed but the preview never ran. Re-trigger via
  `git commit --amend --no-edit && git push -f` if you want a build.
- Vercel runtime logs show **zero errors** in the last 7 days. The single
  request in that window was the audit's own curl. The production
  dashboard at `dashboard-eight-theta-t11irr7qdu.vercel.app` is healthy.
- Supabase reads succeed on every probe. Cost telemetry is live and
  flowing: $0.18 today, $14.93 last 7d, $31.64 last 30d, 1000 calls last
  30d. **GAP-001 from the original gap report is closed** — the
  "Today (UTC) = $0.00" symptom no longer reproduces.
