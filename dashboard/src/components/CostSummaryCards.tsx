import { type CostSummary } from '@/lib/profile-api'
import { Card } from '@/components/ui/Card'
import { Stat } from '@/components/ui/Stat'

interface Props {
  summary: CostSummary
}

export default function CostSummaryCards({ summary }: Props) {
  const today = summary.today
  const last7d = summary.last_7d
  const last30d = summary.last_30d
  const nBuilds = summary.n_resume_builds

  // BUG-014: when there are no builds in the selected window, the
  // "AVG / BUILD (0) $0.00" tile reads as a contradiction. Replace the
  // value with a clear sentence and skip the misleading "$0.00" number.
  // The window matches the cost summary endpoint (last 90 days).
  const buildsTile = nBuilds === 0
    ? { label: 'Avg / build', value: '—', hint: 'No G2 builds in the time window selected' }
    : {
        label: `Avg / build (${nBuilds})`,
        value: `$${summary.avg_per_resume_build.toFixed(2)}`,
        hint: `across ${nBuilds} build${nBuilds === 1 ? '' : 's'}`,
      }

  return (
    <Card padding="lg">
      <dl className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-5">
        <Stat
          label="Today (UTC)"
          value={`$${today.cost_usd.toFixed(2)}`}
          hint={fmtCalls(today.calls, today.avg_latency_ms)}
        />
        <Stat
          label="Last 7 days"
          value={`$${last7d.cost_usd.toFixed(2)}`}
          hint={fmtCalls(last7d.calls, last7d.avg_latency_ms)}
        />
        <Stat
          label="Last 30 days"
          value={`$${last30d.cost_usd.toFixed(2)}`}
          hint={fmtCalls(last30d.calls, last30d.avg_latency_ms)}
        />
        <Stat
          label={buildsTile.label}
          value={buildsTile.value}
          hint={buildsTile.hint}
        />
      </dl>
    </Card>
  )
}

function fmtCalls(calls: number | null, latencyMs: number | null): string {
  if (calls === null) return ''
  const callStr = `${calls} call${calls === 1 ? '' : 's'}`
  if (latencyMs && latencyMs > 0) return `${callStr} · ~${(latencyMs / 1000).toFixed(1)} s avg`
  return callStr
}
