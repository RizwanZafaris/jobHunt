'use client'

/**
 * StudioClient — three-pane Interview Studio shell.
 *
 *   LEFT (40%)   PrepMaterial      — collapsible sections of the G3 prep pack
 *   CENTER (35%) TutorChat         — chat tutor anchored to the prep pack
 *   RIGHT (25%)  OutcomeLogger     — log this round + show prior rounds
 *
 * On mobile (< md), the panes stack: prep first, chat second, outcome third.
 *
 * State boundary: this component owns the conceptLevel (lifted out of
 * TutorChat so PrepMaterial's "Ask tutor about this" + ConceptLadder can
 * participate). Everything else is local to the children.
 *
 * "No prep pack yet" branch: shows a single "Build prep pack" CTA that
 * calls /build-prep-pack and then router.refresh(). The G3 worker handles
 * convergence; the user sees the new pack on the next paint.
 */
import { useCallback, useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'

import type {
  ConceptLevel,
  InterviewStudio,
  PrepSection,
  TutorMessage,
} from '@/lib/types/interview-studio'
import { buildPrepPack } from '@/lib/api/studio'
import { Button, Card, EmptyState, Pill } from '@/components/ui'

import ConceptLadder from './ConceptLadder'
import OutcomeLogger from './OutcomeLogger'
import PrepMaterial from './PrepMaterial'
import TutorChat from './TutorChat'

interface StudioClientProps {
  applicationId: string
  initial: InterviewStudio
}

export default function StudioClient({ applicationId, initial }: StudioClientProps) {
  const router = useRouter()
  const [conceptLevel, setConceptLevel] = useState<ConceptLevel>(
    initial.current_concept_level || 'basics',
  )
  const [history, setHistory] = useState<TutorMessage[]>(initial.tutor_chat_history || [])
  const [pendingPrompt, setPendingPrompt] = useState<string>('')
  const [isBuildingPack, startBuilding] = useTransition()
  const [buildError, setBuildError] = useState<string | null>(null)

  const prep = initial.interview_prep
  const company =
    prep.company_name || initial.application.company || initial.job.company || 'this company'

  const handleBuildPack = useCallback(() => {
    setBuildError(null)
    startBuilding(async () => {
      try {
        await buildPrepPack(applicationId, {
          roundType: 'hm',
          roundNumber: Math.max(1, (prep.round_number ?? 0) + 1),
        })
        router.refresh()
      } catch (e: unknown) {
        setBuildError(e instanceof Error ? e.message : 'Failed to enqueue prep pack')
      }
    })
  }, [applicationId, prep.round_number, router, startBuilding])

  // PrepMaterial → Tutor handoff. We pre-fill the input via pendingPrompt;
  // TutorChat clears its own state once a prompt is submitted.
  const handleAskAbout = useCallback((section: PrepSection, item: string) => {
    const sectionLabel = section.replace(/_/g, ' ')
    const prompt = `Walk me through this ${sectionLabel}: "${item.slice(0, 240)}"`
    setPendingPrompt(prompt)
  }, [])

  const handleEscalateSection = useCallback((target: ConceptLevel) => {
    setConceptLevel(target)
  }, [])

  if (!prep.has_pack) {
    return (
      <div className="grid gap-6">
        <Card padding="lg">
          <EmptyState
            title={`No prep pack yet for ${company}`}
            description="The G3 graph will research the role, predict likely questions, match STAR stories, and write the prep pack. Then the tutor here can walk you through it."
            action={
              <Button
                onClick={handleBuildPack}
                loading={isBuildingPack}
                disabled={isBuildingPack}
              >
                {isBuildingPack ? 'Enqueuing…' : 'Build prep pack'}
              </Button>
            }
          />
          {buildError && (
            <p className="mt-4 text-2xs text-danger" role="alert">
              {buildError}
            </p>
          )}
        </Card>
      </div>
    )
  }

  return (
    <div className="grid gap-6 md:grid-cols-12">
      {/* LEFT — 40% */}
      <section className="md:col-span-5 space-y-4 min-w-0">
        <PrepMaterial
          prep={prep}
          conceptLevel={conceptLevel}
          onAskTutor={handleAskAbout}
          onEscalate={handleEscalateSection}
        />
      </section>

      {/* CENTER — 35% */}
      <section className="md:col-span-4 space-y-4 min-w-0">
        <Card padding="md">
          <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
            <h3 className="text-sm font-semibold text-fg">Tutor</h3>
            <span className="text-2xs text-fg-subtle">
              max ${initial.tutor_max_cost_usd.toFixed(2)} / turn
            </span>
          </div>
          <ConceptLadder value={conceptLevel} onChange={setConceptLevel} />
        </Card>
        <TutorChat
          applicationId={applicationId}
          conceptLevel={conceptLevel}
          history={history}
          onHistoryChange={setHistory}
          onConceptLevelRecommend={setConceptLevel}
          pendingPrompt={pendingPrompt}
          onPendingPromptConsumed={() => setPendingPrompt('')}
        />
      </section>

      {/* RIGHT — 25% */}
      <section className="md:col-span-3 space-y-4 min-w-0">
        <Card padding="md">
          <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
            <h3 className="text-sm font-semibold text-fg">Round outcome</h3>
            <Pill tone="info" size="xs">{`Logged ${initial.outcomes_logged.length}`}</Pill>
          </div>
          <p className="text-2xs text-fg-muted leading-relaxed">
            Logging this outcome will refine your <strong>{company}</strong> persona on the next
            Sunday cron.
          </p>
        </Card>
        <OutcomeLogger
          applicationId={applicationId}
          existingOutcomes={initial.outcomes_logged}
          companyName={company}
        />
      </section>
    </div>
  )
}
