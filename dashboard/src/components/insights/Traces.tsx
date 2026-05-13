/**
 * LangGraph trace observability surface (Stream C, 2026-05-13).
 *
 * Embedded inside /insights as the "Traces" tab. Answers the user's
 * question: "what is my LangGraph agent doing right now?"
 *
 * Reads from agent_call_log + the v_graph_runs view (migration 029).
 * Three sections:
 *   - Active runs       (status='in_progress', last 5 minutes)
 *   - Recent runs       (last 7 days, sorted by recency)
 *   - Recent errors     (last 7 days with error IS NOT NULL)
 */
import {
  fetchRecentRuns,
  fetchTraceErrors,
} from '@/lib/api'
import type {
  ErrorsResponse,
  GraphRunSummary,
  RecentRunsResponse,
} from '@/lib/types/traces'

type SettleResult<T> =
  | { ok: true; value: T }
  | { ok: false; error: string }

function settle<T>(p: Promise<T>): Promise<SettleResult<T>> {
  return p
    .then<SettleResult<T>>((value) => ({ ok: true, value }))
    .catch<SettleResult<T>>((err) => ({
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    }))
}

function fmtMoney(n: number): string {
  return `$${n.toFixed(4)}`
}

function fmtLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function fmtElapsed(iso: string | null | undefined): string {
  if (!iso) return '—'
  const ms = Date.now() - new Date(iso).getTime()
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)}m ago`
  if (ms < 86_400_000) return `${Math.floor(ms / 3600_000)}h ago`
  return `${Math.floor(ms / 86_400_000)}d ago`
}

function statusBadgeClass(status: string): string {
  switch (status) {
    case 'in_progress':
      return 'bg-amber-500/15 text-amber-300 border-amber-500/40'
    case 'failed':
      return 'bg-rose-500/15 text-rose-300 border-rose-500/40'
    case 'converged':
      return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
    default:
      return 'bg-fg-subtle/15 text-fg-subtle border-border'
  }
}

function FreshnessBar({ at }: { at: Date }) {
  const iso = at.toISOString().replace('T', ' ').slice(0, 19)
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 text-3xs text-fg-subtle">
      <span>
        <span className="font-mono tabular-nums text-fg-muted">Live</span>{' '}
        from <code className="font-mono">v_graph_runs</code>,{' '}
        <code className="font-mono">agent_call_log</code>
      </span>
      <span className="opacity-60">·</span>
      <span>
        Loaded{' '}
        <time className="font-mono tabular-nums text-fg-muted" dateTime={at.toISOString()}>
          {iso} UTC
        </time>
      </span>
    </div>
  )
}

function RunRow({ run }: { run: GraphRunSummary }) {
  return (
    <tr>
      <td className="py-1 pr-2">
        <span className="font-mono text-2xs text-fg">{run.graph}</span>
      </td>
      <td className="py-1 pr-2 text-3xs text-fg-muted font-mono truncate max-w-[12rem]">
        {run.run_key.slice(0, 18)}{run.run_key.length > 18 ? '…' : ''}
      </td>
      <td className="py-1 pr-2">
        <span
          className={`inline-block rounded border px-1.5 py-0.5 text-3xs ${statusBadgeClass(
            run.status,
          )}`}
        >
          {run.status}
        </span>
      </td>
      <td className="py-1 pr-2 text-3xs text-fg-muted tabular-nums">
        {fmtElapsed(run.last_event_at)}
      </td>
      <td className="py-1 pr-2 text-3xs text-fg-muted tabular-nums">
        {run.total_nodes}
        {run.error_nodes > 0 && (
          <span className="ml-1 text-rose-300">({run.error_nodes} err)</span>
        )}
      </td>
      <td className="py-1 pr-2 text-3xs text-fg-muted tabular-nums">
        {fmtMoney(run.total_cost_usd)}
      </td>
      <td className="py-1 pr-2 text-3xs text-fg-muted tabular-nums">
        {fmtLatency(run.total_latency_ms)}
      </td>
      <td className="py-1 text-3xs text-fg-subtle font-mono truncate max-w-[18rem]">
        {run.nodes_executed.join(' → ')}
      </td>
    </tr>
  )
}

function RunsTable({ runs }: { runs: GraphRunSummary[] }) {
  if (runs.length === 0) {
    return (
      <p className="text-2xs text-fg-subtle">
        No runs yet. Once an agent fires, you&apos;ll see it here.
      </p>
    )
  }
  return (
    <table className="w-full text-2xs">
      <thead className="text-fg-subtle">
        <tr>
          <th className="text-left font-normal pr-2">Graph</th>
          <th className="text-left font-normal pr-2">Run key</th>
          <th className="text-left font-normal pr-2">Status</th>
          <th className="text-left font-normal pr-2">Last event</th>
          <th className="text-left font-normal pr-2">Nodes</th>
          <th className="text-left font-normal pr-2">Cost</th>
          <th className="text-left font-normal pr-2">Latency</th>
          <th className="text-left font-normal">Path</th>
        </tr>
      </thead>
      <tbody className="text-fg">
        {runs.map((r) => (
          <RunRow key={`${r.graph}-${r.run_key}`} run={r} />
        ))}
      </tbody>
    </table>
  )
}

function ErrorsTable({ data }: { data: ErrorsResponse }) {
  if (data.errors.length === 0) {
    return (
      <p className="text-2xs text-emerald-300">{data.message || 'No errors. ✓'}</p>
    )
  }
  return (
    <table className="w-full text-2xs">
      <thead className="text-fg-subtle">
        <tr>
          <th className="text-left font-normal pr-2">When</th>
          <th className="text-left font-normal pr-2">Graph</th>
          <th className="text-left font-normal pr-2">Node</th>
          <th className="text-left font-normal pr-2">Model</th>
          <th className="text-left font-normal">Error</th>
        </tr>
      </thead>
      <tbody className="text-fg">
        {data.errors.map((e, i) => (
          <tr key={`${e.called_at}-${i}`}>
            <td className="py-1 pr-2 text-3xs text-fg-muted tabular-nums">
              {fmtElapsed(e.called_at)}
            </td>
            <td className="py-1 pr-2 text-3xs font-mono">{e.graph ?? '—'}</td>
            <td className="py-1 pr-2 text-3xs font-mono">{e.node_name ?? '—'}</td>
            <td className="py-1 pr-2 text-3xs text-fg-muted">{e.model ?? '—'}</td>
            <td className="py-1 text-3xs text-rose-300 truncate max-w-[24rem]">
              {e.error}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export async function Traces() {
  const [runsResult, errorsResult] = await Promise.all([
    settle(fetchRecentRuns(50)),
    settle(fetchTraceErrors(20)),
  ])

  const loadedAt = new Date()

  const allRuns = runsResult.ok ? runsResult.value.runs : []
  const activeRuns = allRuns.filter((r) => r.status === 'in_progress')
  const recentRuns = allRuns.filter((r) => r.status !== 'in_progress').slice(0, 30)

  return (
    <div className="space-y-4">
      <FreshnessBar at={loadedAt} />

      {/* Active section */}
      <section className="space-y-2 rounded-lg border border-border bg-card p-4">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-fg">Active runs</h3>
          <code className="text-3xs text-fg-subtle font-mono">
            v_graph_runs · status=in_progress
          </code>
        </div>
        {!runsResult.ok ? (
          <p className="text-2xs text-rose-300">
            Live data unavailable: {runsResult.error}
          </p>
        ) : (
          <RunsTable runs={activeRuns} />
        )}
      </section>

      {/* Recent runs section */}
      <section className="space-y-2 rounded-lg border border-border bg-card p-4">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-fg">Recent runs (last 7 days)</h3>
          <code className="text-3xs text-fg-subtle font-mono">v_graph_runs</code>
        </div>
        {!runsResult.ok ? (
          <p className="text-2xs text-rose-300">
            Live data unavailable: {runsResult.error}
          </p>
        ) : (
          <RunsTable runs={recentRuns} />
        )}
      </section>

      {/* Errors section */}
      <section className="space-y-2 rounded-lg border border-border bg-card p-4">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-fg">Recent errors</h3>
          <code className="text-3xs text-fg-subtle font-mono">
            agent_call_log · error IS NOT NULL
          </code>
        </div>
        {!errorsResult.ok ? (
          <p className="text-2xs text-rose-300">
            Live data unavailable: {errorsResult.error}
          </p>
        ) : (
          <ErrorsTable data={errorsResult.value} />
        )}
      </section>
    </div>
  )
}
