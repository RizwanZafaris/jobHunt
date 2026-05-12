'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import { type PersonaRow } from '@/lib/profile-api'
import RegeneratePersonaButton from './RegeneratePersonaButton'
import DeepResearchPersonaButton from './DeepResearchPersonaButton'

interface Props {
  personas: PersonaRow[]
}

const QUALITY_COLOR: Record<string, string> = {
  high: 'bg-success-bg/40 border-success-border text-success',
  medium: 'bg-warning-bg/40 border-warning-border text-warning',
  low: 'bg-danger-bg/30 border-danger-border/60 text-danger',
  unknown: 'bg-surface-raised border-border-strong text-fg-muted',
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
  // BUG-017: relative-time strings ("2h ago") depend on Date.now() so they
  // drift between SSR and CSR. Render the deterministic UTC date on the
  // first pass, upgrade to relative time after the client mounts.
  const [mounted, setMounted] = useState(false)
  useEffect(() => {
    setMounted(true)
  }, [])

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
    sortKey === key ? <span className="ml-1 text-fg-subtle">{sortDesc ? '↓' : '↑'}</span> : null

  return (
    <section className="bg-surface border border-border rounded-xl overflow-hidden">
      {/* Filter bar */}
      <div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search company..."
          className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg w-48"
        />
        <select
          value={filterQuality}
          onChange={(e) => setFilterQuality(e.target.value)}
          className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg"
        >
          <option value="all">All quality tiers</option>
          <option value="high">High quality only</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="unknown">Unknown</option>
        </select>
        <span className="text-xs text-fg-subtle ml-auto">
          {filtered.length} of {personas.length}
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-bg/50 text-fg-muted uppercase text-2xs">
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
          <tbody className="divide-y divide-border/60">
            {filtered.map((p) => {
              const q = p.metadata?.persona_quality || 'unknown'
              const required = p.ats_keyword_bank?.required || []
              const boost = p.ats_keyword_bank?.boost || []
              const totalKw = required.length + boost.length
              const synthDate = new Date(p.last_synthesized_at)
              return (
                <tr key={p.company_name} className="hover:bg-surface-raised/30 transition-colors">
                  <td className="px-3 py-2">
                    <Link
                      href={`/personas/${encodeURIComponent(p.company_name)}`}
                      className="text-info hover:text-info font-medium"
                    >
                      {p.company_name}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`text-2xs border px-1.5 py-0.5 rounded uppercase ${QUALITY_COLOR[q]}`}
                    >
                      {q}
                    </span>
                    {p.metadata?.unknown_sections !== undefined &&
                      p.metadata.unknown_sections > 0 && (
                        <span
                          className="ml-1 text-2xs text-fg-subtle"
                          title={`${p.metadata.unknown_sections} of 5 recruitment-intel sections were 'Unknown — insufficient data'`}
                        >
                          {p.metadata.unknown_sections}/5 ?
                        </span>
                      )}
                  </td>
                  <td className="px-3 py-2 text-fg-muted">{p.persona_version}</td>
                  <td className="px-3 py-2 text-fg-muted">
                    {p.n_examples_used || (
                      <span className="text-fg-subtle">0</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-fg-muted" title={synthDate.toISOString()}>
                    {mounted ? relativeDate(synthDate) : absoluteDate(synthDate)}
                  </td>
                  <td className="px-3 py-2 text-fg-muted">
                    {totalKw > 0 ? (
                      <>
                        {required.length > 0 && (
                          <span className="text-success" title="required">
                            {required.length}
                          </span>
                        )}
                        {required.length > 0 && boost.length > 0 && (
                          <span className="text-fg-subtle"> · </span>
                        )}
                        {boost.length > 0 && (
                          <span className="text-info" title="boost">
                            +{boost.length}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-fg-subtle italic">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right">
                    <div className="inline-flex items-center gap-1.5 flex-wrap justify-end">
                      <DeepResearchPersonaButton companyName={p.company_name} size="xs" />
                      <RegeneratePersonaButton companyName={p.company_name} />
                    </div>
                  </td>
                </tr>
              )
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-8 text-center text-fg-subtle italic">
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
        onClick ? 'cursor-pointer select-none hover:text-fg' : ''
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
  return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })
}

// SSR-safe deterministic date — pinned locale + timeZone so server and
// client produce the same string before the relative-time upgrade.
function absoluteDate(d: Date): string {
  return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })
}
