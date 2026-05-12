/**
 * WorkspaceClient — interactive shell for the application workspace.
 *
 * Five-tab shell: Role · Resume · Interview · Network · Apply.
 * The page (Server Component) hands the full bundle in; this client
 * component owns tab state, the sticky workspace tab strip, and routes
 * each tab to its dedicated component.
 *
 * Layout decisions:
 *   • Sticky tab strip (under AppShell's top bar) with the workspace
 *     tabs only. Per BUG-012, the "Mark as applied" CTA lives solely
 *     in the Apply tab right-rail card so the action is contextual
 *     (alongside the checklist + applied-date copy) and not duplicated.
 *   • Tab strip is the existing TabStrip primitive but driven by
 *     internal state (no URL writes — simpler than RSC + searchParams
 *     ping-pong, and cheaper for tab switches).
 *   • Below the strip: the active tab's component. Each tab gets only
 *     the slice of the bundle it needs as a prop.
 */
'use client'

import { useCallback, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { clsx } from 'clsx'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { RoleOverviewTab } from '@/components/workspace/RoleOverviewTab'
import { ResumeTab } from '@/components/workspace/ResumeTab'
import { NetworkTab } from '@/components/workspace/NetworkTab'
import { InterviewPrepTab } from '@/components/workspace/InterviewPrepTab'
import { ApplyTab } from '@/components/workspace/ApplyTab'
import { markApplied } from '@/lib/api/workspace'
import type { Workspace, WorkspaceTabKey } from '@/lib/types/workspace'

const TABS: { id: WorkspaceTabKey; label: string; iconName: 'briefcase' | 'document' | 'users' | 'brain' | 'check' }[] = [
  { id: 'role',      label: 'Role',           iconName: 'briefcase' },
  { id: 'resume',    label: 'Resume',         iconName: 'document' },
  { id: 'interview', label: 'Interview prep', iconName: 'brain' },
  { id: 'network',   label: 'Network',        iconName: 'users' },
  { id: 'apply',     label: 'Apply',          iconName: 'check' },
]

const TERMINAL_STATUSES = new Set([
  'applied', 'interviewing', 'offered', 'rejected', 'withdrawn',
])

export interface WorkspaceClientProps {
  workspace: Workspace
  initialTab: WorkspaceTabKey
}

export function WorkspaceClient({ workspace: initial, initialTab }: WorkspaceClientProps) {
  const router = useRouter()
  const [tab, setTab] = useState<WorkspaceTabKey>(initialTab)
  const [workspace, setWorkspace] = useState<Workspace>(initial)
  const [applying, setApplying] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string>('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const job = workspace.job
  const application = workspace.application
  const isApplied = !!application && TERMINAL_STATUSES.has((application.status || '').toLowerCase())
  const score = typeof job.match_score === 'number' ? job.match_score : null

  const companyInitial = useMemo(() => {
    const c = (job.company || '').trim()
    return c ? c.charAt(0).toUpperCase() : '?'
  }, [job.company])

  const warmIntroCount = workspace.warm_intro_paths.length

  const handleMarkApplied = useCallback(async () => {
    setApplying(true)
    setErrorMsg(null)
    try {
      const result = await markApplied(job.id)
      setWorkspace((prev) => ({ ...prev, application: result.application }))
      setStatusMsg('Marked as applied — moving to /applications')
      // Give the live region a beat to announce, then route.
      setTimeout(() => router.push('/applications'), 450)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err))
      setApplying(false)
    }
  }, [job.id, router])

  const handleResumeUpdated = useCallback((next: Workspace['resume']) => {
    setWorkspace((prev) => ({ ...prev, resume: next }))
  }, [])

  return (
    <>
      {/* ── Header strip ─────────────────────────────────────────── */}
      <header className="flex items-start gap-4 pb-2 flex-wrap">
        {/* Logo placeholder — initial in a coloured circle. */}
        <div
          aria-hidden
          className="inline-flex items-center justify-center w-12 h-12 rounded-md bg-surface-raised text-fg font-semibold text-base shrink-0 border border-border"
        >
          {companyInitial}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-2xs uppercase tracking-[0.12em] font-semibold text-fg-subtle mb-1">
            {job.company}
            {job.location ? <span className="text-fg-subtle/80 normal-case font-normal"> · {job.location}</span> : null}
          </p>
          <h1 className="text-2xl sm:text-3xl font-display font-normal text-fg leading-tight tracking-tight">
            {job.title || 'Application workspace'}
          </h1>
          <div className="mt-2 flex items-center gap-2 flex-wrap">
            {score !== null && (
              <Pill tone={score >= 85 ? 'success' : score >= 75 ? 'info' : 'neutral'} size="xs">
                Score {score}/100
              </Pill>
            )}
            {warmIntroCount > 0 && (
              <Pill tone="accent" size="xs" icon={<Icon name="users" size={11} />}>
                {warmIntroCount} warm intro{warmIntroCount === 1 ? '' : 's'}
              </Pill>
            )}
            {workspace.resume?.status === 'converged' && (
              <Pill tone="success" size="xs" icon={<Icon name="check" size={11} />}>
                Resume ready
              </Pill>
            )}
            {workspace.interview_prep?.has_pack && (
              <Pill tone="info" size="xs" icon={<Icon name="brain" size={11} />}>
                Interview pack
              </Pill>
            )}
            {isApplied && (
              <Pill tone="success" size="xs">
                Applied {application?.applied_date ? `· ${application.applied_date}` : ''}
              </Pill>
            )}
            {job.url && (
              <a
                href={job.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-2xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
              >
                Open posting <Icon name="arrow-up-right" size={10} />
              </a>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Link
            href="/today"
            className="inline-flex items-center gap-1 text-2xs font-medium text-fg-muted hover:text-fg px-2 py-1 rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <Icon name="arrow-right" size={12} className="rotate-180" /> Back to Today
          </Link>
        </div>
      </header>

      {/* ── Sticky workspace tab strip ───────────────────────────────
       *   BUG-012: previously this row also held a duplicate "Mark as
       *   applied" button. We keep just the tabs here — the action lives
       *   on the Apply tab right-rail card alongside the checklist. */}
      <div className="sticky top-14 z-20 -mx-4 sm:-mx-6 lg:-mx-8 px-4 sm:px-6 lg:px-8 bg-bg/95 backdrop-blur-sm border-b border-border">
        <div className="flex items-center justify-between gap-3 py-2 flex-wrap">
          <nav role="tablist" aria-label="Workspace tabs" className="flex items-center gap-0.5 -mb-px">
            {TABS.map((t) => {
              const active = tab === t.id
              return (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  aria-current={active ? 'page' : undefined}
                  onClick={() => setTab(t.id)}
                  className={clsx(
                    'inline-flex items-center gap-1.5 px-3 sm:px-3.5 py-2 text-2xs font-medium border-b-2 transition-colors',
                    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                    active
                      ? 'border-accent text-fg'
                      : 'border-transparent text-fg-muted hover:text-fg hover:border-border-strong',
                  )}
                >
                  <Icon name={t.iconName} size={13} />
                  <span className="hidden sm:inline">{t.label}</span>
                </button>
              )
            })}
          </nav>
        </div>
      </div>

      {errorMsg && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger" role="alert">
            {errorMsg}
          </p>
        </Card>
      )}

      {/* ── Tab body ──────────────────────────────────────────────── */}
      <div className="pt-2">
        {tab === 'role' && (
          <RoleOverviewTab job={workspace.job} persona={workspace.persona} />
        )}
        {tab === 'resume' && (
          <ResumeTab
            jobId={workspace.job.id}
            resume={workspace.resume}
            onResumeUpdated={handleResumeUpdated}
          />
        )}
        {tab === 'interview' && (
          <InterviewPrepTab
            jobId={workspace.job.id}
            interviewPrep={workspace.interview_prep}
            applicationId={workspace.application?.id ?? null}
            resumeReady={workspace.resume?.status === 'converged'}
          />
        )}
        {tab === 'network' && (
          <NetworkTab
            companyName={workspace.job.company}
            paths={workspace.warm_intro_paths}
            networkSize={workspace.network_size}
            targetResolved={!!workspace.target_company_id}
            onNetworkRefresh={() => router.refresh()}
          />
        )}
        {tab === 'apply' && (
          <ApplyTab
            workspace={workspace}
            applying={applying}
            isApplied={isApplied}
            onMarkApplied={handleMarkApplied}
            onJumpTab={setTab}
          />
        )}
      </div>

      <LiveRegion>{statusMsg}</LiveRegion>
    </>
  )
}

export default WorkspaceClient
