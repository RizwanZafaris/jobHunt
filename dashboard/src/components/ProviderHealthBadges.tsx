import { type ProviderHealthRow } from '@/lib/profile-api'

interface Props {
  providers: ProviderHealthRow[]
  warning?: string
}

const PROVIDER_COLOR: Record<string, string> = {
  anthropic: 'bg-amber-900/40 border-amber-800 text-amber-300',
  google: 'bg-blue-900/40 border-blue-800 text-blue-300',
  openai: 'bg-emerald-900/40 border-emerald-800 text-emerald-300',
  deepseek: 'bg-violet-900/40 border-violet-800 text-violet-300',
  moonshot: 'bg-pink-900/40 border-pink-800 text-pink-300',
}

/**
 * Per-provider health card strip — error rate + latency p95 over last 7 days.
 *
 * Visual rules (set conservatively for fintech-grade reliability):
 *   error_rate ≤ 1%       → green check
 *   error_rate ≤ 5%       → amber warn
 *   error_rate > 5%       → red alert
 *
 *   p95 ≤ 8s              → fine
 *   p95 ≤ 20s             → amber (LLM calls can be slow but should improve)
 *   p95 > 20s             → red
 */
export default function ProviderHealthBadges({ providers, warning }: Props) {
  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h2 className="text-base font-semibold text-white">Provider Health</h2>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Error rate + latency percentiles over last 7 days
          </p>
        </div>
        <p className="text-[11px] text-gray-500">
          source: <code className="text-gray-400">v_agent_call_health</code>
        </p>
      </div>

      {warning && (
        <div className="text-[11px] text-amber-400 bg-amber-900/20 border border-amber-900/50 rounded-lg px-3 py-2 mb-4">
          ⚠ {warning}
        </div>
      )}

      {providers.length === 0 ? (
        <div className="text-center py-8 border border-dashed border-gray-800 rounded-lg">
          <p className="text-sm text-gray-500">No calls in last 7 days yet.</p>
          <p className="text-xs text-gray-600 mt-1">
            Health metrics appear once LLM traffic flows through{' '}
            <code className="text-gray-500">agents/llm_router.py</code>.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {providers.map((p) => (
            <ProviderCard key={p.provider} row={p} />
          ))}
        </div>
      )}
    </section>
  )
}

function ProviderCard({ row }: { row: ProviderHealthRow }) {
  const errCls = severityClass(row.error_rate_pct, 1, 5)
  const p95Cls = severityClass(row.p95_latency_ms / 1000, 8, 20)
  const overallOk = errCls.tier === 'good' && p95Cls.tier === 'good'

  return (
    <div className={`border rounded-lg p-3 ${PROVIDER_COLOR[row.provider] || 'bg-gray-800 border-gray-700 text-gray-300'}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <code className="text-sm font-mono font-semibold">{row.provider}</code>
          <span className="text-[10px] opacity-70">
            {row.calls_7d} call{row.calls_7d === 1 ? '' : 's'}
          </span>
        </div>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded font-bold ${
            overallOk ? 'bg-emerald-700/40 text-emerald-200' : 'bg-amber-700/40 text-amber-200'
          }`}
        >
          {overallOk ? '✓ OK' : '⚠ check'}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-[11px]">
        <Metric
          label="Error rate"
          value={`${row.error_rate_pct.toFixed(2)}%`}
          sub={`${row.errors_7d} of ${row.calls_7d}`}
          tone={errCls.tier}
        />
        <Metric
          label="p95 latency"
          value={`${(row.p95_latency_ms / 1000).toFixed(1)}s`}
          sub={`p50 ${(row.p50_latency_ms / 1000).toFixed(1)}s · p99 ${(row.p99_latency_ms / 1000).toFixed(1)}s`}
          tone={p95Cls.tier}
        />
      </div>

      <div className="mt-2 pt-2 border-t border-current/10 flex items-center justify-between text-[10px] opacity-70">
        <span>${row.total_cost_usd_7d.toFixed(2)} spent</span>
        <span title={row.last_call_at || 'no calls'}>
          {row.last_call_at ? `last call ${relativeTime(row.last_call_at)}` : 'no calls'}
        </span>
      </div>
    </div>
  )
}

type Tier = 'good' | 'warn' | 'bad'

function severityClass(value: number, warnThreshold: number, badThreshold: number): {
  tier: Tier
} {
  if (value <= warnThreshold) return { tier: 'good' }
  if (value <= badThreshold) return { tier: 'warn' }
  return { tier: 'bad' }
}

const TONE_TEXT: Record<Tier, string> = {
  good: 'text-emerald-200',
  warn: 'text-amber-200',
  bad: 'text-red-300',
}

function Metric({
  label,
  value,
  sub,
  tone,
}: {
  label: string
  value: string
  sub: string
  tone: Tier
}) {
  return (
    <div className="bg-black/20 rounded px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider opacity-70">{label}</div>
      <div className={`text-base font-bold ${TONE_TEXT[tone]}`}>{value}</div>
      <div className="text-[10px] opacity-60 mt-0.5">{sub}</div>
    </div>
  )
}

function relativeTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = Date.now()
  const diffSec = Math.floor((now - d.getTime()) / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
  return `${Math.floor(diffSec / 86400)}d ago`
}
