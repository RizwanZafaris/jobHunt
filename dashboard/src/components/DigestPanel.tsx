'use client'

import { Card } from '@/components/ui/Card'
import { Stat } from '@/components/ui/Stat'
import { Icon } from '@/components/ui/Icon'

interface Digest {
  message?: string
  digest_content?: string
  run_date?: string
  created_at?: string
  jobs_found_today?: number
  jobs_scored_high?: number
  applications_sent?: number
  stale_companies?: number
  digest_sent?: boolean
}

interface Props {
  digest: Digest | null
}

export default function DigestPanel({ digest }: Props) {
  if (!digest || digest.message) {
    return (
      <Card title="Daily digest" className="h-full">
        <div className="flex flex-col items-center justify-center py-8 text-center gap-1">
          <Icon name="sparkles" size={20} className="text-fg-subtle mb-1" />
          <p className="text-fg-muted text-xs">No digest yet.</p>
          <p className="text-2xs text-fg-subtle">Run the boss agent to generate one.</p>
        </div>
      </Card>
    )
  }

  const content = digest.digest_content || ''
  const runDate = digest.run_date || digest.created_at?.split('T')[0]

  return (
    <Card
      title="Daily digest"
      actions={
        runDate ? (
          <span className="text-2xs text-fg-subtle">
            {new Date(runDate).toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })}
          </span>
        ) : null
      }
      className="h-full"
    >
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 mb-4 pb-4 border-b border-border">
        <Stat label="Jobs today" value={digest.jobs_found_today ?? 0} />
        <Stat label="High match" value={digest.jobs_scored_high ?? 0} tone={digest.jobs_scored_high ? 'success' : 'default'} />
        <Stat label="Applied" value={digest.applications_sent ?? 0} />
        <Stat label="Stale companies" value={digest.stale_companies ?? 0} tone={digest.stale_companies ? 'warning' : 'default'} />
      </dl>

      <div className="text-xs text-fg-muted whitespace-pre-wrap leading-relaxed font-sans">
        {content.length > 600 ? content.slice(0, 600) + '…' : content}
      </div>

      {digest.digest_sent && (
        <div className="mt-3 flex items-center gap-1.5 text-2xs text-success">
          <Icon name="check" size={12} />
          Email delivered
        </div>
      )}
    </Card>
  )
}
