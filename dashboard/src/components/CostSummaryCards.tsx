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

  return (
    <Card padding="lg">
      <dl className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-5">
        <Stat
          label="Today"
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
          label={`Avg / build (${summary.n_resume_builds})`}
          value={`$${summary.avg_per_resume_build.toFixed(2)}`}
          hint={
            summary.n_resume_builds === 0
              ? 'no G2 builds yet'
              : `across ${summary.n_resume_builds} resume_build${summary.n_resume_builds === 1 ? '' : 's'}`
          }
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
