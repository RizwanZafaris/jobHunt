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
    <section className="bg-gray-900 border border-gray-800 rounded-xl">
      <div className="px-5 py-3 border-b border-gray-800 flex flex-col sm:flex-row gap-2 items-start sm:items-center justify-between">
        <h3 className="text-sm font-semibold text-white">
          Documents <span className="text-gray-500 font-normal text-xs">({filtered.length} shown)</span>
        </h3>
        <div className="flex flex-wrap gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Search filename..."
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200 w-48"
          />
          <select
            value={classFilter}
            onChange={(e) => setClassFilter(e.target.value)}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200"
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
          <thead className="text-left text-gray-400 uppercase tracking-wider">
            <tr className="border-b border-gray-800">
              <th className="px-5 py-2.5">File</th>
              <th className="px-3 py-2.5">Class</th>
              <th className="px-3 py-2.5 text-right">Chars</th>
              <th className="px-3 py-2.5 text-right">Size</th>
              <th className="px-3 py-2.5">Hash</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-5 py-8 text-center text-gray-500">
                  No documents match.
                </td>
              </tr>
            ) : (
              filtered.slice(0, 250).map((d) => (
                <tr key={d.id} className="hover:bg-gray-800/40">
                  <td className="px-5 py-2 text-white" title={d.file_name}>
                    {d.file_name.length > 60 ? d.file_name.slice(0, 60) + '…' : d.file_name}
                  </td>
                  <td className="px-3 py-2 text-gray-300">{d.document_class}</td>
                  <td className="px-3 py-2 text-right text-gray-400 tabular-nums">
                    {d.char_count.toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-400 tabular-nums">
                    {(d.file_size / 1024).toFixed(1)} KB
                  </td>
                  <td className="px-3 py-2 text-gray-500 font-mono">{d.file_hash.slice(0, 8)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        {filtered.length > 250 && (
          <div className="px-5 py-3 border-t border-gray-800 text-xs text-gray-500 text-center">
            Showing 250 of {filtered.length} — refine filters to see more.
          </div>
        )}
      </div>
    </section>
  )
}
