'use client'

import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'
import { type CostByProviderRow } from '@/lib/profile-api'

interface Props {
  providers: CostByProviderRow[]
  days: number
}

const PROVIDER_COLOR: Record<string, string> = {
  anthropic: '#d97706',
  google: '#3b82f6',
  openai: '#10b981',
  deepseek: '#8b5cf6',
  moonshot: '#ec4899',
}

export default function CostByProviderChart({ providers, days }: Props) {
  const data = providers.filter((p) => p.cost_usd > 0)
  const total = data.reduce((sum, p) => sum + p.cost_usd, 0)
  const isEmpty = total === 0

  return (
    <section className="bg-surface border border-border rounded-xl p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-base font-semibold text-fg">Cost by Provider</h2>
          <p className="text-2xs text-fg-subtle mt-0.5">last {days} days</p>
        </div>
        {!isEmpty && (
          <span className="text-xs text-fg-muted">
            <span className="text-fg font-bold">${total.toFixed(2)}</span> total
          </span>
        )}
      </div>

      {isEmpty ? (
        <div className="text-center py-12 border border-dashed border-border rounded-lg">
          <p className="text-sm text-fg-subtle">No provider data yet</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-5 gap-4 items-center">
          {/* Donut */}
          <div className="sm:col-span-2 flex justify-center">
            <ResponsiveContainer width="100%" height={180}>
              <PieChart>
                <Pie
                  data={data}
                  dataKey="cost_usd"
                  nameKey="provider"
                  cx="50%"
                  cy="50%"
                  innerRadius={45}
                  outerRadius={75}
                  paddingAngle={2}
                  stroke="none"
                >
                  {data.map((entry) => (
                    <Cell
                      key={entry.provider}
                      fill={PROVIDER_COLOR[entry.provider] || '#6b7280'}
                    />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: '#1f2937',
                    border: '1px solid #374151',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                  formatter={(v: any) => [`$${Number(v).toFixed(4)}`, 'cost']}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>

          {/* Per-provider rows */}
          <div className="sm:col-span-3 space-y-2">
            {data.map((p) => {
              const pct = total > 0 ? (p.cost_usd / total) * 100 : 0
              const color = PROVIDER_COLOR[p.provider] || '#6b7280'
              return (
                <div key={p.provider} className="text-xs">
                  <div className="flex items-center justify-between mb-1">
                    <span className="flex items-center gap-2 text-fg">
                      <span
                        className="inline-block w-2 h-2 rounded-full"
                        style={{ background: color }}
                      />
                      <code className="font-mono">{p.provider}</code>
                    </span>
                    <span className="text-fg-muted">
                      <span className="text-fg font-bold">${p.cost_usd.toFixed(2)}</span>
                      <span className="text-fg-subtle"> ({pct.toFixed(0)}%)</span>
                    </span>
                  </div>
                  <div className="bg-surface-raised rounded-full h-1.5 overflow-hidden">
                    <div
                      className="h-1.5 rounded-full"
                      style={{ width: `${pct}%`, backgroundColor: color }}
                    />
                  </div>
                  <div className="flex items-center justify-between mt-0.5 text-2xs text-fg-subtle">
                    <span>{p.calls} calls · ~{Math.round(p.avg_latency_ms / 100) / 10}s avg</span>
                    <span>
                      {(p.input_tokens / 1000).toFixed(1)}k in /{' '}
                      {(p.output_tokens / 1000).toFixed(1)}k out
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
