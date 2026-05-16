'use client'

import { useMemo, useState } from 'react'
import type { SourceDocument } from '@/lib/profile-api'

interface Props {
  documents: SourceDocument[]
}

export default function SourcesTable({ documents }: Props) {
  const [filter, setFilter] = useState('')
  const [classFilter, setClassFilter] = useState('all')

  const classes = useMemo(
    () => Array.from(new Set(documents.map((d) => d.document_class))).sort(),
    [documents]
  )

  const filtered = useMemo(
    () =>
      documents.filter((d) => {
        const matchText = filter === '' || d.file_name.toLowerCase().includes(filter.toLowerCase())
        const matchClass = classFilter === 'all' || d.document_class === classFilter
        return matchText && matchClass
      }),
    [documents, filter, classFilter]
  )

  return (
    <section className="bg-surface border border-border rounded-xl">
      <div className="px-5 py-3 border-b border-border flex flex-col sm:flex-row gap-2 items-start sm:items-center justify-between">
        <h3 className="text-sm font-semibold text-fg">
          Documents <span className="text-fg-subtle font-normal text-xs">({filtered.length} shown)</span>
        </h3>
        <div className="flex flex-wrap gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search filename..."
            aria-label="Filter source documents by filename"
            className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg w-48"
          />
          <select
            value={classFilter}
            onChange={(e) => setClassFilter(e.target.value)}
            aria-label="Filter source documents by document class"
            className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg"
          >
            <option value="all">All classes</option>
            {classes.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-left text-fg-muted uppercase tracking-wider">
            <tr className="border-b border-border">
              <th className="px-5 py-2.5">File</th>
              <th className="px-3 py-2.5">Class</th>
              <th className="px-3 py-2.5 text-right">Chars</th>
              <th className="px-3 py-2.5 text-right">Size</th>
              <th className="px-3 py-2.5">Hash</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center text-fg-subtle">
                  No documents match.
                </td>
              </tr>
            ) : (
              filtered.slice(0, 250).map((d) => (
                <tr key={d.id} className="hover:bg-surface-raised/40">
                  <td className="px-5 py-2 text-fg" title={d.file_name}>
                    {d.file_name.length > 60 ? d.file_name.slice(0, 60) + '…' : d.file_name}
                  </td>
                  <td className="px-3 py-2 text-fg-muted">{d.document_class}</td>
                  <td className="px-3 py-2 text-right text-fg-muted tabular-nums">
                    {d.char_count.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right text-fg-muted tabular-nums">
                    {(d.file_size / 1024).toFixed(1)} KB
                  </td>
                  <td className="px-3 py-2 text-fg-subtle font-mono">{d.file_hash.slice(0, 8)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {filtered.length > 250 && (
          <div className="px-5 py-3 border-t border-border text-xs text-fg-subtle text-center">
            Showing 250 of {filtered.length} — refine filters to see more.
          </div>
        )}
      </div>
    </section>
  )
}
