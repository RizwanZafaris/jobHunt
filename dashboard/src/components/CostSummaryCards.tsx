import { type CostSummary } from '@/lib/profile-api'

interface Props {
  summary: CostSummary
}

export default function CostSummaryCards({ summary }: Props) {
  const today = summary.today
  const last7d = summary.last_7d
  const last30d = summary.last_30d

  return (
    <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <Card
        label="Today"
        cost={today.cost_usd}
        calls={today.calls}
        avgLatencyMs={today.avg_latency_ms}
        color="emerald"
      />
      <Card
        label="Last 7 days"
        cost={last7d.cost_usd}
        calls={last7d.calls}
        avgLatencyMs={last7d.avg_latency_ms}
        color="blue"
      />
      <Card
        label="Last 30 days"
        cost={last30d.cost_usd}
        calls={last30d.calls}
        avgLatencyMs={last30d.avg_latency_ms}
        color="violet"
      />
      <Card
        label={`Avg / build (${summary.n_resume_builds})`}
        cost={summary.avg_per_resume_build}
        calls={null}
        avgLatencyMs={null}
        color="amber"
        sub={
          summary.n_resume_builds === 0
            ? 'no G2 builds yet'
            : `across ${summary.n_resume_builds} resume_build${summary.n_resume_builds === 1 ? '' : 's'}`
        }
      />
    </section>
  )
}

const COLOR_CLS: Record<string, string> = {
  emerald: 'bg-emerald-900/30 border-emerald-800/60 text-emerald-300',
  blue: 'bg-blue-900/30 border-blue-800/60 text-blue-300',
  violet: 'bg-violet-900/30 border-violet-800/60 text-violet-300',
  amber: 'bg-amber-900/30 border-amber-800/60 text-amber-300',
}

function Card({
  label,
  cost,
  calls,
  avgLatencyMs,
  color,
  sub,
}: {
  label: string
  cost: number
  calls: number | null
  avgLatencyMs: number | null
  color: keyof typeof COLOR_CLS
  sub?: string
}) {
  return (
    <div className={`border rounded-xl px-4 py-3 ${COLOR_CLS[color]}`}>
      <div className="text-[10px] uppercase tracking-wider opacity-80">{label}</div>
      <div className="text-2xl font-bold mt-0.5">${cost.toFixed(2)}</div>
      <div className="text-[10px] opacity-75 mt-1">
        {calls !== null && (
          <>
            {calls} call{calls === 1 ? '' : 's'}
            {avgLatencyMs !== null && avgLatencyMs > 0 && (
              <> · ~{(avgLatencyMs / 1000).toFixed(1)}s avg</>
            )}
          </>
        )}
        {sub && sub}
      </div>
    </div>
  )
}
