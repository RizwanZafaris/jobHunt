'use client'

interface StatsProps {
  stats: any
  companiesCount: number
}

export default function StatsCards({ stats, companiesCount }: StatsProps) {
  const totalJobs = stats?.total_jobs ?? 0
  const byStatus = stats?.by_status ?? {}
  const byScore = stats?.by_score ?? {}
  const applications = stats?.applications ?? {}

  const cards = [
    {
      label: 'Total Jobs Found',
      value: totalJobs,
      sub: `${byStatus.new ?? 0} new today`,
      color: 'text-blue-400',
      bg: 'bg-blue-900/20 border-blue-800',
      icon: '🔍',
    },
    {
      label: 'High Match (80+)',
      value: byScore['80+'] ?? 0,
      sub: `${byScore['60-79'] ?? 0} strong fits (60-79)`,
      color: 'text-emerald-400',
      bg: 'bg-emerald-900/20 border-emerald-800',
      icon: '⭐',
    },
    {
      label: 'Applications',
      value: Object.values(applications).reduce((a: any, b: any) => a + b, 0) as number,
      sub: `${applications.interviewing ?? 0} in interviews`,
      color: 'text-purple-400',
      bg: 'bg-purple-900/20 border-purple-800',
      icon: '📨',
    },
    {
      label: 'Companies Tracked',
      value: companiesCount,
      sub: 'with live intel',
      color: 'text-amber-400',
      bg: 'bg-amber-900/20 border-amber-800',
      icon: '🏢',
    },
    {
      label: 'Offers',
      value: applications.offer ?? 0,
      sub: applications.accepted ? `${applications.accepted} accepted` : 'Keep pushing',
      color: 'text-rose-400',
      bg: 'bg-rose-900/20 border-rose-800',
      icon: '🎯',
    },
  ]

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
      {cards.map((card) => (
        <div
          key={card.label}
          className={`rounded-xl border p-4 ${card.bg}`}
        >
          <div className="flex items-center justify-between mb-1">
            <span className="text-lg">{card.icon}</span>
          </div>
          <div className={`text-2xl font-bold ${card.color}`}>{card.value}</div>
          <div className="text-xs text-gray-300 font-medium mt-0.5">{card.label}</div>
          <div className="text-xs text-gray-500 mt-1">{card.sub}</div>
        </div>
      ))}
    </div>
  )
}
