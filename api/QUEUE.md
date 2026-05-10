# jobHunt durable work queue

> Status: scaffold landed (Phase 3 PR-1). The migration of existing
> `BackgroundTasks` call sites to `enqueue_*` is a **separate PR**
> (PR-2). See the migration cookbook below for the exact diff.

## Why this exists

Pre-Phase-3 every long-running graph (G1 persona deep-research,
G2 resume build, G3 interview prep, plus 11 misc background jobs in
`server.py`) ran via FastAPI's `BackgroundTasks`. Two problems:

1. **No durability.** Railway redeploys every push. Any in-flight
   build dies on the floor. A 4-minute G2 resume build, ~$3 of LLM
   spend, vanishes. No retry. No reaper. No surface to the user.
2. **No idempotency.** A user double-clicking "Generate resume"
   enqueues two builds. Two API instances behind a load balancer
   duplicates work.

The audit flagged this as a critical SaaS-blocker.

## The model

```
   ┌──────────────┐  enqueue_g2_build()    ┌───────────┐
   │  api/server  │ ─────────────────────▶ │ jobs_runs │  (Postgres
   │  (FastAPI)   │                        │   row     │   audit log)
   └──────┬───────┘                        └─────┬─────┘
          │ q.enqueue('api.worker...')           │
          ▼                                      ▼
   ┌──────────────┐                       ┌──────────────┐
   │ Redis (RQ)   │ ◀──── worker pops ─── │  api/worker  │
   │  'jobhunt'   │                       │  (rq Worker) │
   └──────────────┘                       └──────┬───────┘
                                                 │
                                                 ▼
                                          ┌──────────────┐
                                          │  G2/G1/G3    │
                                          │  graph code  │
                                          └──────────────┘
```

Three components, each owns its concerns:

- **`api/queue.py`** — enqueue side. Computes the idempotency hash,
  writes a `jobs_runs` row, pushes an RQ job. Caller gets the run id
  back synchronously.
- **`api/worker.py`** — consumer. Dispatches to the existing graph
  entrypoints (`run_g2_graph`, `deep_research_persona`, `run_g3_graph`).
  Owns status transitions on `jobs_runs` and the retry policy.
- **`api/jobs_runs.py`** — DB model + helpers. The audit trail.
  Surfaces to the user via dashboard polling.

The orphan reaper (`api/orphan_reaper.py`) sweeps stale `running` rows
every 5 min and either re-queues them or marks them terminal.

## Idempotency rules

```python
idempotency_key = sha256(f"{user_id}|{kind}|{json.dumps(payload, sort_keys=True)}")
```

The key is enforced UNIQUE on `jobs_runs.idempotency_key`. Two calls
to `enqueue_g2_build(user_id=u, job_id=42)` within the same
queued/running window collapse to ONE run. The second call gets back
the same run id without enqueueing.

If you actually want two builds for the same job (e.g. retry after a
known-bad config), pass `force=True` — it goes into the payload, the
hash changes, the dedup misses, the second build runs.

Terminal rows (`succeeded`/`failed`/`cancelled`) also dedup by key. To
re-run after a terminal failure: pass `force=True`.

## Retry policy

Lives in `api/worker.py`. Constants:

```python
RETRY_INTERVALS = [60, 240, 960]  # 1m, 4m, 16m
MAX_ATTEMPTS = 3
```

On a worker exception:

1. We log the traceback to `jobs_runs.last_error`.
2. We classify retryable vs not (`_is_retryable`):
   - **Retryable**: `TimeoutError`, `ConnectionError`, anything that
     looks like a rate-limit / transient API error.
   - **Not retryable**: `TypeError`, `AttributeError`, `KeyError`,
     `ValueError` — programmer errors don't get better with retry.
3. If retryable AND `attempts < 3`: status flips back to `queued`,
   we re-raise so RQ's Retry kicks in with the next interval.
4. Else: status flips to terminal `failed`, traceback is preserved.

## Reaper behavior

`api/orphan_reaper.py` runs every 5 min. Logic:

1. `find_orphans(stale_minutes=15)` — selects rows where
   `status='running' AND started_at < now() - 15 min`.
   These are almost certainly killed by a Railway redeploy
   (worst-case legitimate run is ~5 min).
2. For each orphan:
   - If `attempts < 3`: requeue (status → queued, push fresh RQ job)
   - Else: terminal failure with `last_error='orphaned by redeploy'`

Runs in two modes:
- **Embedded** (default): APScheduler thread inside the worker process.
  Started by `start_scheduler()`.
- **Standalone cron**: `python -m api.orphan_reaper` for Railway's
  scheduled-tasks feature. Single-shot, exits after one sweep.

---

## Migration cookbook — converting one BackgroundTasks call

The point of this scaffold is that swapping `BackgroundTasks` for
the durable queue should be **mechanical**. Here's the worked example
for `POST /jobs/{job_id}/generate-resume` — the highest-value endpoint
(this is where users spend $3+ of LLM cost per click).

### Before

```python
# api/server.py around line 1493
@app.post("/jobs/{job_id}/generate-resume")
async def generate_resume_for_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    max_cost_usd: Optional[float] = None,
    force: bool = False,
    _auth=Depends(verify_secret),
):
    # ... persona-gate, score-gate, validation (UNCHANGED) ...

    async def _run():
        from pipeline import JobHuntPipeline
        from datetime import datetime, timezone
        pipeline = JobHuntPipeline()
        try:
            await pipeline._process_single_job(job)
            db.table("jobs").update({
                "resume_generated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", job_id).execute()
        except Exception as e:
            logger.error(f"Resume generation failed for job {job_id}: {e}")

    background_tasks.add_task(_run)
    return {
        "status": "started",
        "message": f"Generating tailored resume for {job.get('title')} @ {job.get('company')}. Refresh in ~60s.",
        "job_id": job_id,
        "score": score,
        "archetype": job.get("archetype"),
        "max_cost_usd": max_cost_usd,
        "force": force,
        "persona_gate": {...},
    }
```

### After

```python
# api/server.py around line 1493
@app.post("/jobs/{job_id}/generate-resume")
async def generate_resume_for_job(
    job_id: int,
    # ↓ BackgroundTasks no longer needed
    max_cost_usd: Optional[float] = None,
    force: bool = False,
    _auth=Depends(verify_secret),
):
    # ... persona-gate, score-gate, validation (UNCHANGED) ...

    # ↓ Replace _run() + background_tasks.add_task with one enqueue call
    from api.queue import enqueue_g2_build
    user_id = _resolve_user_id_from_request(_auth)  # or settings.default_user_id
    run_id = enqueue_g2_build(
        user_id=user_id,
        job_id=job_id,
        force=force,
        max_cost_usd=max_cost_usd,
    )

    return {
        "status": "queued",                 # was 'started'
        "run_id": run_id,                   # NEW — for status polling
        "message": f"Resume build queued for {job.get('title')} @ {job.get('company')}. Poll /jobs-runs/{run_id} to track.",
        "job_id": job_id,
        "score": score,
        "archetype": job.get("archetype"),
        "max_cost_usd": max_cost_usd,
        "force": force,
        "persona_gate": {...},
    }
```

### Diff (unified)

```diff
@@ api/server.py @@
 @app.post("/jobs/{job_id}/generate-resume")
 async def generate_resume_for_job(
     job_id: int,
-    background_tasks: BackgroundTasks,
     max_cost_usd: Optional[float] = None,
     force: bool = False,
     _auth=Depends(verify_secret),
 ):
     # ... persona-gate, score-gate, validation UNCHANGED ...

-    async def _run():
-        from pipeline import JobHuntPipeline
-        from datetime import datetime, timezone
-        pipeline = JobHuntPipeline()
-        try:
-            await pipeline._process_single_job(job)
-            db.table("jobs").update({
-                "resume_generated_at": datetime.now(timezone.utc).isoformat(),
-            }).eq("id", job_id).execute()
-        except Exception as e:
-            logger.error(f"Resume generation failed for job {job_id}: {e}")
-
-    background_tasks.add_task(_run)
+    from api.queue import enqueue_g2_build
+    user_id = _resolve_user_id_from_request(_auth)
+    run_id = enqueue_g2_build(
+        user_id=user_id,
+        job_id=job_id,
+        force=force,
+        max_cost_usd=max_cost_usd,
+    )
     return {
-        "status": "started",
+        "status": "queued",
+        "run_id": run_id,
         "message": (
-            f"Generating tailored resume for {job.get('title')} @ {job.get('company')}. "
-            f"Refresh in ~60s."
+            f"Resume build queued for {job.get('title')} @ {job.get('company')}. "
+            f"Poll /jobs-runs/{run_id} to track."
         ),
         "job_id": job_id,
```

### Side effect: `resume_generated_at`

The old `_run()` updated `jobs.resume_generated_at` on success. Where
does that go now? **The worker handles it** — but it lives inside
`pipeline._process_single_job` already (or, more precisely, inside
`g2_io.finalize_resume_build` which the G2 graph calls on its way to
END). So the worker doesn't need to do it. If we want a separate
"resume_generated_at" timestamp, add it as a side-effect inside
`worker_run_g2`'s success branch:

```python
# api/worker.py inside worker_run_g2 after mark_succeeded(...)
db.table("jobs").update({
    "resume_generated_at": _now_iso(),
}).eq("id", int(job_id)).execute()
```

(Decision deferred — the existing `resume_builds.finalized_at` already
gives the dashboard what it needs.)

### Checklist for PR-2 (the call-site swap)

The grep below finds every `BackgroundTasks` call site in `server.py`.
Each one should be evaluated:

- **G1/G2/G3 graphs** → swap to `enqueue_g1_discovery` / `enqueue_g2_build` / `enqueue_g3_interview_prep`.
- **Misc short jobs** (cost-alert check, persona-synth refresh,
  reclassify-jobs, etc.) → these are short (<60s) and rarely-hit;
  not worth migrating right now. Leave as `BackgroundTasks` and revisit
  after observing real production traffic.

| Line | Endpoint | Graph | Action in PR-2 |
| --- | --- | --- | --- |
| 233  | `POST /pipeline/run` | full pipeline (multi-graph) | DEFER — orchestrates many graphs; needs its own design |
| 347  | `POST /companies/build` | (CompanyAgent.build_or_refresh) | KEEP BackgroundTasks (short, idempotent) |
| 410  | `POST /boss/audit` | BossAgent | KEEP BackgroundTasks |
| 671  | `POST /applications/review` | ApplicationTrackerAgent | KEEP BackgroundTasks (sync result already returned) |
| 1164 | `POST /profile/recommendations/regenerate` | ProfileAnalyzer | KEEP BackgroundTasks |
| 1294 | `POST /companies/research` | CompanyAgent (batch) | DEFER — multi-company batch fan-out |
| 1340 | `POST /pipeline/run-targets` | full pipeline | DEFER (see /pipeline/run) |
| 1362 | `POST /jobs/reclassify` | JobScoutAgent | KEEP BackgroundTasks (admin-rare) |
| **1417** | **`POST /jobs/{job_id}/generate-resume`** | **G2** | **MIGRATE → enqueue_g2_build** |
| **1525** | **`POST /jobs/{job_id}/prep-interview`** | **G3** | **MIGRATE → enqueue_g3_interview_prep** |
| 1952 | `POST /personas/synthesize` | PersonaSynthesizer | KEEP (cheap; 1-3 min) |
| **2058** | **`POST /personas/deep-research`** | **G1 (deep_research_persona)** | **MIGRATE → enqueue_g1_discovery** |
| 2130 | `POST /personas/refresh-news` | refresh_news_only | KEEP (per-company news refresh; ~10s) |
| 2205 | `POST /personas/deep-research-batch` | (G1 fan-out across all targets) | MIGRATE — per-company enqueue inside the batch |
| 2626 | `POST /alerts/check` | CostAlerter | KEEP |
| 2643 | `POST /alerts/weekly-digest` | CostAlerter | KEEP |

The three **MIGRATE** rows are the durability blockers. The DEFER /
KEEP rows are explicit decisions: short jobs that don't justify the
queue overhead OR multi-graph orchestrations that need their own
design pass.

---

## Running locally

Three processes:

```bash
# 1. Redis (or use a Docker container if you don't have it locally)
brew install redis
redis-server &

# 2. The API
uvicorn api.server:app --reload --port 8000

# 3. The worker
python -m api.worker
```

You should see in the worker log:

```
[INFO] api.worker: jobHunt worker starting (queue=jobhunt, redis=Redis<...>)
[INFO] rq.worker: Worker rq:worker:... started
```

Smoke test the queue:

```python
from api.queue import enqueue_g2_build, queue_health
print(queue_health())
run_id = enqueue_g2_build(user_id="00000000-0000-0000-0000-000000000001", job_id=1)
print(f"queued: {run_id}")
```

Watch the worker log for the pickup.

## Operating in production

### Inspect the queue

```bash
# Counts by registry
rq info -u $REDIS_URL

# Show queued jobs
rq info -u $REDIS_URL --only-jobs --queue jobhunt

# Show failed registry
rq info -u $REDIS_URL --only-failed --queue jobhunt
```

Or programmatically in Python:

```python
from api.queue import queue_health
print(queue_health())
# {'name': 'jobhunt', 'queued': 3, 'started': 1, 'failed': 0, ...}
```

### Drain the queue

```bash
# Stop accepting new work — set a maintenance flag in your config and
# refuse enqueue_* calls in api/server.py during the window. There's no
# RQ-side "pause" primitive.

# Wait for in-flight to finish, or kill them:
rq empty jobhunt -u $REDIS_URL
```

### Retry failed jobs

```bash
# Move all failed jobs back to the queue (after you've fixed the bug):
rq requeue --all -u $REDIS_URL

# Or pick one:
rq requeue -u $REDIS_URL <job_id>
```

For terminal `failed` rows in `jobs_runs` that you want to retry:
flip the row's status to 'queued' and re-enqueue with a fresh
`force=True` payload (the original idempotency key blocks a naive
retry).

```sql
-- One-off: reset and let the user retry
UPDATE jobs_runs SET status = 'cancelled' WHERE id = '<run_id>';
-- Then in the app, the user clicks "Generate resume" again with
-- force=true; the new payload's hash differs and a fresh row lands.
```

### Manual reaper run

```bash
# One-shot CLI: scan for orphans, requeue or fail them, exit.
python -m api.orphan_reaper

# With a custom stale threshold:
python -m api.orphan_reaper --stale-minutes 30
```

Output is a single JSON line — easy to pipe into observability.

### Scaling

Default Railway config: 1 worker replica, `WORKER_CONCURRENCY=1`.

Each worker handles one job at a time (RQ default). For more parallelism:

- **Vertical**: bump `WORKER_CONCURRENCY` in railway.toml. Spawns N
  worker subprocesses inside the container.
- **Horizontal**: scale the `worker` service to N replicas in
  Railway. Each replica is independent. Best for cost-of-failure
  isolation.

For G2 builds (5 min, $3 each), 2-4 replicas comfortably handles
20-40 builds/hour. Pay attention to Anthropic rate limits before
scaling worker count further.

### Monitoring

The worker logs go to Railway's stdout. Useful greps:

```
[INFO] api.queue: enqueued kind=g2_resume run_id=...
[INFO] api.worker: ...
[ERROR] worker_run_g2 failed run_id=... attempt=2/3 retry=true
[WARNING] orphan_reaper: ...
```

Add a `/admin/queue-health` endpoint in PR-2 that returns
`api.queue.queue_health()` so the dashboard surfaces queue depth +
failed count without shelling into Railway.
