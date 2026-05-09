'use client'

import { useMemo, useState } from 'react'
import type { KeywordRow } from '@/lib/profile-api'

interface Props {
  keywords: KeywordRow[]
  categoryColors: Record<string, string>
}

export default function KeywordExplorer({ keywords, categoryColors }: Props) {
  const [filter, setFilter] = useState('')
  const [category, setCategory] = useState<string>('all')
  const [minStrength, setMinStrength] = useState(0)

  const cats = useMemo(
    () => Array.from(new Set(keywords.map((k) => k.category))).sort(),
    [keywords]
  )

  const filtered = useMemo(
    () =>
      keywords.filter((k) => {
        const matchText = filter === '' || k.keyword.toLowerCase().includes(filter.toLowerCase())
        const matchCat = category === 'all' || k.category === category
        const matchStrength = k.ats_strength >= minStrength
        return matchText && matchCat && matchStrength
      }),
    [keywords, filter, category, minStrength]
  )

  return (
    <section className="bg-surface border border-border rounded-xl">
      <div className="px-5 py-3 border-b border-border flex flex-col sm:flex-row gap-2 items-start sm:items-center justify-between">
        <h3 className="text-sm font-semibold text-fg">
          All Keywords <span className="text-fg-subtle font-normal text-xs">({filtered.length} shown)</span>
        </h3>
        <div className="flex flex-wrap gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search keyword..."
            className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg placeholder:text-fg-subtle focus:outline-none focus:ring-1 focus:ring-blue-500 w-48"
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg"
          >
            <option value="all">All categories</option>
            {cats.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select
            value={minStrength}
            onChange={(e) => setMinStrength(Number(e.target.value))}
            className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg"
          >
            <option value={0}>Any strength</option>
            <option value={20}>20+</option>
            <option value={40}>40+</option>
            <option value={60}>60+</option>
            <option value={80}>80+</option>
          </select>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-fg-muted uppercase tracking-wider">
            <tr className="border-b border-border">
              <th className="px-5 py-2.5">Keyword</th>
              <th className="px-3 py-2.5">Category</th>
              <th className="px-3 py-2.5 text-right">Strength</th>
              <th className="px-3 py-2.5 text-right">Files</th>
              <th className="px-3 py-2.5 text-right">Coverage</th>
              <th className="px-3 py-2.5 text-right">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-5 py-8 text-center text-fg-subtle">
                  No keywords match your filters.
                </td>
              </tr>
            ) : (
              filtered.slice(0, 200).map((k) => {
                const color = categoryColors[k.category] || 'bg-surface-raised text-fg-muted border-border-strong'
                const barWidth = Math.min(100, k.ats_strength)
                return (
                  <tr key={k.id} className="hover:bg-surface-raised/40">
                    <td className="px-5 py-2 text-fg font-medium">{k.keyword}</td>
                    <td className="px-3 py-2">
                      <span className={`inline-block border px-2 py-0.5 rounded text-2xs ${color}`}>
                        {k.category}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center justify-end gap-2">
                        <div className="w-16 h-1.5 bg-surface-raised rounded-full overflow-hidden">
                          <div
                            className="h-1.5 bg-info-bg rounded-full"
                            style={{ width: `${barWidth}%` }}
                          />
                        </div>
                        <span className="text-fg-muted tabular-nums w-10 text-right">{k.ats_strength}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right text-fg-muted tabular-nums">{k.files_count}</td>
                    <td className="px-3 py-2 text-right text-fg-muted tabular-nums">{k.coverage_pct}%</td>
                    <td className="px-3 py-2 text-right text-fg-muted tabular-nums">{k.total_occurrences}</td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
        {filtered.length > 200 && (
          <div className="px-5 py-3 border-t border-border text-xs text-fg-subtle text-center">
            Showing 200 of {filtered.length} matches — refine filters to see more.
          </div>
        )}
      </div>
    </section>
  )
}
