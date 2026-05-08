'use client'

import Link from 'next/link'
import { useState } from 'react'
import { getScoreGrade, getScoreClass, getStatusClass } from '@/lib/api'

interface Job {
  id: number
  title: string
  company: string
  location: string
  match_score: number
  status: string
  url: string
  discovered_at: string
  archetype?: string | null
  legitimacy_tier?: string | null
}

interface Props {
  jobs: Job[]
}

export default function JobsTable({ jobs }: Props) {
  const [filter, setFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [minScore, setMinScore] = useState(0)

  const filtered = jobs.filter((j) => {
    const matchText = filter === '' ||
      j.title.toLowerCase().includes(filter.toLowerCase()) ||
      j.company.toLowerCase().includes(filter.toLowerCase())
    const matchStatus = statusFilter === 'all' || j.status === statusFilter
    const matchScore = j.match_score >= minScore
    return matchText && matchStatus && matchScore
  })

  const statuses = ['all', ...Array.from(new Set(jobs.map(j => j.status)))]

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800">
      <div className="px-6 py-4 border-b border-gray-800 flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
        <h2 className="text-base font-semibold text-white">
          Job Pipeline
          <span className="ml-2 text-xs text-gray-400 font-normal">({filtered.length} shown)</span>
        </h2>
        <div className="flex flex-wrap gap-2">
          {/* Search */}
          <input
            type="text"
            placeholder="Search..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200 placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500 w-36"
          />
          {/* Status filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            {statuses.map(s => (
              <option key={s} value={s}>{s === 'all' ? 'All statuses' : s}</option>
            ))}
          </select>
          {/* Min score */}
          <select
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500"
          >
            <option value={0}>All scores</option>
            <option value={40}>40+ match</option>
            <option value={60}>60+ match</option>
            <option value={75}>75+ match</option>
            <option value={85}>85+ match</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-gray-800">
              <th className="px-6 py-3 text-xs font-medium text-gray-400 uppercase tracking-wider">Role</th>
              <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase tracking-wider">Company</th>
              <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase tracking-wider">Location</th>
              <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase tracking-wider">Score</th>
              <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase tracking-wider">Status</th>
              <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase tracking-wider">Found</th>
              <th className="px-4 py-3 text-xs font-medium text-gray-400 uppercase tracking-wider"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-6 py-8 text-center text-gray-500 text-sm">
                  No jobs match your filters.
                </td>
              </tr>
            ) : (
              filtered.map((job) => (
                <tr key={job.id} className="hover:bg-gray-800/50 transition-colors">
                  <td className="px-6 py-3">
                    <Link href={`/jobs/${job.id}`} className="text-white font-medium text-sm hover:text-blue-400">
                      {job.title}
                    </Link>
                    <div className="flex gap-1 mt-0.5">
                      {job.archetype && (
                        <span className="text-[10px] bg-emerald-900/40 border border-emerald-800 text-emerald-300 px-1.5 py-0 rounded">
                          {job.archetype}
                        </span>
                      )}
                      {job.legitimacy_tier === 'Suspicious' && (
                        <span className="text-[10px] bg-red-900/40 border border-red-800 text-red-300 px-1.5 py-0 rounded">
                          ⚠️ ghost?
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-gray-300 text-sm">{job.company}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs">{job.location || '—'}</td>
                  <td className="px-4 py-3">
                    <span className={`score-badge ${getScoreClass(job.match_score)}`}>
                      {getScoreGrade(job.match_score)} {job.match_score}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`status-badge ${getStatusClass(job.status)}`}>
                      {job.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {job.discovered_at
                      ? new Date(job.discovered_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
                      : '—'}
                  </td>
                  <td className="px-4 py-3">
                    {job.url && (
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-blue-400 hover:text-blue-300 underline"
                      >
                        View →
                      </a>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
