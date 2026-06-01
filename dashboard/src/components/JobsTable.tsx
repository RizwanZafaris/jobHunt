'use client'

import Link from 'next/link'
import { useId, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getScoreGrade, getScoreClass, getStatusClass } from '@/lib/api'
import { Card } from '@/components/ui/Card'
import { TextInput, Select } from '@/components/ui/Field'
import { Pill } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'

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
  const router = useRouter()
  const [filter, setFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [minScore, setMinScore] = useState(0)
  const captionId = useId()

  // useMemo: avoid rebuilding the array on every keystroke when only one filter changed.
  const filtered = useMemo(() => {
    const f = filter.toLowerCase()
    return jobs.filter((j) => {
      const matchText = f === '' || j.title.toLowerCase().includes(f) || j.company.toLowerCase().includes(f)
      const matchStatus = statusFilter === 'all' || j.status === statusFilter
      const matchScore = j.match_score >= minScore
      return matchText && matchStatus && matchScore
    })
  }, [jobs, filter, statusFilter, minScore])

  const statuses = useMemo(
    () => ['all', ...Array.from(new Set(jobs.map((j) => j.status)))],
    [jobs],
  )

  return (
    <Card padding="none">
      <div className="px-4 sm:px-5 py-3 border-b border-border flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
        <h2 className="text-sm font-semibold text-fg">
          Job pipeline
          <span className="ml-2 text-2xs text-fg-subtle font-normal tnum">({filtered.length} shown)</span>
        </h2>
        <div className="flex flex-wrap gap-2 items-end w-full sm:w-auto">
          <TextInput
            label="Search jobs"
            srLabel
            type="search"
            placeholder="Search…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-40"
          />
          <Select
            label="Status filter"
            srLabel
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            {statuses.map((s) => (
              <option key={s} value={s}>{s === 'all' ? 'All statuses' : s}</option>
            ))}
          </Select>
          <Select
            label="Minimum score filter"
            srLabel
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
          >
            <option value={0}>All scores</option>
            <option value={40}>40+ match</option>
            <option value={60}>60+ match</option>
            <option value={75}>75+ match</option>
            <option value={85}>85+ match</option>
          </Select>
          <button
            type="button"
            onClick={() => router.refresh()}
            aria-label="Refresh data"
            title="Refresh"
            className="inline-flex items-center justify-center w-9 h-9 rounded-md text-fg-muted hover:text-fg hover:bg-surface-raised border border-transparent hover:border-border focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors"
          >
            <Icon name="refresh" size={14} />
          </button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm" aria-describedby={captionId}>
          <caption id={captionId} className="sr-only">
            Job pipeline — sortable list of discovered roles with match score and status.
          </caption>
          <thead>
            <tr className="text-left border-b border-border">
              <th scope="col" className="px-4 sm:px-5 py-3 text-2xs font-semibold text-fg-subtle uppercase tracking-wider">Role</th>
              <th scope="col" className="px-3 py-3 text-2xs font-semibold text-fg-subtle uppercase tracking-wider">Company</th>
              <th scope="col" className="px-3 py-3 text-2xs font-semibold text-fg-subtle uppercase tracking-wider">Location</th>
              <th scope="col" className="px-3 py-3 text-2xs font-semibold text-fg-subtle uppercase tracking-wider">Score</th>
              <th scope="col" className="px-3 py-3 text-2xs font-semibold text-fg-subtle uppercase tracking-wider">Status</th>
              <th scope="col" className="px-3 py-3 text-2xs font-semibold text-fg-subtle uppercase tracking-wider">Found</th>
              <th scope="col" className="px-3 py-3"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-6 py-10 text-center text-fg-subtle text-xs">
                  No jobs match your filters.
                </td>
              </tr>
            ) : (
              filtered.map((job) => (
                <tr key={job.id} className="hover:bg-surface-raised/60 transition-colors">
                  <td className="px-4 sm:px-5 py-3">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="text-fg font-medium text-sm hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
                    >
                      {job.title}
                    </Link>
                    <div className="flex gap-1 mt-1">
                      {job.archetype && <Pill tone="success">{job.archetype}</Pill>}
                      {job.legitimacy_tier === 'Suspicious' && (
                        <Pill tone="danger">
                          <Icon name="alert-triangle" size={10} />
                          ghost?
                        </Pill>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-fg-muted text-xs">{job.company}</td>
                  <td className="px-3 py-3 text-fg-subtle text-2xs">{job.location || '—'}</td>
                  <td className="px-3 py-3">
                    <span className={`score-badge ${getScoreClass(job.match_score)}`}>
                      {getScoreGrade(job.match_score)} {job.match_score}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className={`status-badge ${getStatusClass(job.status)}`}>
                      {job.status}
                    </span>
                  </td>
                  <td className="px-3 py-3 text-fg-subtle text-2xs whitespace-nowrap">
                    {job.discovered_at
                      ? new Date(job.discovered_at).toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })
                      : '—'}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    {job.url && (
                      <a
                        href={job.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-2xs text-info hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
                      >
                        View <Icon name="arrow-up-right" size={11} />
                      </a>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
