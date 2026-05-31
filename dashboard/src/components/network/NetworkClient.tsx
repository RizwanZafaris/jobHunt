/**
 * NetworkClient — the /network page client shell.
 *
 * Sections: (1) your network summary + "Find people" (Apollo discovery),
 * (2) warm-intro paths to target companies.
 *
 * Replaces the former LinkedIn CSV import with Apollo-based people discovery
 * (PeopleFinderModal → /apollo/search-people → /network/people).
 */
'use client'

import { useRouter } from 'next/navigation'
import { useState } from 'react'
import { Card } from '@/components/ui/Card'
import { PageHeader } from '@/components/ui/PageHeader'
import { Icon } from '@/components/ui/Icon'
import { Button } from '@/components/ui/Button'
import { EmptyState } from '@/components/ui/EmptyState'
import { PeopleFinderModal } from '@/components/network/PeopleFinderModal'
import { IntroDraftModal } from '@/components/network/IntroDraftModal'
import type { NetworkData } from '@/lib/types/network'

export interface NetworkClientProps {
  data: NetworkData
}

export function NetworkClient({ data }: NetworkClientProps) {
  const router = useRouter()
  const [finderOpen, setFinderOpen] = useState(false)
  const [introTarget, setIntroTarget] = useState<{ personId: string; personName: string } | null>(null)

  const peopleCount = data.people?.length ?? 0
  const pathCount = data.paths?.length ?? 0

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Network"
        description="Warm intros + referral paths to your target companies."
      />

      {/* Section 1 — summary + find people */}
      <Card padding="md">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-sm font-semibold text-fg">Your network</h2>
            <p className="text-2xs text-fg-muted mt-0.5">
              {peopleCount} {peopleCount === 1 ? 'person' : 'people'} · {pathCount} warm{' '}
              {pathCount === 1 ? 'path' : 'paths'}
            </p>
          </div>
          <Button variant="primary" size="md" onClick={() => setFinderOpen(true)}>
            <Icon name="search" size={14} />
            Find people
          </Button>
        </div>
      </Card>

      {/* Section 2 — warm-intro paths */}
      {pathCount === 0 ? (
        <EmptyState
          icon="users"
          title="No connections yet"
          description="Find people at your target companies to start mapping warm intros."
          action={
            <Button variant="primary" size="md" onClick={() => setFinderOpen(true)}>
              <Icon name="search" size={14} />
              Find people
            </Button>
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          {data.paths.map((path) => (
            <Card key={path.person_id} padding="md">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-fg truncate">{path.person_name}</p>
                  <p className="text-2xs text-fg-muted truncate">{path.reasoning}</p>
                </div>
                <button
                  onClick={() => setIntroTarget({ personId: path.person_id, personName: path.person_name })}
                  className="shrink-0 text-2xs text-accent hover:underline"
                >
                  Draft intro
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* Modals */}
      {finderOpen && (
        <PeopleFinderModal
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
