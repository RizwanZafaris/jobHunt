/**
 * ApplyTab — the launch checklist + the big Mark-as-applied button.
 *
 * Four-step checklist (per spec):
 *   1. Resume reviewed (auto-checks once a converged build exists OR
 *      the user has manually edited it)
 *   2. Cover note ready (auto-checks if application.cover_email present;
 *      otherwise marked optional)
 *   3. Warm intro identified (or explicitly documented as cold)
 *   4. Application URL open
 *
 * Each unchecked item shows a "Jump to {tab}" link. After hitting Mark
 * as applied, WorkspaceClient routes to /applications.
 *
 * No clever auto-progression — the user can always click Mark as
 * applied even with checklist gaps. The checklist is a nudge, not a
 * gate.
 */
'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import type { Workspace, WorkspaceTabKey } from '@/lib/types/workspace'

export interface ApplyTabProps {
  workspace: Workspace
  applying: boolean
  isApplied: boolean
  onMarkApplied: () => void | Promise<void>
  onJumpTab: (tab: WorkspaceTabKey) => void
}

interface ChecklistItem {
  id: string
  label: string
  state: 'done' | 'pending' | 'optional'
  detail?: string
  jumpTo?: WorkspaceTabKey
  jumpHref?: string
  jumpLabel?: string
  manuallyToggleable?: boolean
}

export function ApplyTab({
  workspace,
  applying,
  isApplied,
  onMarkApplied,
  onJumpTab,
}: ApplyTabProps) {
  const [coldAck, setColdAck] = useState(false)
  const [postingOpened, setPostingOpened] = useState(false)
  const job = workspace.job
  const application = workspace.application
  const resume = workspace.resume
  const paths = workspace.warm_intro_paths

  const resumeDone = !!resume && (resume.status === 'converged' || !!resume.user_edited_md)
  // BUG-033 (2026-05-12): "Cover note ready" used to gate on
  // `application?.cover_email`, a dead column. G2 writes the cover email
  // markdown to `resume_builds.cover_email_md`. Apply tab now sources
  // from the resume artifact in the workspace bundle. The legacy
  // application column is kept as a fallback so older rows (or any
  // future writer that lands on the application row directly) still
  // count.
  const coverDone = !!resume?.cover_email_md || !!application?.cover_email

  const networkDone =
    paths.length > 0 || coldAck

  const items: ChecklistItem[] = [
    {
      id: 'resume',
      label: 'Resume reviewed and tailored',
      state: resumeDone ? 'done' : 'pending',
      detail: resumeDone
        ? resume?.user_edited_md
          ? 'You\'ve edited this resume — looks ready.'
          : 'Converged G2 build on file.'
        : 'Build or open the resume on the Resume tab.',
      jumpTo: 'resume',
      jumpLabel: resumeDone ? 'Re-review' : 'Open resume',
    },
    {
      id: 'cover',
      label: 'Cover note ready',
      state: coverDone ? 'done' : 'optional',
      detail: coverDone
        ? 'Cover email on file from the latest build.'
        : 'Optional — many ATS flows don\'t require one. The Resume build emits a cover email alongside the resume if you want it.',
      jumpTo: 'resume',
      jumpLabel: coverDone ? 'View cover' : undefined,
    },
    {
      id: 'network',
      label: 'Warm intro identified',
      state: networkDone ? 'done' : 'pending',
      detail: paths.length > 0
        ? `${paths.length} warm path${paths.length === 1 ? '' : 's'} surfaced — pick the strongest.`
        : coldAck
        ? 'Acknowledged — applying cold.'
        : 'Pick a warm intro from the Network tab, or acknowledge applying cold.',
      jumpTo: paths.length > 0 ? 'network' : undefined,
      jumpLabel: paths.length > 0 ? 'See intros' : undefined,
      manuallyToggleable: paths.length === 0,
    },
    {
      id: 'posting',
      label: 'Application URL open',
      state: postingOpened ? 'done' : (job.url ? 'pending' : 'optional'),
      detail: job.url
        ? postingOpened
          ? 'Posting opened in a new tab.'
          : 'Click through and start filling the form.'
        : 'No public URL on file for this posting.',
      jumpHref: job.url ?? undefined,
      jumpLabel: job.url ? (postingOpened ? 'Reopen' : 'Open posting') : undefined,
    },
  ]

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 space-y-3">
        <Card
          padding="md"
          title="Pre-flight checklist"
          description="Quick gut check before you submit. Not a gate."
        >
          <ol className="flex flex-col gap-2" aria-label="Application checklist">
            {items.map((item) => (
              <li key={item.id}>
                <ChecklistRow
                  item={item}
                  onJumpTab={onJumpTab}
                  onColdAck={
                    item.id === 'network' && paths.length === 0
                      ? () => setColdAck((v) => !v)
                      : undefined
                  }
                  coldAcked={item.id === 'network' ? coldAck : false}
                  onPostingOpen={
                    item.id === 'posting' && job.url
                      ? () => setPostingOpened(true)
                      : undefined
                  }
                />
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <div className="space-y-3">
        <Card padding="md">
          <h3 className="text-sm font-semibold text-fg mb-1">Ready to submit?</h3>
          <p className="text-2xs text-fg-muted leading-relaxed mb-4">
            We&apos;ll mark this application as <span className="text-fg font-semibold">applied</span> with today&apos;s date,
            move it onto the Applications board, and drop the action card from /today.
          </p>
          <Button
            variant={isApplied ? 'success' : 'primary'}
            size="lg"
            block
            onClick={onMarkApplied}
            loading={applying}
            disabled={isApplied || applying}
          >
            {isApplied ? (
              <>
                <Icon name="check" size={14} />
                Applied
              </>
            ) : (
              <>
                Mark as applied
                <Icon name="check" size={14} />
              </>
            )}
          </Button>
          {isApplied && application?.applied_date && (
            <p className="mt-2 text-2xs text-success text-center">
              Applied on {application.applied_date}
            </p>
          )}
        </Card>

        <Card padding="md">
          <h3 className="text-sm font-semibold text-fg mb-1.5">After you apply</h3>
          <ul className="space-y-1.5 text-2xs text-fg-muted leading-relaxed">
            <li>• If a recruiter responds: log it on /applications.</li>
            <li>• At ~7 days silence: a stale-application card surfaces on /today.</li>
            <li>• Outcome data feeds persona evolution — every application teaches the next.</li>
          </ul>
        </Card>
      </div>
    </div>
  )
}

function ChecklistRow({
  item,
  onJumpTab,
  onColdAck,
  coldAcked,
  onPostingOpen,
}: {
  item: ChecklistItem
  onJumpTab: (tab: WorkspaceTabKey) => void
  onColdAck?: () => void
  coldAcked?: boolean
  onPostingOpen?: () => void
}) {
  const tone = item.state === 'done' ? 'success' : item.state === 'optional' ? 'neutral' : 'warning'
  const iconName = item.state === 'done' ? 'check' : item.state === 'optional' ? 'circle-half' : 'alert-triangle'
  const label = item.state === 'done' ? 'Done' : item.state === 'optional' ? 'Optional' : 'Pending'

  return (
    <article
      className={[
        'flex items-start gap-3 rounded-md border p-3',
        item.state === 'done'
          ? 'border-success-border bg-success-bg/40'
          : item.state === 'optional'
          ? 'border-border bg-surface'
          : 'border-warning-border bg-warning-bg/40',
      ].join(' ')}
    >
      <span
        aria-hidden
        className="inline-flex items-center justify-center w-6 h-6 rounded-md bg-surface text-fg-muted shrink-0 mt-0.5 border border-border"
      >
        <Icon name={iconName} size={12} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-sm font-semibold text-fg">{item.label}</p>
          <Pill tone={tone} size="xs">
            {label}
          </Pill>
        </div>
        {item.detail && (
          <p className="text-2xs text-fg-muted leading-relaxed mt-0.5">{item.detail}</p>
        )}
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          {item.jumpTo && item.jumpLabel && (
            <button
              type="button"
              className="text-2xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline"
              onClick={() => onJumpTab(item.jumpTo!)}
            >
              {item.jumpLabel} →
            </button>
          )}
          {item.jumpHref && item.jumpLabel && (
            <a
              href={item.jumpHref}
              target="_blank"
              rel="noreferrer"
              onClick={onPostingOpen}
              className="inline-flex items-center gap-1 text-2xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline"
            >
              {item.jumpLabel}
              <Icon name="arrow-up-right" size={10} />
            </a>
          )}
          {onColdAck && (
            <label className="inline-flex items-center gap-1.5 text-2xs text-fg-muted">
              <input
                type="checkbox"
                checked={!!coldAcked}
                onChange={onColdAck}
                className="accent-accent"
              />
              No warm intro available — applying cold
            </label>
          )}
        </div>
      </div>
    </article>
  )
}

export default ApplyTab
