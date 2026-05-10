/**
 * ResumeTab — view / edit / download / build the resume artifact.
 *
 * Three modes:
 *   1. No resume yet → big "Build my resume" CTA. POSTs
 *      /workspace/{id}/build-resume which enqueues G2. We poll the run
 *      every 8s and refresh the workspace bundle when it terminates.
 *   2. Resume present, view mode → markdown viewer (prose) + four
 *      buttons (Download MD / PDF / DOCX, Edit ✏️). The PDF/DOCX
 *      buttons are only enabled if the build has those URLs (Phase 2
 *      doesn't render server-side from markdown — coming Phase 3).
 *   3. Resume present, edit mode → routes to <ResumeEditor> below.
 *
 * The "Last edited" timestamp surfaces alongside the artefact pill so
 * the user trusts that their edits have been saved.
 */
'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import { EmptyState } from '@/components/ui/EmptyState'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { ResumeEditor } from '@/components/workspace/ResumeEditor'
import {
  buildResume,
  downloadResumeHref,
  fetchJobsRun,
  fetchWorkspace,
} from '@/lib/api/workspace'
import type { ResumeArtifact } from '@/lib/types/workspace'

const PROSE_CLS =
  'text-fg text-sm leading-relaxed ' +
  '[&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:mt-0 [&_h1]:mb-2 [&_h1]:text-fg ' +
  '[&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-fg ' +
  '[&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1.5 [&_h3]:text-fg ' +
  '[&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-2 [&_li]:my-0.5 ' +
  '[&_strong]:font-semibold [&_strong]:text-fg ' +
  '[&_a]:text-accent [&_a:hover]:underline ' +
  '[&_hr]:border-border [&_hr]:my-4 ' +
  '[&_code]:bg-surface-raised [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-2xs'

const POLL_INTERVAL_MS = 8000
const POLL_TIMEOUT_MS = 10 * 60 * 1000  // 10 min — G2 worst-case is ~5 min

export interface ResumeTabProps {
  jobId: number
  resume: ResumeArtifact | null
  onResumeUpdated: (next: ResumeArtifact | null) => void
}

export function ResumeTab({ jobId, resume, onResumeUpdated }: ResumeTabProps) {
  const [editing, setEditing] = useState(false)
  const [building, setBuilding] = useState(false)
  const [buildStatus, setBuildStatus] = useState<string>('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState<string>('')
  // Local override of resume (so a save updates the view immediately).
  const [localResume, setLocalResume] = useState<ResumeArtifact | null>(resume)
  useEffect(() => {
    setLocalResume(resume)
  }, [resume])

  const pollTimeout = useRef<number | null>(null)
  const pollStarted = useRef<number>(0)

  useEffect(() => {
    return () => {
      if (pollTimeout.current !== null) {
        window.clearTimeout(pollTimeout.current)
      }
    }
  }, [])

  const markdown = useMemo(() => {
    if (!localResume) return ''
    return (localResume.user_edited_md ?? localResume.resume_md ?? '').trim()
  }, [localResume])

  const lastEditedLabel = localResume?.user_edited_at
    ? formatDateTime(localResume.user_edited_at)
    : null

  const isUserEdited = !!localResume?.user_edited_md

  const handleResumeSaved = useCallback((next: ResumeArtifact) => {
    setLocalResume(next)
    onResumeUpdated(next)
    setStatusMsg('Resume edits saved')
  }, [onResumeUpdated])

  const handleBuild = useCallback(async () => {
    setBuilding(true)
    setErrorMsg(null)
    setBuildStatus('Queueing G2 build…')
    try {
      const queued = await buildResume(jobId)
      pollStarted.current = Date.now()
      pollRun(queued.run_id)
    } catch (err) {
      setBuilding(false)
      setBuildStatus('')
      setErrorMsg(err instanceof Error ? err.message : String(err))
    }
    // pollRun handles unwind on success/failure.
    function pollRun(runId: string) {
      if (pollTimeout.current !== null) {
        window.clearTimeout(pollTimeout.current)
      }
      const tick = async () => {
        try {
          const row = await fetchJobsRun(runId)
          if (row.status === 'queued') {
            setBuildStatus('Queued — waiting for a worker…')
          } else if (row.status === 'running') {
            const elapsed = Math.floor((Date.now() - pollStarted.current) / 1000)
            setBuildStatus(`Building resume… (${elapsed}s of ~5 min)`)
          } else if (row.status === 'succeeded') {
            setBuildStatus('Build finished — refreshing…')
            const fresh = await fetchWorkspace(jobId)
            setLocalResume(fresh.resume)
            onResumeUpdated(fresh.resume)
            setBuilding(false)
            setBuildStatus('')
            setStatusMsg('Resume ready')
            return
          } else if (row.status === 'failed' || row.status === 'cancelled') {
            setBuilding(false)
            setBuildStatus('')
            setErrorMsg(
              row.last_error
                ? `Build ${row.status}: ${row.last_error.slice(0, 200)}`
                : `Build ${row.status}.`,
            )
            return
          }
        } catch (err) {
          setBuilding(false)
          setBuildStatus('')
          setErrorMsg(err instanceof Error ? err.message : String(err))
          return
        }
        if (Date.now() - pollStarted.current > POLL_TIMEOUT_MS) {
          setBuilding(false)
          setBuildStatus('')
          setErrorMsg('Build is taking unusually long. Refresh the page to check.')
          return
        }
        pollTimeout.current = window.setTimeout(tick, POLL_INTERVAL_MS) as unknown as number
      }
      // Kick first tick immediately.
      pollTimeout.current = window.setTimeout(tick, 1500) as unknown as number
    }
  }, [jobId, onResumeUpdated])

  // ── No resume yet ─────────────────────────────────────────────
  if (!localResume) {
    return (
      <Card padding="md">
        {building ? (
          <div className="flex flex-col items-center text-center gap-3 py-6">
            <div className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-surface-raised text-accent">
              <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
            </div>
            <h3 className="text-sm font-semibold text-fg">Building your resume</h3>
            <p className="text-xs text-fg-muted max-w-md leading-relaxed">
              {buildStatus || 'G2 is running — writer, critic, polisher, persona gate. ~5 minutes.'}
            </p>
            <p className="text-2xs text-fg-subtle">You can switch tabs — this keeps running.</p>
          </div>
        ) : (
          <EmptyState
            icon="document"
            title="No resume built yet"
            description="Run G2 to build a tailored markdown resume against this role's persona. ~5 minutes, ~$1."
            action={
              <Button variant="primary" size="md" onClick={handleBuild}>
                <Icon name="rocket" size={14} />
                Build my resume
              </Button>
            }
            hint={errorMsg ? <span className="text-danger">{errorMsg}</span> : undefined}
          />
        )}
      </Card>
    )
  }

  // ── Edit mode ────────────────────────────────────────────────
  if (editing) {
    return (
      <ResumeEditor
        jobId={jobId}
        buildId={localResume.build_id}
        initialMarkdown={markdown}
        onCancel={() => setEditing(false)}
        onSaved={(next) => {
          handleResumeSaved(next)
          setEditing(false)
        }}
      />
    )
  }

  // ── View mode ────────────────────────────────────────────────
  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <Card padding="md">
        <div className="flex items-center gap-2 flex-wrap">
          <Pill tone={localResume.status === 'converged' ? 'success' : 'info'} size="xs">
            {localResume.status}
          </Pill>
          {isUserEdited && (
            <Pill tone="info" size="xs" icon={<Icon name="pencil" size={10} />}>edited</Pill>
          )}
          {typeof localResume.cost_usd_total === 'number' && (
            <span className="text-2xs text-fg-subtle tnum">
              ${localResume.cost_usd_total.toFixed(2)}
            </span>
          )}
          {typeof localResume.iterations === 'number' && (
            <span className="text-2xs text-fg-subtle">iter {localResume.iterations}</span>
          )}
          {lastEditedLabel && (
            <span className="text-2xs text-fg-subtle">
              Last edited {lastEditedLabel}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2 flex-wrap">
            <DownloadLink href={downloadResumeHref(jobId, 'md')} label="Markdown" enabled />
            <DownloadLink
              href={downloadResumeHref(jobId, 'pdf')}
              label="PDF"
              enabled={!!localResume.resume_pdf_url}
              hint={!localResume.resume_pdf_url ? 'PDF unavailable until next build' : undefined}
            />
            <DownloadLink
              href={downloadResumeHref(jobId, 'docx')}
              label="DOCX"
              enabled={!!localResume.resume_docx_url}
              hint={!localResume.resume_docx_url ? 'DOCX unavailable until next build' : undefined}
            />
            <Button variant="primary" size="sm" onClick={() => setEditing(true)}>
              <Icon name="pencil" size={12} />
              Edit with AI
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleBuild}
              loading={building}
              disabled={building}
            >
              <Icon name="refresh" size={12} />
              Rebuild (G2)
            </Button>
          </div>
        </div>
        {building && buildStatus && (
          <p className="mt-2 text-2xs text-fg-muted">{buildStatus}</p>
        )}
        {errorMsg && (
          <p className="mt-2 text-2xs text-danger" role="alert">
            {errorMsg}
          </p>
        )}
      </Card>

      {/* Resume body */}
      <Card padding="lg">
        {markdown ? (
          <article className={PROSE_CLS}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
          </article>
        ) : (
          <p className="text-fg-muted text-sm">
            This build doesn&apos;t have markdown stored yet. Re-run G2 to repopulate.
          </p>
        )}
      </Card>

      <LiveRegion>{statusMsg}</LiveRegion>
    </div>
  )
}

function DownloadLink({
  href,
  label,
  enabled,
  hint,
}: {
  href: string
  label: string
  enabled: boolean
  hint?: string
}) {
  if (!enabled) {
    return (
      <span
        className="inline-flex items-center gap-1 text-2xs font-medium text-fg-subtle px-2 py-1 rounded-md border border-border opacity-60 cursor-not-allowed"
        title={hint}
      >
        <Icon name="download" size={11} />
        {label}
      </span>
    )
  }
  return (
    <a
      href={href}
      className="inline-flex items-center gap-1 text-2xs font-medium text-fg-muted hover:text-fg px-2 py-1 rounded-md border border-border-strong hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors"
    >
      <Icon name="download" size={11} />
      {label}
    </a>
  )
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString('en-GB', {
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default ResumeTab
