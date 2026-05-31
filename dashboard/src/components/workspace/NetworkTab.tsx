/**
 * NetworkTab — warm-intro paths to THIS company, inside the workspace.
 *
 * Shows referral paths scoped to the application's company + a "Find people"
 * action (Apollo discovery, pre-filled to this company) when the graph is empty.
 */
'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { PeopleFinderModal } from '@/components/network/PeopleFinderModal'
import { IntroDraftModal } from '@/components/network/IntroDraftModal'
import type { ReferralPath } from '@/lib/types/network'

export interface NetworkTabProps {
  jobId: number
  companyName: string
  paths: ReferralPath[]
}

export function NetworkTab({ jobId, companyName, paths }: NetworkTabProps) {
  const router = useRouter()
  const [introTarget, setIntroTarget] = useState<{ personId: string; personName: string } | null>(null)
  const [finderOpen, setFinderOpen] = useState(false)

  const hasPaths = paths.length > 0

  return (
    <div className="flex flex-col gap-4">
      {!hasPaths ? (
        <EmptyState
          icon="users"
          title={`Find people at ${companyName}`}
          description={`Search Apollo for people who work at ${companyName} and add them to your network to surface warm referral paths.`}
          action={
            <Button variant="primary" size="md" onClick={() => setFinderOpen(true)}>
              <Icon name="search" size={14} />
              Find people
            </Button>
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex justify-end">
            <Button variant="secondary" size="sm" onClick={() => setFinderOpen(true)}>
              <Icon name="search" size={14} />
              Find more people
            </Button>
          </div>
          {paths.map((p) => (
            <Card key={p.person_id} padding="md">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-fg truncate">{p.person_name}</p>
                  <p className="text-2xs text-fg-muted truncate">{p.reasoning}</p>
                </div>
                <button
                  onClick={() => setIntroTarget({ personId: p.person_id, personName: p.person_name })}
                  className="shrink-0 text-2xs text-accent hover:underline"
                >
                  Draft intro
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {finderOpen && (
        <PeopleFinderModal
          company={companyName}
          onClose={() => setFinderOpen(false)}
          onAdded={() => router.refresh()}
        />
      )}
      {introTarget && (
        <IntroDraftModal
          personId={introTarget.personId}
          personName={introTarget.personName}
          onClose={() => setIntroTarget(null)}
        />
      )}
    </div>
  )
}
