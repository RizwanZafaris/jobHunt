'use client'

import { useMemo, useState } from 'react'
import { type CostByAgentRow } from '@/lib/profile-api'

interface Props {
  agents: CostByAgentRow[]
  days: number
}

type SortKey = 'cost' | 'calls' | 'latency' | 'name'

export default function CostByAgentTable({ agents, days }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('cost')
  const [filter, setFilter] = useState('')

  const filtered = useMemo(() => {
    let arr = agents
    if (filter) {
      const f = filter.toLowerCase()
      arr = arr.filter((a) => a.agent_name.toLowerCase().includes(f))
    }
    arr = [...arr].sort((a, b) => {
      switch (sortKey) {
        case 'cost':
          return b.cost_usd - a.cost_usd
        case 'calls':
          return b.calls - a.calls
        case 'latency':
          return b.avg_latency_ms - a.avg_latency_ms
        case 'name':
          return a.agent_name.localeCompare(b.agent_name)
      }
    })
    return arr
  }, [agents, filter, sortKey])

  const totalCost = useMemo(
    () => agents.reduce((sum, a) => sum + a.cost_usd, 0),
    [agents]
  )
  const isEmpty = agents.length === 0

  return (
    <section className="bg-surface border border-border rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2 justify-between">
        <div>
          <h2 className="text-base font-semibold text-fg">Cost by Agent</h2>
          <p className="text-2xs text-fg-subtle mt-0.5">
            last {days} days · which G2 nodes burn the most
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter agent..."
            className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg w-48"
          />
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="text-xs bg-surface-raised border border-border-strong rounded-lg px-2 py-1.5 text-fg"
          >
            <option value="cost">Sort: cost</option>
            <option value="calls">Sort: calls</option>
            <option value="latency">Sort: latency</option>
            <option value="name">Sort: name</option>
          </select>
        </div>
      </div>

      {isEmpty ? (
        <div className="text-center py-10 text-sm text-fg-subtle italic">
          No agent calls yet — check back after a G2 build runs.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="bg-bg/50 text-fg-muted uppercase text-2xs">
              <tr>
                <th className="px-3 py-2 text-left font-semibold tracking-wider">Agent</th>
                <th className="px-3 py-2 text-left font-semibold tracking-wider">Provider/model</th>
                <th className="px-3 py-2 text-right font-semibold tracking-wider">Cost</th>
                <th className="px-3 py-2 text-right font-semibold tracking-wider">Calls</th>
                <th className="px-3 py-2 text-right font-semibold tracking-wider">In/Out</th>
                <th className="px-3 py-2 text-right font-semibold tracking-wider">Avg latency</th>
                <th className="px-3 py-2 w-24"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {filtered.map((a) => {
                const pct = totalCost > 0 ? (a.cost_usd / totalCost) * 100 : 0
                return (
                  <tr key={a.agent_name} className="hover:bg-surface-raised/30 transition-colors">
                    <td className="px-3 py-2">
                      <code className="text-info font-mono text-2xs">{a.agent_name}</code>
                    </td>
                    <td className="px-3 py-2 text-fg-muted">
                      <div className="flex flex-wrap gap-1">
                        {a.providers.map((p) => (
                          <span
                            key={p}
                            className="text-2xs bg-surface-raised border border-border-strong px-1.5 rounded"
                          >
                            {p}
                          </span>
                        ))}
                      </div>
                      {a.models.length > 0 && (
                        <div className="text-2xs text-fg-subtle mt-0.5 truncate max-w-[18rem]">
                          {a.models.join(', ')}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-right">
                      <span className="text-fg font-bold">${a.cost_usd.toFixed(3)}</span>
                      <span className="ml-1 text-2xs text-fg-subtle">
                        {pct.toFixed(0)}%
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right text-fg-muted">{a.calls}</td>
                    <td className="px-3 py-2 text-right text-fg-muted">
                      {(a.input_tokens / 1000).toFixed(1)}k / {(a.output_tokens / 1000).toFixed(1)}k
                    </td>
                    <td className="px-3 py-2 text-right text-fg-muted">
                      {(a.avg_latency_ms / 1000).toFixed(1)}s
                    </td>
                    <td className="px-3 py-2">
                      <div className="bg-surface-raised rounded-full h-1.5 overflow-hidden">
                        <div
                          className="h-1.5 rounded-full bg-info-bg"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                    </td>
                  </tr>
                )
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={7} className="px-3 py-6 text-center text-fg-subtle italic">
                    No agents match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
