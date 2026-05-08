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
    <section className="bg-gray-900 border border-gray-800 rounded-xl">
      <div className="px-5 py-3 border-b border-gray-800 flex flex-col sm:flex-row gap-2 items-start sm:items-center justify-between">
        <h3 className="text-sm font-semibold text-white">
          All Keywords <span className="text-gray-500 font-normal text-xs">({filtered.length} shown)</span>
        </h3>
        <div className="flex flex-wrap gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search keyword..."
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200 placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500 w-48"
          />
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200"
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
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200"
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
          <thead className="text-left text-gray-400 uppercase tracking-wider">
            <tr className="border-b border-gray-800">
              <th className="px-5 py-2.5">Keyword</th>
              <th className="px-3 py-2.5">Category</th>
              <th className="px-3 py-2.5 text-right">Strength</th>
              <th className="px-3 py-2.5 text-right">Files</th>
              <th className="px-3 py-2.5 text-right">Coverage</th>
              <th className="px-3 py-2.5 text-right">Total</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-5 py-8 text-center text-gray-500">
                  No keywords match your filters.
                </td>
              </tr>
            ) : (
              filtered.slice(0, 200).map((k) => {
                const color = categoryColors[k.category] || 'bg-gray-800 text-gray-300 border-gray-700'
                const barWidth = Math.min(100, k.ats_strength)
                return (
                  <tr key={k.id} className="hover:bg-gray-800/40">
                    <td className="px-5 py-2 text-white font-medium">{k.keyword}</td>
                    <td className="px-3 py-2">
                      <span className={`inline-block border px-2 py-0.5 rounded text-[10px] ${color}`}>
                        {k.category}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex items-center justify-end gap-2">
                        <div className="w-16 h-1.5 bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className="h-1.5 bg-blue-500 rounded-full"
                            style={{ width: `${barWidth}%` }}
                          />
                        </div>
                        <span className="text-gray-300 tabular-nums w-10 text-right">{k.ats_strength}</span>
                      </div>
                    </td>
                    <td className="px-3 py-2 text-right text-gray-400 tabular-nums">{k.files_count}</td>
                    <td className="px-3 py-2 text-right text-gray-400 tabular-nums">{k.coverage_pct}%</td>
                    <td className="px-3 py-2 text-right text-gray-400 tabular-nums">{k.total_occurrences}</td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
        {filtered.length > 200 && (
          <div className="px-5 py-3 border-t border-gray-800 text-xs text-gray-500 text-center">
            Showing 200 of {filtered.length} matches — refine filters to see more.
          </div>
        )}
      </div>
    </section>
  )
}
