'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { updateApplication, type ApplicationsResponse, type Application } from '@/lib/profile-api'
import { Pill } from '@/components/ui/Pill'

interface Props {
  initial: ApplicationsResponse
}

const COLUMNS: { key: string; label: string; color: string }[] = [
  { key: 'evaluated', label: 'Evaluated', color: 'border-info-border bg-info-bg/20' },
  { key: 'applied', label: 'Applied', color: 'border-info-border bg-info-bg/20' },
  { key: 'interviewing', label: 'Interviewing', color: 'border-warning-border bg-warning-bg/20' },
  { key: 'offer', label: 'Offer', color: 'border-success-border bg-success-bg/20' },
  { key: 'accepted', label: 'Accepted', color: 'border-success-border bg-success-bg/40' },
  { key: 'rejected', label: 'Rejected', color: 'border-danger-border bg-danger-bg/20' },
]

const SCORE_COLOR = (s: number) =>
  s >= 80 ? 'text-success'
  : s >= 65 ? 'text-info'
  : s >= 50 ? 'text-warning'
  : 'text-fg-muted'

export default function ApplicationsBoard({ initial }: Props) {
  const router = useRouter()
  const [apps, setApps] = useState<Application[]>(initial.applications)
  // BUG-012: surface the server-side threshold for the warning copy.
  const applyThreshold = initial.apply_threshold ?? 85

  async function changeStatus(id: string, next: string) {
    const original = apps
    setApps((arr) => arr.map((a) => (a.id === id ? { ...a, status: next } : a)))
    try {
      await updateApplication(id, { status: next })
      router.refresh()
    } catch (e: any) {
      setApps(original)
      alert(`Failed: ${e.message}`)
    }
  }

  const counts = COLUMNS.map((c) => ({
    ...c,
    count: apps.filter((a) => a.status === c.key).length,
    items: apps.filter((a) => a.status === c.key),
  }))

  if (apps.length === 0) {
    return (
      <div className="bg-surface border border-border rounded-xl p-8 text-center">
        <h2 className="text-lg font-semibold text-fg mb-2">No applications yet</h2>
        <p className="text-sm text-fg-muted mb-4">
          Click &ldquo;Track in pipeline&rdquo; on a job detail page to add it here.
        </p>
        <Link
          href="/"
          className="text-xs bg-info-bg hover:bg-info-bg text-fg px-4 py-2 rounded-lg inline-block font-medium"
        >
          Browse jobs →
        </Link>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
      {counts.map((col) => (
        <div key={col.key} className={`border-t-2 ${col.color} bg-surface rounded-xl p-3 min-h-[200px]`}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-semibold text-fg uppercase tracking-wider">{col.label}</h3>
            <span className="text-xs text-fg-subtle">{col.count}</span>
          </div>
          <div className="space-y-2">
            {col.items.length === 0 ? (
              <p className="text-2xs text-fg-subtle italic text-center py-4">empty</p>
            ) : (
              col.items.map((a) => (
                <article
                  key={a.id}
                  className="bg-surface-raised/50 border border-border rounded-lg p-2.5 hover:border-border-strong transition-colors"
                >
                  <div className="flex items-center justify-between gap-2">
                    <h4 className="text-xs font-semibold text-fg truncate flex-1" title={a.role}>
                      {a.role}
                    </h4>
                    {a.job?.match_score !== undefined && (
                      <span className={`text-2xs font-semibold tabular-nums ${SCORE_COLOR(a.job.match_score)}`}>
                        {a.job.match_score}
                      </span>
                    )}
                  </div>
                  <p className="text-2xs text-info mt-0.5 truncate">{a.company}</p>
                  {a.job?.location && (
                    <p className="text-2xs text-fg-subtle truncate">{a.job.location}</p>
                  )}
                  <div className="flex items-center gap-1 mt-2">
                    <select
                      value={a.status}
                      onChange={(e) => changeStatus(a.id, e.target.value)}
                      aria-label={`Change status for ${a.company} application`}
                      className="text-2xs bg-surface border border-border-strong rounded px-1.5 py-0.5 text-fg-muted flex-1"
                    >
                      {COLUMNS.map((c) => (
                        <option key={c.key} value={c.key}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                    {a.job_id && (
                      <Link
                        href={`/jobs/${a.job_id}`}
                        className="text-2xs text-info hover:text-info px-1"
                        title="Open job detail"
                      >
                        →
                      </Link>
                    )}
                  </div>
                  {a.applied_date && (
                    <p className="text-2xs text-fg-subtle mt-1">
                      applied {new Date(a.applied_date).toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })}
                    </p>
                  )}
                  {/* BUG-012: explain *why* a rejection happened when the
                       underlying match_score was already below the apply bar. */}
                  {a.status === 'rejected' && a.threshold_violated && (
                    <div className="mt-1.5">
                      <Pill
                        tone="warning"
                        size="xs"
                        title={`Underlying match score was ${a.job?.match_score ?? '—'} / 100; apply_threshold is ${applyThreshold}.`}
                      >
                        Applied below apply threshold (score &lt; {applyThreshold})
                      </Pill>
                    </div>
                  )}
                </article>
              ))
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
