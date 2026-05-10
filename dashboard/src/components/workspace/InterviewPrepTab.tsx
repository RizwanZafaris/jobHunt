/**
 * InterviewPrepTab — surfaces the interview pack or kicks off prep.
 *
 * If a converged interview_prep row exists, link out to
 * `/applications/{job_id}/interview-studio` (Phase 3 surface — this tab
 * does NOT build the studio itself, that belongs to the Phase 3 agent).
 *
 * Otherwise show two paths:
 *   • If the resume is ready → "Run interview prep (G3)" CTA. Hits the
 *     existing /interview-prep endpoint already in api/server.py.
 *   • If the resume is not yet ready → quiet hint to build first.
 */
'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import { EmptyState } from '@/components/ui/EmptyState'
import type { WorkspaceInterviewPrep } from '@/lib/types/workspace'

export interface InterviewPrepTabProps {
  jobId: number
  interviewPrep: WorkspaceInterviewPrep | null
  applicationId: string | null
  resumeReady: boolean
}

export function InterviewPrepTab({
  jobId,
  interviewPrep,
  applicationId,
  resumeReady,
}: InterviewPrepTabProps) {
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState<string>('')

  const studioHref = `/applications/${jobId}/interview-studio`

  const handleKickoff = async () => {
    setBusy(true)
    setErrorMsg(null)
    try {
      const res = await fetch('/api/proxy/interview-prep', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId }),
        cache: 'no-store',
      })
      if (!res.ok) {
        const txt = await res.text()
        throw new Error(`HTTP ${res.status}: ${txt.slice(0, 200)}`)
      }
      setStatusMsg('Interview prep kicked off — refresh in ~3 minutes.')
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  // ── Pack ready: link out to the (Phase 3) studio. ─────────────────
  if (interviewPrep?.has_pack) {
    return (
      <Card
        padding="md"
        title="Interview prep is ready"
        description="A focused studio with mock questions, STAR stories, and a chat tutor."
      >
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2 flex-wrap">
            <Pill tone="success" size="xs" icon={<Icon name="check" size={10} />}>
              {interviewPrep.status ?? 'converged'}
            </Pill>
            {interviewPrep.round_type && (
              <Pill tone="info" size="xs">
                {interviewPrep.round_type}
                {interviewPrep.round_number ? ` · round ${interviewPrep.round_number}` : ''}
              </Pill>
            )}
          </div>
          <div className="flex items-center gap-2">
            {interviewPrep.prep_pack_url && (
              <a
                href={interviewPrep.prep_pack_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-2xs font-medium text-fg-muted hover:text-fg px-2 py-1 rounded-md border border-border-strong hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <Icon name="download" size={11} />
                Pack
              </a>
            )}
            <Link
              href={studioHref}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors min-h-9"
            >
              Open interview studio
              <Icon name="arrow-right" size={12} />
            </Link>
          </div>
        </div>
        {applicationId && (
          <p className="mt-3 text-2xs text-fg-subtle">
            Tied to application <span className="font-mono">{applicationId.slice(0, 8)}</span>.
          </p>
        )}
      </Card>
    )
  }

  // ── No pack yet: kickoff or wait for resume. ──────────────────────
  if (!resumeReady) {
    return (
      <Card padding="md">
        <EmptyState
          icon="brain"
          title="Build the resume first"
          description="Interview prep uses the same persona + JD context that G2 generates. Run G2 on the Resume tab, then kick off G3 here."
        />
      </Card>
    )
  }

  return (
    <Card padding="md">
      <EmptyState
        icon="brain"
        title="Run interview prep (G3)"
        description="G3 builds a prep pack tailored to this role's interview format — likely questions, STAR stories from your bank, and competency gaps to anticipate. ~3 min, ~$0.50."
        action={
          <Button
            variant="primary"
            size="md"
            onClick={handleKickoff}
            loading={busy}
            disabled={busy}
          >
            <Icon name="rocket" size={14} />
            Generate prep pack
          </Button>
        }
        hint={
          errorMsg ? <span className="text-danger">{errorMsg}</span> :
          statusMsg ? <span className="text-success">{statusMsg}</span> : undefined
        }
      />
    </Card>
  )
}

export default InterviewPrepTab
