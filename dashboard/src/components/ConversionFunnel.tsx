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
    <section className="bg-gray-900 border border-gray-800 rounded-xl p-6">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h2 className="text-base font-semibold text-white">Conversion Funnel</h2>
          <p className="text-[11px] text-gray-500 mt-0.5">
            How resumes built → recruiter responses → interviews → offers, per company
          </p>
        </div>
        <p className="text-[11px] text-gray-500">
          Source: <code className="text-gray-400">v_company_conversion_funnel</code>
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
        <div className="mb-4 text-[11px] text-amber-400 bg-amber-900/20 border border-amber-900/50 rounded-lg px-3 py-2">
          ⚠ {warning}
        </div>
      )}

      {isEmpty ? (
        <div className="text-center py-10 border border-dashed border-gray-800 rounded-lg">
          <p className="text-sm text-gray-400">No outcome data yet.</p>
          <p className="text-xs text-gray-500 mt-1">
            Log outcomes from{' '}
            <span className="text-blue-400">/jobs/&lt;id&gt;</span>{' '}
            (right column → "📊 Outcome") to populate this funnel.
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
  blue: 'bg-blue-900/30 border-blue-800/60 text-blue-300',
  violet: 'bg-violet-900/30 border-violet-800/60 text-violet-300',
  emerald: 'bg-emerald-900/30 border-emerald-800/60 text-emerald-300',
  amber: 'bg-amber-900/30 border-amber-800/60 text-amber-300',
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
      <div className="text-[10px] uppercase tracking-wider opacity-80">{label}</div>
      <div className="flex items-baseline gap-2 mt-0.5">
        <div className="text-xl font-bold">{value}</div>
        {rate !== null && rate !== undefined && (
          <div className="text-[10px] opacity-75">{rate}%</div>
        )}
      </div>
    </div>
  )
}
