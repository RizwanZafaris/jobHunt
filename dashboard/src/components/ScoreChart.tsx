'use client'

import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer } from 'recharts'

interface Props {
  stats: any
}

const SCORE_COLORS = {
  '80+': '#10b981',
  '60-79': '#3b82f6',
  '40-59': '#f59e0b',
  '0-39': '#6b7280',
}

const STATUS_COLORS = [
  '#3b82f6', '#8b5cf6', '#06b6d4', '#f59e0b', '#10b981', '#ef4444',
]

export default function ScoreChart({ stats }: Props) {
  const byScore = stats?.by_score ?? {}
  const byStatus = stats?.by_status ?? {}

  const scoreData = Object.entries(byScore)
    .map(([range, count]) => ({ range, count }))
    .reverse()

  const statusData = Object.entries(byStatus)
    .map(([status, count]) => ({ status, count }))
    .sort((a: any, b: any) => b.count - a.count)

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-6">
      <h2 className="text-base font-semibold text-white mb-6">Pipeline Analytics</h2>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-8">
        {/* Score Distribution */}
        <div>
          <h3 className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-3">
            Match Score Distribution
          </h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={scoreData} barSize={28}>
              <XAxis
                dataKey="range"
                tick={{ fill: '#9ca3af', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#9ca3af', fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={30}
              />
              <Tooltip
                contentStyle={{
                  background: '#1f2937',
                  border: '1px solid #374151',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
                labelStyle={{ color: '#e5e7eb' }}
              />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {scoreData.map((entry) => (
                  <Cell
                    key={entry.range}
                    fill={SCORE_COLORS[entry.range as keyof typeof SCORE_COLORS] || '#6b7280'}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Status Distribution */}
        <div>
          <h3 className="text-xs font-medium text-gray-400 uppercase tracking-wider mb-3">
            Status Breakdown
          </h3>
          <div className="space-y-2">
            {statusData.slice(0, 8).map(({ status, count }, i) => {
              const total = statusData.reduce((sum: number, s: any) => sum + (s.count as number), 0)
              const pct = total > 0 ? Math.round((count as number) / total * 100) : 0
              return (
                <div key={status} className="flex items-center gap-3">
                  <div className="w-20 text-xs text-gray-400 text-right truncate">{status}</div>
                  <div className="flex-1 bg-gray-800 rounded-full h-2 overflow-hidden">
                    <div
                      className="h-2 rounded-full transition-all"
                      style={{
                        width: `${pct}%`,
                        backgroundColor: STATUS_COLORS[i % STATUS_COLORS.length],
                      }}
                    />
                  </div>
                  <div className="w-8 text-xs text-gray-400 text-right">{count as number}</div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
