'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApplication, updateApplication } from '@/lib/profile-api'

interface Props {
  jobId: number
  existingApp: any
}

export default function JobApplyButton({ jobId, existingApp }: Props) {
  const router = useRouter()
  const [app, setApp] = useState(existingApp)
  const [loading, setLoading] = useState(false)

  if (!app) {
    return (
      <button
        onClick={async () => {
          setLoading(true)
          try {
            const r = await createApplication(jobId, 'evaluated')
            setApp(r.row)
            router.refresh()
          } catch (e: any) {
            alert(`Failed: ${e.message}`)
          } finally {
            setLoading(false)
          }
        }}
        disabled={loading}
        className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg font-medium whitespace-nowrap"
      >
        {loading ? '⏳ Adding...' : '➕ Track in Pipeline'}
      </button>
    )
  }

  return (
    <select
      value={app.status}
      onChange={async (e) => {
        const next = e.target.value
        setLoading(true)
        try {
          await updateApplication(app.id, { status: next })
          setApp({ ...app, status: next })
          router.refresh()
        } catch (e: any) {
          alert(`Failed: ${e.message}`)
        } finally {
          setLoading(false)
        }
      }}
      disabled={loading}
      className="text-xs bg-blue-600 text-white px-3 py-1.5 rounded-lg font-medium border-0 cursor-pointer disabled:opacity-50"
    >
      <option value="evaluated">Evaluated</option>
      <option value="applied">Applied</option>
      <option value="interviewing">Interviewing</option>
      <option value="offer">Offer</option>
      <option value="accepted">Accepted</option>
      <option value="rejected">Rejected</option>
    </select>
  )
}
