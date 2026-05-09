'use client'

import { Card } from '@/components/ui/Card'
import { Stat } from '@/components/ui/Stat'

interface StatsValue {
  total_jobs?: number
  by_status?: Record<string, number>
  by_score?: Record<string, number>
  applications?: Record<string, number>
}

interface StatsProps {
  stats: StatsValue | null
  companiesCount: number
}

export default function StatsCards({ stats, companiesCount }: StatsProps) {
  const totalJobs = stats?.total_jobs ?? 0
  const byStatus = stats?.by_status ?? {}
  const byScore = stats?.by_score ?? {}
  const applications = stats?.applications ?? {}
  const totalApps = Object.values(applications).reduce<number>((a, b) => a + (b ?? 0), 0)

  return (
    <Card padding="lg">
      <dl className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-x-6 gap-y-5">
        <Stat
          label="Jobs found"
          value={totalJobs.toLocaleString()}
          hint={`${byStatus.new ?? 0} new today`}
        />
        <Stat
          label="High match"
          value={byScore['80+'] ?? 0}
          hint={`${byScore['60-79'] ?? 0} strong fits 60–79`}
          tone="success"
        />
        <Stat
          label="Applications"
          value={totalApps}
          hint={`${applications.interviewing ?? 0} in interviews`}
        />
        <Stat
          label="Companies tracked"
          value={companiesCount}
          hint="with live intel"
        />
        <Stat
          label="Offers"
          value={applications.offer ?? 0}
          hint={applications.accepted ? `${applications.accepted} accepted` : 'keep pushing'}
          tone={applications.offer ? 'success' : 'default'}
        />
      </dl>
    </Card>
  )
}
