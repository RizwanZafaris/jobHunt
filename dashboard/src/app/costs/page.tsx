import {
  fetchCostSummary,
  fetchDailyCost,
  fetchCostByProvider,
  fetchCostByAgent,
  fetchCostByResumeBuild,
  fetchRecentCalls,
  type CostSummary,
  type DailyCostResponse,
  type CostByProviderRow,
  type CostByAgentRow,
  type CostByResumeBuildRow,
  type AgentCallLogRow,
} from '@/lib/profile-api'
import Link from 'next/link'
import ProfileNav from '@/components/ProfileNav'
import CostSummaryCards from '@/components/CostSummaryCards'
import DailyCostChart from '@/components/DailyCostChart'
import CostByProviderChart from '@/components/CostByProviderChart'
import CostByAgentTable from '@/components/CostByAgentTable'
import RecentCallsTable from '@/components/RecentCallsTable'

export const dynamic = 'force-dynamic'

export default async function CostsPage() {
  let summary: CostSummary | null = null
  let daily: DailyCostResponse | null = null
  let byProvider: { providers: CostByProviderRow[]; days: number } | null = null
  let byAgent: { agents: CostByAgentRow[]; days: number } | null = null
  let byBuild: { builds: CostByResumeBuildRow[] } | null = null
  let recentCalls: { calls: AgentCallLogRow[]; warning?: string } | null = null
  let error: string | null = null

  try {
    const [s, d, p, a, b, r] = await Promise.all([
      fetchCostSummary(),
      fetchDailyCost(30),
      fetchCostByProvider(7),
      fetchCostByAgent(7),
      fetchCostByResumeBuild(20),
      fetchRecentCalls({ limit: 100 }),
    ])
    summary = s
    daily = d
    byProvider = p
    byAgent = a
    byBuild = b
    recentCalls = r
  } catch (e: any) {
    error = e?.message || 'Failed to load cost data'
  }

  return (
    <Shell>
      {/* Header */}
      <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold text-white">💸 LLM Cost Observability</h1>
            <p className="text-sm text-gray-400 mt-1">
              Per-call cost + latency telemetry from{' '}
              <code className="text-gray-300">agent_call_log</code>.{' '}
              <span className="text-gray-500">
                Written by{' '}
                <code className="text-gray-400">agents/llm_router.py</code>{' '}
                on every successful LLM call across all 5 providers.
              </span>
            </p>
          </div>
          {summary && (
            <div className="text-right text-xs text-gray-400">
              <div>
                Today:{' '}
                <span className="text-white font-bold">
                  ${summary.today.cost_usd.toFixed(2)}
                </span>
              </div>
              <div>
                30d:{' '}
                <span className="text-white font-bold">
                  ${summary.last_30d.cost_usd.toFixed(2)}
                </span>
              </div>
            </div>
          )}
        </div>
      </section>

      {error && (
        <div className="bg-red-900/30 border border-red-900/60 rounded-xl p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {summary && summary.warning && (
        <div className="bg-amber-900/20 border border-amber-900/50 rounded-xl p-4 text-sm text-amber-300">
          ⚠ {summary.warning} — apply{' '}
          <code className="text-amber-200">db/multi_llm_schema.sql</code> against
          your Supabase project to start collecting cost telemetry.
        </div>
      )}

      {/* Summary cards */}
      {summary && <CostSummaryCards summary={summary} />}

      {/* Daily trend */}
      {daily && (
        <DailyCostChart rows={daily.rows} days={daily.days} warning={daily.warning} />
      )}

      {/* Side-by-side: provider donut + agent bar */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
        <div className="lg:col-span-2">
          {byProvider && (
            <CostByProviderChart
              providers={byProvider.providers}
              days={byProvider.days}
            />
          )}
        </div>
        <div className="lg:col-span-3">
          {byAgent && <CostByAgentTable agents={byAgent.agents} days={byAgent.days} />}
        </div>
      </div>

      {/* Top resume builds by cost */}
      {byBuild && byBuild.builds.length > 0 && (
        <section className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-800">
            <h2 className="text-base font-semibold text-white">Top Resume Builds by Cost</h2>
            <p className="text-[11px] text-gray-500 mt-0.5">
              Last 90 days · most expensive first
            </p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-950/50 text-gray-400 uppercase text-[10px]">
                <tr>
                  <th className="px-3 py-2 text-left">Company</th>
                  <th className="px-3 py-2 text-right">Polisher</th>
                  <th className="px-3 py-2 text-right">Iters</th>
                  <th className="px-3 py-2 text-right">Calls</th>
                  <th className="px-3 py-2 text-right">Tokens</th>
                  <th className="px-3 py-2 text-right">Cost</th>
                  <th className="px-3 py-2 text-left">Status</th>
                  <th className="px-3 py-2 text-left">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/60">
                {byBuild.builds.map((b) => (
                  <tr key={b.resume_build_id} className="hover:bg-gray-800/30">
                    <td className="px-3 py-2 text-gray-200">{b.company_name || '—'}</td>
                    <td className="px-3 py-2 text-right text-gray-300">
                      {b.polisher_score ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-400">
                      {b.iterations ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-300">{b.calls}</td>
                    <td className="px-3 py-2 text-right text-gray-400 whitespace-nowrap">
                      {(b.input_tokens / 1000).toFixed(1)}k /{' '}
                      {(b.output_tokens / 1000).toFixed(1)}k
                    </td>
                    <td className="px-3 py-2 text-right text-white font-bold">
                      ${b.cost_usd.toFixed(2)}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`text-[10px] border px-1.5 py-0.5 rounded uppercase ${
                          b.status === 'converged'
                            ? 'bg-emerald-900/40 border-emerald-800 text-emerald-300'
                            : b.status === 'failed'
                            ? 'bg-red-900/30 border-red-900/60 text-red-400'
                            : 'bg-gray-800 border-gray-700 text-gray-400'
                        }`}
                      >
                        {b.status || '?'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-gray-500 text-[10px]">
                      {b.created_at
                        ? new Date(b.created_at).toLocaleDateString('en-GB', {
                            day: 'numeric',
                            month: 'short',
                          })
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Recent calls — debug-grade visibility */}
      {recentCalls && (
        <RecentCallsTable initial={recentCalls.calls} warning={recentCalls.warning} />
      )}

      {/* Footer help */}
      <section className="bg-gray-900/50 border border-gray-800 rounded-xl p-4 text-[11px] text-gray-500 leading-relaxed">
        <p className="font-semibold text-gray-400 mb-1">How cost telemetry flows</p>
        <ol className="list-decimal list-inside space-y-0.5">
          <li>
            Every <code className="text-gray-400">agents/llm_router.py</code>{' '}
            call wraps the request with <code>time.perf_counter()</code> + cost
            estimate (from <code>PRICING_PER_1M</code> table).
          </li>
          <li>
            On success, the default log callback INSERTs a row into{' '}
            <code className="text-gray-400">agent_call_log</code>:
            (provider, model, tokens, cost_usd, latency_ms, agent_name,
            resume_build_id, called_at).
          </li>
          <li>
            This page reads via the{' '}
            <code className="text-gray-400">v_daily_llm_cost</code> view (for
            daily rollups) + raw aggregations (for provider/agent/build).
          </li>
          <li>
            Pricing is best-effort — update{' '}
            <code className="text-gray-400">PRICING_PER_1M</code> in{' '}
            <code className="text-gray-400">agents/llm_router.py</code> when
            providers change rates.{' '}
            <Link href="/personas" className="text-blue-400 hover:text-blue-300">
              See personas →
            </Link>
          </li>
        </ol>
      </section>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
          <h1 className="text-xl font-bold text-white">🤖 Job Hunt AI</h1>
          <ProfileNav />
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">{children}</main>
    </div>
  )
}
