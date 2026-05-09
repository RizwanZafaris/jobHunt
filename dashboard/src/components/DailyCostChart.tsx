'use client'

import { useMemo } from 'react'
import {
 AreaChart,
 Area,
 XAxis,
 YAxis,
 Tooltip,
 ResponsiveContainer,
 Legend,
 CartesianGrid,
} from 'recharts'
import { type DailyCostRow } from '@/lib/profile-api'

interface Props {
 rows: DailyCostRow[]
 days: number
 warning?: string
}

const PROVIDER_COLOR: Record<string, string> = {
 anthropic: '#d97706', // amber-600 — Claude
 google: '#3b82f6', // blue-500 — Gemini
 openai: '#10b981', // emerald-500
 deepseek: '#8b5cf6', // violet-500
 moonshot: '#ec4899', // pink-500 — Kimi
}

export default function DailyCostChart({ rows, days, warning }: Props) {
 // Pivot rows from (day, provider, model) into (day, providerA, providerB, ...)
 const { chartData, providers } = useMemo(() => {
 const byDay: Record<string, Record<string, number>> = {}
 const providerSet = new Set<string>()
 for (const r of rows) {
 providerSet.add(r.provider)
 if (!byDay[r.day]) byDay[r.day] = {}
 byDay[r.day][r.provider] = (byDay[r.day][r.provider] || 0) + Number(r.total_cost_usd || 0)
 }
 const sortedDays = Object.keys(byDay).sort()
 return {
 chartData: sortedDays.map((day) => ({
 day,
 ...byDay[day],
 })),
 providers: Array.from(providerSet).sort(),
 }
 }, [rows])

 const isEmpty = chartData.length === 0

 return (
 <section className="bg-surface border border-border rounded-xl p-6">
 <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
 <div>
 <h2 className="text-base font-semibold text-fg">Daily Cost</h2>
 <p className="text-2xs text-fg-subtle mt-0.5">
 Stacked by provider · last {days} days
 </p>
 </div>
 <p className="text-2xs text-fg-subtle">
 source: <code className="text-fg-muted">v_daily_llm_cost</code>
 </p>
 </div>

 {warning && (
 <div className="mb-4 text-2xs text-warning bg-warning-bg/20 border border-warning-border/50 rounded-lg px-3 py-2">
 {warning}
 </div>
 )}

 {isEmpty ? (
 <div className="text-center py-10 border border-dashed border-border rounded-lg">
 <p className="text-sm text-fg-muted">No LLM calls logged yet.</p>
 <p className="text-xs text-fg-subtle mt-1">
 Cost rows appear here once <code className="text-fg-muted">USE_G2_GRAPH=true</code>{' '}
 is set on Railway and a resume build runs.
 </p>
 </div>
 ) : (
 <ResponsiveContainer width="100%" height={240}>
 <AreaChart data={chartData} margin={{ top: 5, right: 12, bottom: 0, left: 0 }}>
 <defs>
 {providers.map((p) => (
 <linearGradient key={p} id={`g-${p}`} x1="0" y1="0" x2="0" y2="1">
 <stop offset="5%" stopColor={PROVIDER_COLOR[p] || '#6b7280'} stopOpacity={0.7} />
 <stop offset="95%" stopColor={PROVIDER_COLOR[p] || '#6b7280'} stopOpacity={0.1} />
 </linearGradient>
 ))}
 </defs>
 <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
 <XAxis
 dataKey="day"
 tick={{ fill: '#9ca3af', fontSize: 11 }}
 axisLine={false}
 tickLine={false}
 tickFormatter={(d: string) => d.slice(5)} // 'MM-DD'
 />
 <YAxis
 tick={{ fill: '#9ca3af', fontSize: 11 }}
 axisLine={false}
 tickLine={false}
 width={50}
 tickFormatter={(v: number) => `$${v.toFixed(2)}`}
 />
 <Tooltip
 contentStyle={{
 background: '#1f2937',
 border: '1px solid #374151',
 borderRadius: '8px',
 fontSize: '12px',
 }}
 labelStyle={{ color: '#e5e7eb' }}
 formatter={(v: any, name: string) => [`$${Number(v).toFixed(4)}`, name]}
 />
 <Legend
 wrapperStyle={{ fontSize: '11px', color: '#9ca3af' }}
 iconType="square"
 />
 {providers.map((p) => (
 <Area
 key={p}
 type="monotone"
 dataKey={p}
 stackId="1"
 stroke={PROVIDER_COLOR[p] || '#6b7280'}
 fill={`url(#g-${p})`}
 name={p}
 />
 ))}
 </AreaChart>
 </ResponsiveContainer>
 )}
 </section>
 )
}
