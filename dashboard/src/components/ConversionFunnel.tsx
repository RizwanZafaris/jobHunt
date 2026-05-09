'use client'

import {
 BarChart,
 Bar,
 XAxis,
 YAxis,
 Tooltip,
 Cell,
 ResponsiveContainer,
 Legend,
} from 'recharts'
import { type ConversionFunnelRow } from '@/lib/profile-api'

interface Props {
 funnel: ConversionFunnelRow[]
 warning?: string
}

export default function ConversionFunnel({ funnel, warning }: Props) {
 // Aggregate totals across all companies
 const totals = funnel.reduce(
 (acc, row) => {
 acc.resumes_built += row.resumes_built || 0
 acc.responses += row.responses || 0
 acc.interviews += row.interviews || 0
 acc.offers += row.offers || 0
 return acc
 },
 { resumes_built: 0, responses: 0, interviews: 0, offers: 0 }
 )

 const isEmpty = totals.resumes_built === 0

 // Per-company stacked-bar data — only companies with at least 1 build
 const perCompany = funnel
 .filter((r) => r.resumes_built > 0)
 .sort((a, b) => (b.interviews || 0) - (a.interviews || 0))
 .slice(0, 12) // top 12 by interviews to keep chart readable
 .map((r) => ({
 company: r.company_name,
 builds: r.resumes_built || 0,
 responses: r.responses || 0,
 interviews: r.interviews || 0,
 offers: r.offers || 0,
 }))

 return (
 <section className="bg-surface border border-border rounded-xl p-6">
 <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
 <div>
 <h2 className="text-base font-semibold text-fg">Conversion Funnel</h2>
 <p className="text-2xs text-fg-subtle mt-0.5">
 How resumes built recruiter responses interviews offers, per company
 </p>
 </div>
 <p className="text-2xs text-fg-subtle">
 Source: <code className="text-fg-muted">v_company_conversion_funnel</code>
 </p>
 </div>

 {/* Aggregate stats — the big-picture funnel */}
 <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
 <FunnelStat label="Resumes built" value={totals.resumes_built} color="blue" />
 <FunnelStat
 label="Responses"
 value={totals.responses}
 color="violet"
 rate={
 totals.resumes_built > 0
 ? Math.round((totals.responses / totals.resumes_built) * 100)
 : null
 }
 />
 <FunnelStat
 label="Interviews"
 value={totals.interviews}
 color="emerald"
 rate={
 totals.responses > 0
 ? Math.round((totals.interviews / totals.responses) * 100)
 : null
 }
 />
 <FunnelStat
 label="Offers"
 value={totals.offers}
 color="amber"
 rate={
 totals.interviews > 0
 ? Math.round((totals.offers / totals.interviews) * 100)
 : null
 }
 />
 </div>

 {warning && (
 <div className="mb-4 text-2xs text-warning bg-warning-bg/20 border border-warning-border/50 rounded-lg px-3 py-2">
 {warning}
 </div>
 )}

 {isEmpty ? (
 <div className="text-center py-10 border border-dashed border-border rounded-lg">
 <p className="text-sm text-fg-muted">No outcome data yet.</p>
 <p className="text-xs text-fg-subtle mt-1">
 Log outcomes from{' '}
 <span className="text-info">/jobs/&lt;id&gt;</span>{' '}
 (right column &mdash; Outcome) to populate this funnel.
 </p>
 </div>
 ) : (
 <ResponsiveContainer width="100%" height={Math.max(220, perCompany.length * 28 + 40)}>
 <BarChart
 data={perCompany}
 layout="vertical"
 barCategoryGap={6}
 margin={{ top: 10, right: 12, bottom: 10, left: 0 }}
 >
 <XAxis
 type="number"
 tick={{ fill: '#9ca3af', fontSize: 11 }}
 axisLine={false}
 tickLine={false}
 />
 <YAxis
 type="category"
 dataKey="company"
 tick={{ fill: '#9ca3af', fontSize: 11 }}
 axisLine={false}
 tickLine={false}
 width={120}
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
 <Legend
 wrapperStyle={{ fontSize: '11px', color: '#9ca3af' }}
 iconType="square"
 />
 <Bar dataKey="builds" name="Resumes" fill="#3b82f6" radius={[0, 4, 4, 0]} />
 <Bar dataKey="responses" name="Responses" fill="#8b5cf6" radius={[0, 4, 4, 0]} />
 <Bar dataKey="interviews" name="Interviews" fill="#10b981" radius={[0, 4, 4, 0]} />
 <Bar dataKey="offers" name="Offers" fill="#f59e0b" radius={[0, 4, 4, 0]} />
 </BarChart>
 </ResponsiveContainer>
 )}
 </section>
 )
}

const COLOR_CLS: Record<string, string> = {
 blue: 'bg-info-bg/30 border-info-border/60 text-info',
 violet: 'bg-info-bg/30 border-info-border/60 text-info',
 emerald: 'bg-success-bg/30 border-success-border/60 text-success',
 amber: 'bg-warning-bg/30 border-warning-border/60 text-warning',
}

function FunnelStat({
 label,
 value,
 color,
 rate,
}: {
 label: string
 value: number
 color: keyof typeof COLOR_CLS
 rate?: number | null
}) {
 return (
 <div className={`border rounded-lg px-3 py-2 ${COLOR_CLS[color]}`}>
 <div className="text-2xs uppercase tracking-wider opacity-80">{label}</div>
 <div className="flex items-baseline gap-2 mt-0.5">
 <div className="text-xl font-bold">{value}</div>
 {rate !== null && rate !== undefined && (
 <div className="text-2xs opacity-75">{rate}%</div>
 )}
 </div>
 </div>
 )
}
