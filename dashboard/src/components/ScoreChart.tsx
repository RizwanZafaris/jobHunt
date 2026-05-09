'use client'

import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer } from 'recharts'
import { useEffect, useState } from 'react'
import { Card } from '@/components/ui/Card'

interface StatsValue {
  by_score?: Record<string, number>
  by_status?: Record<string, number>
}

interface Props {
  stats: StatsValue | null
}

/**
 * Hook into CSS variables so the chart palette follows the theme.
 * Recharts can't read CSS variables directly — we resolve them once on
 * mount and on theme change.
 */
function useTokenColors() {
  const [colors, setColors] = useState({
    fg: '#18181b',
    fgMuted: '#71717a',
    border: '#e4e4e7',
    surface: '#ffffff',
    success: '#15803d',
    info: '#1d4ed8',
    warning: '#a16207',
    fgSubtle: '#a1a1aa',
  })

  useEffect(() => {
    function read() {
      const cs = getComputedStyle(document.documentElement)
      const c = (name: string) => `rgb(${cs.getPropertyValue(name).trim()})`
      setColors({
        fg: c('--fg'),
        fgMuted: c('--fg-muted'),
        border: c('--border'),
        surface: c('--surface'),
        success: c('--success'),
        info: c('--info'),
        warning: c('--warning'),
        fgSubtle: c('--fg-subtle'),
      })
    }
    read()
    const obs = new MutationObserver(read)
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] })
    return () => obs.disconnect()
  }, [])

  return colors
}

export default function ScoreChart({ stats }: Props) {
  const c = useTokenColors()
  const byScore = stats?.by_score ?? {}
  const byStatus = stats?.by_status ?? {}

  const SCORE_COLORS: Record<string, string> = {
    '80+':   c.success,
    '60-79': c.info,
    '40-59': c.warning,
    '0-39':  c.fgSubtle,
  }

  const scoreData = Object.entries(byScore)
    .map(([range, count]) => ({ range, count }))
    .reverse()

  const statusEntries = Object.entries(byStatus)
    .map(([status, count]) => ({ status, count: count as number }))
    .sort((a, b) => b.count - a.count)
  const totalStatus = statusEntries.reduce((s, x) => s + x.count, 0)

  return (
    <Card title="Pipeline analytics">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
        {/* Score Distribution */}
        <figure>
          <figcaption className="text-2xs font-semibold text-fg-subtle uppercase tracking-wider mb-2">
            Match score distribution
          </figcaption>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={scoreData} barSize={28}>
              <XAxis
                dataKey="range"
                tick={{ fill: c.fgMuted, fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: c.fgMuted, fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                width={26}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ fill: 'transparent' }}
                contentStyle={{
                  background: c.surface,
                  border: `1px solid ${c.border}`,
                  borderRadius: '8px',
                  fontSize: '12px',
                  color: c.fg,
                  boxShadow: '0 4px 12px -2px rgb(0 0 0 / 0.08)',
                }}
                labelStyle={{ color: c.fg }}
              />
              <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                {scoreData.map((entry) => (
                  <Cell key={entry.range} fill={SCORE_COLORS[entry.range] || c.fgSubtle} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-2xs text-fg-muted">
            {Object.entries(SCORE_COLORS).reverse().map(([range, color]) => (
              <li key={range} className="flex items-center gap-1.5">
                <span aria-hidden className="inline-block w-2 h-2 rounded-sm" style={{ background: color }} />
                {range}
              </li>
            ))}
          </ul>
        </figure>

        {/* Status Distribution */}
        <figure>
          <figcaption className="text-2xs font-semibold text-fg-subtle uppercase tracking-wider mb-2">
            Status breakdown
          </figcaption>
          <div className="space-y-2">
            {statusEntries.slice(0, 8).map(({ status, count }) => {
              const pct = totalStatus > 0 ? Math.round((count / totalStatus) * 100) : 0
              return (
                <div key={status} className="flex items-center gap-3 text-xs">
                  <div className="w-20 text-fg-muted text-right truncate" title={status}>{status}</div>
                  <div
                    className="flex-1 bg-surface-raised rounded-full h-2 overflow-hidden"
                    role="progressbar"
                    aria-valuenow={pct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${status}: ${count} (${pct}%)`}
                  >
                    <div className="h-2 rounded-full bg-fg/70" style={{ width: `${pct}%` }} />
                  </div>
                  <div className="w-10 text-fg-muted text-right tnum">{count}</div>
                </div>
              )
            })}
          </div>
        </figure>
      </div>
    </Card>
  )
}
