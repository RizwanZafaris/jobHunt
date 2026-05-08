# Performance Operations — `agent_call_log`

**Status**: Phase 1.9 (composite indexes + RPC rollups + health view + cleanup
function) applied. Partitioning deferred until row-count thresholds are crossed.
**Last updated**: 2026-05-09

This document is the runbook for keeping the cost-telemetry pipeline
responsive as the system scales. Read this when:

- The `/costs` dashboard starts taking >2s to load
- `agent_call_log` row count crosses 10k / 100k thresholds
- You're considering a retention policy change

---

## 1. Topology recap

```
   agents/llm_router.py
     │
     │  on every successful LLM call:
     │  INSERT INTO agent_call_log (...)
     ▼
   ┌──────────────────────────────────────────────┐
   │  agent_call_log (Postgres)                   │
   │  Indexes (Phase 0 + 1.9):                    │
   │   ─ called_at DESC                           │
   │   ─ provider                                 │
   │   ─ agent_name                               │
   │   ─ resume_build_id                          │
   │   ─ (called_at DESC, provider)        ★ 1.9  │
   │   ─ (called_at DESC, agent_name)      ★ 1.9  │
   │   ─ (called_at DESC, resume_build_id) ★ 1.9  │
   │   ─ (called_at DESC) WHERE error IS NOT NULL ★1.9│
   └──────────────────────────────────────────────┘
     │
     ├──→ v_daily_llm_cost              (Phase 0)
     ├──→ v_company_conversion_funnel   (Phase 0)
     ├──→ v_agent_call_health           (Phase 1.9)
     ├──→ v_agent_call_log_stats        (Phase 1.9)
     ├──→ cost_by_provider_window(d)    (Phase 1.9 RPC)
     ├──→ cost_by_agent_window(d)       (Phase 1.9 RPC)
     └──→ cleanup_agent_call_log(d)     (Phase 1.9 RPC)
            │
            ▼
        api/server.py /costs/* endpoints
            │
            ▼
        dashboard /costs page
```

---

## 2. Health monitoring

`v_agent_call_health` aggregates last 7 days per provider:

| Column | Meaning |
|---|---|
| `provider` | anthropic / google / openai / deepseek / moonshot |
| `calls_7d` | total calls in window |
| `errors_7d` | calls where `error IS NOT NULL` |
| `error_rate_pct` | `100 * errors_7d / calls_7d` |
| `p50_latency_ms`, `p95_latency_ms`, `p99_latency_ms` | latency percentiles |
| `total_cost_usd_7d` | rolled-up spend |
| `last_call_at` | most recent successful or failed call |

Surfaced in the dashboard as `ProviderHealthBadges` on `/costs`. Severity
thresholds (see component source):

| Metric | 🟢 good | 🟡 warn | 🔴 bad |
|---|---|---|---|
| error_rate_pct | ≤ 1% | ≤ 5% | > 5% |
| p95_latency_ms | ≤ 8s | ≤ 20s | > 20s |

These are conservative for fintech-grade reliability. Adjust in
`dashboard/src/components/ProviderHealthBadges.tsx::severityClass()`
if real-world latency norms differ.

### Querying directly

```sql
SELECT * FROM v_agent_call_health;
```

### Alerting (Phase 1.10)

`agents/cost_alerter.py` runs two cron-driven checks:

1. **Daily threshold check** (22:00 GST after the boss audit) — reads
   today's spend from `agent_call_log`, compares against
   `settings.daily_cost_alert_usd` (default $20), fires if exceeded.
   Idempotent — won't double-fire same day (boss_audit_log dedup).
2. **Weekly digest** (Sundays 09:00 GST) — aggregates last 7 days:
   per-provider cost + error rate, top 5 most expensive resume_builds,
   cost_capped count, conversion funnel.

Dispatch order (first match wins):
1. **Slack webhook** (preferred) — set `SLACK_WEBHOOK_URL` in env. Get
   one at https://api.slack.com/messaging/webhooks.
2. **SendGrid email** (fallback) — uses existing `SENDGRID_API_KEY` +
   `ALERT_EMAIL_TO` (or `DIGEST_EMAIL_TO` if blank).
3. **Stdout** (last resort) — logged to Railway stdout. Useful when
   you haven't wired anything yet.

**Manual triggers:**
```bash
# CLI
python main.py --alert-check
python main.py --weekly-digest

# API
curl -X POST "$API/alerts/check"          -H "X-Secret-Key: $SECRET"
curl -X POST "$API/alerts/weekly-digest"  -H "X-Secret-Key: $SECRET"

# Inspect last 10 alerter audit entries
curl "$API/alerts/last" -H "X-Secret-Key: $SECRET"
```

**Tuning the threshold:**
- Realistic floor: `$5/day` if you're running ≤ 2 G2 builds/day
- Defensive upper bound: `$50/day` for active job hunting (10+ builds)
- Set 2× your typical daily — alerts should fire on anomalies, not
  routine traffic. Adjust after the first week of real data.

**Adding `> 5% error rate` or `> 20s p95` alerting** as a layer on top
is a future enhancement — currently surfaced visually on `/costs` only.

---

## 3. Scale-up thresholds & actions

### ≤ 10k rows (current state)

**Action**: nothing. The Phase 0 + 1.9 indexes carry every dashboard
query in <50ms. `cost_by_provider_window(7)` and `cost_by_agent_window(7)`
are SQL-only via RPC — no Python aggregation overhead.

### 10k – 100k rows

**Triggers** (any one):
- `SELECT total_rows FROM v_agent_call_log_stats > 10000`
- `SELECT rows_last_24h FROM v_agent_call_log_stats > 500`
- Daily `/costs` page load > 2s

**Actions**:
1. Run cleanup cron weekly to keep table bounded:
   ```bash
   curl -X POST "$API/costs/cleanup" \
     -H "X-Secret-Key: $SECRET" \
     -H "Content-Type: application/json" \
     -d '{"days_to_keep": 365}'
   ```
   The DB function refuses `days_to_keep < 7` as a safety guard.
2. If queries still feel slow, run `VACUUM ANALYZE agent_call_log;`
   manually to refresh planner stats.

### > 100k rows

**Triggers**:
- `SELECT total_rows FROM v_agent_call_log_stats > 100000`
- `SELECT rows_last_24h FROM v_agent_call_log_stats > 5000`
- `SELECT pg_total_relation_size('public.agent_call_log') > 1 GB`

**Action**: convert to **monthly range partitioning** with `pg_partman`.

The full partition setup is at the bottom of
`db/agent_call_log_perf.sql` (commented). Quick checklist:

1. Confirm low-traffic window (e.g. 2 AM GST Sunday).
2. Take a fresh backup or snapshot.
3. Run the BEGIN..COMMIT block from `db/agent_call_log_perf.sql` §6.
4. Verify row counts match between old + new tables before dropping
   the legacy table.
5. Schedule pg_partman maintenance via pg_cron (also in §6).
6. After 1 month, verify monthly partitions are auto-created
   (`\dt+ public.agent_call_log_*` in psql).

**Why not partition now?**
At 0 rows, partitioning adds operational complexity (extra tables to
manage, partition pruning to verify, retention via `partman.run_maintenance`)
without performance benefit. Indexes alone serve us up to ~100k rows.

---

## 4. Manual cleanup commands

### Inspect before deleting

```sql
SELECT
  COUNT(*) FILTER (WHERE called_at < NOW() - INTERVAL '365 days') AS would_delete_365d,
  COUNT(*) FILTER (WHERE called_at < NOW() - INTERVAL '180 days') AS would_delete_180d,
  COUNT(*) FILTER (WHERE called_at < NOW() - INTERVAL '90 days')  AS would_delete_90d
FROM agent_call_log;
```

### Run cleanup

```sql
-- Default (365 days):
SELECT cleanup_agent_call_log();

-- More aggressive (180 days):
SELECT cleanup_agent_call_log(180);

-- This will throw — minimum is 7:
SELECT cleanup_agent_call_log(3);
-- ERROR: cleanup_agent_call_log: refusing days_to_keep < 7
```

Or via API:

```bash
curl -X POST "$API/costs/cleanup" \
  -H "X-Secret-Key: $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"days_to_keep": 180}'
```

### Recommended cadence

| Volume regime | Cleanup cadence | Retention |
|---|---|---|
| Light (≤ 1k/day) | Quarterly manual | 365d |
| Moderate (1k–10k/day) | Monthly cron | 365d |
| Heavy (≥ 10k/day) | Weekly cron after partitioning | 90–180d |

The default of 365 days is generous — adjust based on whether you need
historical cost analytics that far back.

---

## 5. Backend behaviour notes

### `/costs/by-provider` and `/costs/by-agent` (Phase 1.9 RPC)

These now call `cost_by_provider_window(days_back)` and
`cost_by_agent_window(days_back)` instead of fetching every row and
aggregating in Python. Two consequences:

- **Faster at scale**: aggregation runs on the DB with composite indexes,
  not over the network. The wire payload shrinks from O(rows) to O(N)
  where N is `provider count` or `agent count` (~5 and ~15 respectively).
- **Graceful fallback**: if the RPC call fails (e.g. function not yet
  applied on a fresh dev DB), the Python aggregation kicks in
  automatically. You'll see a `WARNING` in the logs but the endpoint
  keeps working.

### `/costs/health` and `/costs/log-stats` (Phase 1.9, new)

- `/costs/health` reads `v_agent_call_health` directly. Fast — view
  scans only last 7 days of rows via the partial index on
  `(called_at DESC) WHERE error IS NOT NULL` plus the composite
  `(called_at DESC, provider)`.
- `/costs/log-stats` reads `v_agent_call_log_stats` — uses
  `pg_total_relation_size` and `pg_indexes_size` which are O(1) catalog
  lookups. Surfaced in the dashboard footer for sizing decisions.

### `/costs/cleanup` (Phase 1.9, new)

Wraps the `cleanup_agent_call_log()` function. Returns the deleted
count. Subject to the function's `days_to_keep < 7` safeguard which
surfaces as HTTP 400.

---

## 6. What to watch when running G2 for the first time

Phase 1's G2 graph fires ~12 LLM calls per converged build. After your
first real run:

1. **Sanity-check the routing**: open `/costs`, scroll to "Cost by Agent"
   table. You should see entries like:
   - `g2.insider_expert` → google / gemini-2.5-pro
   - `g2.advocate` → anthropic / claude-opus-4-5-...
   - `g2.ats_critic_a` → deepseek / deepseek-reasoner
   - `g2.ats_critic_b` → moonshot / kimi-k2
   - `g2.writer` / `g2.orchestrator` / `g2.polisher` → anthropic
2. **Check the conversion rate**: `iterations` column on the resume_build
   row should be 1–3 (≥1 writer-critic loop).
3. **Cost vs. budget**: the design doc estimated ~$2.01 per converged
   build. Watch the "Top Resume Builds by Cost" table to confirm.
4. **Provider health**: `ProviderHealthBadges` should turn green for
   each provider that's been called. Any badge in amber/red on the first
   run probably indicates a transient API issue, not a systemic problem;
   check `/costs` → Recent Calls → "Errors only" to see the failed call.
5. **Inspect agent_call_log directly**:
   ```sql
   SELECT agent_name, provider, model, latency_ms, cost_usd
   FROM agent_call_log
   WHERE called_at >= NOW() - INTERVAL '1 hour'
   ORDER BY called_at DESC;
   ```

If the routing isn't matching expectations, the fix is in
`config/settings.py` (G2_*_MODEL slots) — not in the router itself.
