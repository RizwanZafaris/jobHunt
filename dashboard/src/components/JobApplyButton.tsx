'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { createApplication, updateApplication } from '@/lib/profile-api'
import { Button } from '@/components/ui/Button'
import { Select } from '@/components/ui/Field'
import { Icon } from '@/components/ui/Icon'

interface Application {
  id?: string | null
  status?: string
}

interface Props {
  jobId: number
  existingApp?: Application | null
}

const STATUSES = [
  { value: 'evaluated',    label: 'Evaluated' },
  { value: 'applied',      label: 'Applied' },
  { value: 'interviewing', label: 'Interviewing' },
  { value: 'offer',        label: 'Offer' },
  { value: 'accepted',     label: 'Accepted' },
  { value: 'rejected',     label: 'Rejected' },
]

export default function JobApplyButton({ jobId, existingApp }: Props) {
  const router = useRouter()
  const [app, setApp] = useState<Application | null | undefined>(existingApp)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!app || !app.id) {
    return (
      <Button
        variant="secondary"
        size="md"
        onClick={async () => {
          setLoading(true)
          setError(null)
          try {
            const r = await createApplication(jobId, 'evaluated')
            setApp(r.row)
            router.refresh()
          } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Failed')
          } finally {
            setLoading(false)
          }
        }}
        loading={loading}
      >
        <Icon name="plus" size={14} />
        Track in pipeline
        {error && <span className="ml-2 text-2xs text-danger">— {error}</span>}
      </Button>
    )
  }

  return (
    <Select
      label="Application status"
      srLabel
      value={app.status ?? 'evaluated'}
      onChange={async (e) => {
        const next = e.target.value
        if (!app.id) return
        setLoading(true)
        setError(null)
        try {
          await updateApplication(app.id, { status: next })
          setApp({ ...app, status: next })
          router.refresh()
        } catch (err: unknown) {
          setError(err instanceof Error ? err.message : 'Failed')
        } finally {
          setLoading(false)
        }
      }}
      disabled={loading}
      className="min-h-9"
    >
      {STATUSES.map((s) => (
        <option key={s.value} value={s.value}>{s.label}</option>
      ))}
    </Select>
  )
}
