'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { updateApplication, type ApplicationsResponse, type Application } from '@/lib/profile-api'

interface Props {
  initial: ApplicationsResponse
}

const COLUMNS: { key: string; label: string; color: string }[] = [
  { key: 'evaluated', label: 'Evaluated', color: 'border-purple-700 bg-purple-900/20' },
  { key: 'applied', label: 'Applied', color: 'border-cyan-700 bg-cyan-900/20' },
  { key: 'interviewing', label: 'Interviewing', color: 'border-amber-700 bg-amber-900/20' },
  { key: 'offer', label: 'Offer', color: 'border-emerald-700 bg-emerald-900/20' },
  { key: 'accepted', label: 'Accepted', color: 'border-emerald-500 bg-emerald-900/40' },
  { key: 'rejected', label: 'Rejected', color: 'border-red-800 bg-red-900/20' },
]

const SCORE_COLOR = (s: number) =>
  s >= 80 ? 'text-emerald-400'
  : s >= 65 ? 'text-blue-400'
  : s >= 50 ? 'text-amber-400'
  : 'text-gray-400'

export default function ApplicationsBoard({ initial }: Props) {
  const router = useRouter()
  const [apps, setApps] = useState<Application[]>(initial.applications)

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
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
        <h2 className="text-lg font-semibold text-white mb-2">No applications yet</h2>
        <p className="text-sm text-gray-400 mb-4">
          Click "Track in Pipeline" on a job detail page to add it here.
        </p>
        <Link
          href="/"
          className="text-xs bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-lg inline-block font-medium"
        >
          Browse jobs →
        </Link>
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
      {counts.map((col) => (
        <div key={col.key} className={`border-t-2 ${col.color} bg-gray-900 rounded-xl p-3 min-h-[200px]`}>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-xs font-semibold text-white uppercase tracking-wider">{col.label}</h3>
            <span className="text-xs text-gray-500">{col.count}</span>
          </div>
          <div className="space-y-2">
            {col.items.length === 0 ? (
              <p className="text-[11px] text-gray-600 italic text-center py-4">empty</p>
            ) : (
              col.items.map((a) => (
                <article
                  key={a.id}
                  className="bg-gray-800/50 border border-gray-800 rounded-lg p-2.5 hover:border-gray-700 transition-colors"
                >
                  <div className="flex items-center justify-between gap-2">
                    <h4 className="text-xs font-semibold text-white truncate flex-1" title={a.role}>
                      {a.role}
                    </h4>
                    {a.job?.match_score !== undefined && (
                      <span className={`text-[10px] font-semibold tabular-nums ${SCORE_COLOR(a.job.match_score)}`}>
                        {a.job.match_score}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-blue-400 mt-0.5 truncate">{a.company}</p>
                  {a.job?.location && (
                    <p className="text-[10px] text-gray-500 truncate">{a.job.location}</p>
                  )}
                  <div className="flex items-center gap-1 mt-2">
                    <select
                      value={a.status}
                      onChange={(e) => changeStatus(a.id, e.target.value)}
                      className="text-[10px] bg-gray-900 border border-gray-700 rounded px-1.5 py-0.5 text-gray-300 flex-1"
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
                        className="text-[10px] text-blue-400 hover:text-blue-300 px-1"
                        title="Open job detail"
                      >
                        →
                      </Link>
                    )}
                  </div>
                  {a.applied_date && (
                    <p className="text-[10px] text-gray-500 mt-1">
                      applied {new Date(a.applied_date).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
                    </p>
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
