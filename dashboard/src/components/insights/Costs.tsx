/**
 * Costs insight surface.
 *
 * Extracted from app/costs/page.tsx so /insights can host it under the
 * Costs sub-tab without re-implementing data fetching.
 */
import Link from 'next/link'
import {
  fetchCostSummary,
  fetchDailyCost,
  fetchCostByProvider,
  fetchCostByAgent,
  fetchCostByResumeBuild,
  fetchRecentCalls,
  fetchCostHealth,
  fetchLogStats,
  fetchLastAlerts,
  type CostSummary,
  type DailyCostResponse,
  type CostByProviderRow,
  type CostByAgentRow,
  type CostByResumeBuildRow,
  type AgentCallLogRow,
  type ProviderHealthRow,
  type AgentCallLogStats,
  type AlertHistoryEntry,
} from '@/lib/profile-api'
import CostSummaryCards from '@/components/CostSummaryCards'
import DailyCostChart from '@/components/DailyCostChart'
import CostByProviderChart from '@/components/CostByProviderChart'
import CostByAgentTable from '@/components/CostByAgentTable'
import RecentCallsTable from '@/components/RecentCallsTable'
import ProviderHealthBadges from '@/components/ProviderHealthBadges'
import AlertHistoryTable from '@/components/AlertHistoryTable'
import { Card } from '@/components/ui/Card'
import { Pill } from '@/components/ui/Pill'

export async function Costs() {
  let summary: CostSummary | null = null
  let daily: DailyCostResponse | null = null
  let byProvider: { providers: CostByProviderRow[]; days: number } | null = null
  let byAgent: { agents: CostByAgentRow[]; days: number } | null = null
  let byBuild: { builds: CostByResumeBuildRow[] } | null = null
  let recentCalls: { calls: AgentCallLogRow[]; warning?: string } | null = null
  let health: { providers: ProviderHealthRow[]; warning?: string } | null = null
  let logStats: AgentCallLogStats | null = null
  let alerts: { alerts: AlertHistoryEntry[]; warning?: string } | null = null
  let error: string | null = null

  try {
    const [s, d, p, a, b, r, h, ls, al] = await Promise.all([
      fetchCostSummary(),
      fetchDailyCost(30),
      fetchCostByProvider(7),
      fetchCostByAgent(7),
      fetchCostByResumeBuild(20),
      fetchRecentCalls({ limit: 100 }),
      fetchCostHealth().catch(() => ({ providers: [] as ProviderHealthRow[] })),
      fetchLogStats().catch(() => ({ total_rows: 0 } as AgentCallLogStats)),
      fetchLastAlerts().catch(() => ({
        alerts: [] as AlertHistoryEntry[],
        warning: 'fetch failed',
      })),
    ])
    summary = s
    daily = d
    byProvider = p
    byAgent = a
    byBuild = b
    recentCalls = r
    health = h
    logStats = ls
    alerts = al
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load cost data'
  }

  return (
    <div className="space-y-6">
      {summary && (
        <div
          className="flex items-center gap-3 text-2xs text-fg-muted cursor-help"
          title="Time windows are computed from UTC midnight, matching the agent_call_log timestamps. The Recent Calls table below shows wall-clock relative times."
        >
          <span>Today (UTC) <span className="text-fg font-semibold tnum">${summary.today.cost_usd.toFixed(2)}</span></span>
          <span className="text-fg-subtle" aria-hidden>·</span>
          <span>30 d <span className="text-fg font-semibold tnum">${summary.last_30d.cost_usd.toFixed(2)}</span></span>
        </div>
      )}

      {error && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger">{error}</p>
        </Card>
      )}

      {summary?.warning && (
        <Card tone="warning" padding="sm">
          <p className="text-xs text-warning">
            {summary.warning} — apply{' '}
            <code className="font-mono">db/multi_llm_schema.sql</code> against your Supabase project to start collecting cost telemetry.
          </p>
        </Card>
      )}

      {summary && <CostSummaryCards summary={summary} />}
      {health && <ProviderHealthBadges providers={health.providers} warning={health.warning} />}
      {daily && <DailyCostChart rows={daily.rows} days={daily.days} warning={daily.warning} />}

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-2">
          {byProvider && (
            <CostByProviderChart providers={byProvider.providers} days={byProvider.days} />
          )}
        </div>
        <div className="lg:col-span-3">
          {byAgent && <CostByAgentTable agents={byAgent.agents} days={byAgent.days} />}
        </div>
      </div>

      {byBuild && byBuild.builds.length > 0 && (
        <Card title="Top resume builds by cost" description="Last 90 days · most expensive first" padding="none">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <caption className="sr-only">Top resume builds by cost in the last 90 days.</caption>
              <thead className="bg-surface-raised text-fg-subtle uppercase text-2xs">
                <tr>
                  <th scope="col" className="px-4 py-2.5 text-left font-semibold tracking-wider">Company</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold tracking-wider">Polisher</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold tracking-wider">Iters</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold tracking-wider">Calls</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold tracking-wider">Tokens</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-semibold tracking-wider">Cost</th>
                  <th scope="col" className="px-3 py-2.5 text-left font-semibold tracking-wider">Status</th>
                  <th scope="col" className="px-4 py-2.5 text-left font-semibold tracking-wider">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {byBuild.builds.map((b) => (
                  <tr key={b.resume_build_id} className="hover:bg-surface-raised">
                    <td className="px-4 py-2 text-fg">{b.company_name || '—'}</td>
                    <td className="px-3 py-2 text-right text-fg-muted tnum">{b.polisher_score ?? '—'}</td>
                    <td className="px-3 py-2 text-right text-fg-muted tnum">{b.iterations ?? '—'}</td>
                    <td className="px-3 py-2 text-right text-fg-muted tnum">{b.calls}</td>
                    <td className="px-3 py-2 text-right text-fg-muted tnum whitespace-nowrap">
                      {(b.input_tokens / 1000).toFixed(1)}k / {(b.output_tokens / 1000).toFixed(1)}k
                    </td>
                    <td className="px-3 py-2 text-right text-fg font-semibold tnum">${b.cost_usd.toFixed(2)}</td>
                    <td className="px-3 py-2">
                      <Pill tone={b.status === 'converged' ? 'success' : b.status === 'failed' ? 'danger' : 'neutral'}>
                        {b.status || '?'}
                      </Pill>
                    </td>
                    <td className="px-4 py-2 text-fg-subtle whitespace-nowrap">
                      {b.created_at
                        ? new Date(b.created_at).toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {recentCalls && (
        <RecentCallsTable initial={recentCalls.calls} warning={recentCalls.warning} />
      )}

      {alerts && (
        <AlertHistoryTable alerts={alerts.alerts} warning={alerts.warning} />
      )}

      <Card title="Call log stats" description="Storage health and scaling guidance">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <div>
            {logStats?.warning ? (
              <p className="text-2xs text-warning">{logStats.warning}</p>
            ) : logStats ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <dt className="text-fg-subtle">Total rows</dt>
                <dd className="text-fg font-mono tnum">{logStats.total_rows.toLocaleString()}</dd>
                {logStats.rows_last_24h !== undefined && (
                  <>
                    <dt className="text-fg-subtle">Last 24 h</dt>
                    <dd className="text-fg font-mono tnum">{logStats.rows_last_24h.toLocaleString()}</dd>
                  </>
                )}
                {logStats.rows_last_7d !== undefined && (
                  <>
                    <dt className="text-fg-subtle">Last 7 d</dt>
                    <dd className="text-fg font-mono tnum">{logStats.rows_last_7d.toLocaleString()}</dd>
                  </>
                )}
                {logStats.total_size && (
                  <>
                    <dt className="text-fg-subtle">Table size</dt>
                    <dd className="text-fg font-mono">{logStats.total_size}</dd>
                  </>
                )}
                {logStats.indexes_size && (
                  <>
                    <dt className="text-fg-subtle">Indexes</dt>
                    <dd className="text-fg font-mono">{logStats.indexes_size}</dd>
                  </>
                )}
                {logStats.oldest_row_at && (
                  <>
                    <dt className="text-fg-subtle">Oldest entry</dt>
                    <dd className="text-fg font-mono">
                      {new Date(logStats.oldest_row_at).toLocaleDateString('en-US', {
                        day: 'numeric',
                        month: 'short',
                        year: 'numeric',
                        timeZone: 'UTC',
                      })}
                    </dd>
                  </>
                )}
              </dl>
            ) : (
              <p className="text-2xs text-fg-subtle">…</p>
            )}
          </div>
          <div>
            <p className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-2">Scale-up guidance</p>
            <ul className="text-2xs text-fg-muted space-y-1.5 leading-relaxed">
              <li>
                <span className="text-fg">≤ 10 k rows</span> — current setup is fine. Composite indexes from{' '}
                <code className="font-mono text-fg">agent_call_log_perf.sql</code> keep all queries sub-50 ms.
              </li>
              <li>
                <span className="text-warning">10 k – 100 k rows</span> — consider cleanup cron:{' '}
                <code className="font-mono text-fg">POST /costs/cleanup</code> with default 365-day retention.
              </li>
              <li>
                <span className="text-danger">&gt; 100 k rows</span> — apply the partition setup at the bottom of{' '}
                <code className="font-mono text-fg">db/agent_call_log_perf.sql</code> (monthly via pg_partman). See{' '}
                <Link href="/insights?tab=personas" className="text-info hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded">
                  docs/PERF.md
                </Link>{' '}
                once it lands.
              </li>
            </ul>
          </div>
        </div>
      </Card>
    </div>
  )
}

export default Costs
