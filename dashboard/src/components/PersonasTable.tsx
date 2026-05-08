'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { type PersonaRow } from '@/lib/profile-api'
import RegeneratePersonaButton from './RegeneratePersonaButton'

interface Props {
  personas: PersonaRow[]
}

const QUALITY_COLOR: Record<string, string> = {
  high: 'bg-emerald-900/40 border-emerald-800 text-emerald-300',
  medium: 'bg-amber-900/40 border-amber-800 text-amber-300',
  low: 'bg-red-900/30 border-red-900/60 text-red-400',
  unknown: 'bg-gray-800 border-gray-700 text-gray-400',
}

type SortKey = 'name' | 'version' | 'examples' | 'synthesized' | 'quality'

const QUALITY_ORDER: Record<string, number> = {
  high: 3,
  medium: 2,
  low: 1,
  unknown: 0,
}

export default function PersonasTable({ personas }: Props) {
  const [filter, setFilter] = useState('')
  const [filterQuality, setFilterQuality] = useState<string>('all')
  const [sortKey, setSortKey] = useState<SortKey>('synthesized')
  const [sortDesc, setSortDesc] = useState(true)

  const filtered = useMemo(() => {
    let arr = personas
    if (filterQuality !== 'all') {
      arr = arr.filter((p) => (p.metadata?.persona_quality || 'unknown') === filterQuality)
    }
    if (filter) {
      const f = filter.toLowerCase()
      arr = arr.filter((p) => p.company_name.toLowerCase().includes(f))
    }
    arr = [...arr].sort((a, b) => {
      let cmp = 0
      switch (sortKey) {
        case 'name':
          cmp = a.company_name.localeCompare(b.company_name)
          break
        case 'version':
          cmp = (a.persona_version || 0) - (b.persona_version || 0)
          break
        case 'examples':
          cmp = (a.n_examples_used || 0) - (b.n_examples_used || 0)
          break
        case 'synthesized':
          cmp = new Date(a.last_synthesized_at).getTime() - new Date(b.last_synthesized_at).getTime()
          break
        case 'quality':
          cmp =
            (QUALITY_ORDER[a.metadata?.persona_quality || 'unknown'] || 0) -
            (QUALITY_ORDER[b.metadata?.persona_quality || 'unknown'] || 0)
          break
      }
      return sortDesc ? -cmp : cmp
    })
    return arr
  }, [personas, filter, filterQuality, sortKey, sortDesc])

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDesc(!sortDesc)
    } else {
      setSortKey(key)
      setSortDesc(true)
    }
  }

  const sortIndicator = (key: SortKey) =>
    sortKey === key ? <span className="ml-1 text-gray-500">{sortDesc ? '↓' : '↑'}</span> : null

  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      {/* Filter bar */}
      <div className="px-4 py-3 border-b border-gray-800 flex flex-wrap items-center gap-2">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search company..."
          className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200 w-48"
        />
        <select
          value={filterQuality}
          onChange={(e) => setFilterQuality(e.target.value)}
          className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200"
        >
          <option value="all">All quality tiers</option>
          <option value="high">High quality only</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="unknown">Unknown</option>
        </select>
        <span className="text-xs text-gray-500 ml-auto">
          {filtered.length} of {personas.length}
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-gray-950/50 text-gray-400 uppercase text-[10px]">
            <tr>
              <Th onClick={() => toggleSort('name')}>
                Company {sortIndicator('name')}
              </Th>
              <Th onClick={() => toggleSort('quality')}>
                Quality {sortIndicator('quality')}
              </Th>
              <Th onClick={() => toggleSort('version')}>
                v {sortIndicator('version')}
              </Th>
              <Th onClick={() => toggleSort('examples')}>
                Examples {sortIndicator('examples')}
              </Th>
              <Th onClick={() => toggleSort('synthesized')}>
                Last synth {sortIndicator('synthesized')}
              </Th>
              <Th>ATS keywords</Th>
              <Th />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/60">
            {filtered.map((p) => {
              const q = p.metadata?.persona_quality || 'unknown'
              const required = p.ats_keyword_bank?.required || []
              const boost = p.ats_keyword_bank?.boost || []
              const totalKw = required.length + boost.length
              const synthDate = new Date(p.last_synthesized_at)
              return (
                <tr key={p.company_name} className="hover:bg-gray-800/30 transition-colors">
                  <td className="px-3 py-2">
                    <Link
                      href={`/personas/${encodeURIComponent(p.company_name)}`}
                      className="text-blue-400 hover:text-blue-300 font-medium"
                    >
                      {p.company_name}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`text-[10px] border px-1.5 py-0.5 rounded uppercase ${QUALITY_COLOR[q]}`}
                    >
                      {q}
                    </span>
                    {p.metadata?.unknown_sections !== undefined &&
                      p.metadata.unknown_sections > 0 && (
                        <span
                          className="ml-1 text-[10px] text-gray-500"
                          title={`${p.metadata.unknown_sections} of 5 recruitment-intel sections were 'Unknown — insufficient data'`}
                        >
                          {p.metadata.unknown_sections}/5 ?
                        </span>
                      )}
                  </td>
                  <td className="px-3 py-2 text-gray-300">{p.persona_version}</td>
                  <td className="px-3 py-2 text-gray-300">
                    {p.n_examples_used || (
                      <span className="text-gray-600">0</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-400" title={synthDate.toISOString()}>
                    {relativeDate(synthDate)}
                  </td>
                  <td className="px-3 py-2 text-gray-400">
                    {totalKw > 0 ? (
                      <>
                        {required.length > 0 && (
                          <span className="text-emerald-400" title="required">
                            {required.length}
                          </span>
                        )}
                        {required.length > 0 && boost.length > 0 && (
                          <span className="text-gray-700"> · </span>
                        )}
                        {boost.length > 0 && (
                          <span className="text-blue-400" title="boost">
                            +{boost.length}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-gray-600 italic">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <RegeneratePersonaButton companyName={p.company_name} />
                  </td>
                </tr>
              )
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-8 text-center text-gray-500 italic">
                  No personas match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function Th({
  children,
  onClick,
}: {
  children?: React.ReactNode
  onClick?: () => void
}) {
  return (
    <th
      onClick={onClick}
      className={`px-3 py-2 text-left font-semibold tracking-wider ${
        onClick ? 'cursor-pointer select-none hover:text-gray-200' : ''
      }`}
    >
      {children}
    </th>
  )
}

function relativeDate(d: Date): string {
  const now = Date.now()
  const diffH = Math.floor((now - d.getTime()) / (1000 * 60 * 60))
  if (diffH < 1) return 'just now'
  if (diffH < 24) return `${diffH}h ago`
  const diffD = Math.floor(diffH / 24)
  if (diffD < 7) return `${diffD}d ago`
  const diffW = Math.floor(diffD / 7)
  if (diffW < 4) return `${diffW}w ago`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}
