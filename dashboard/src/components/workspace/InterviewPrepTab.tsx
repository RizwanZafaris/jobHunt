/**
 * InterviewPrepTab — surfaces the interview pack or kicks off prep.
 *
 * If a converged interview_prep row exists, link out to the interview studio
 * and offer the rendered pack (URL or inline markdown fallback).
 *
 * Otherwise, kicking off prep ENQUEUES a G3 build on the durable queue
 * (POST /interview-studio/{application_id}/build-prep-pack → worker_run_g3)
 * and polls /jobs-runs/{run_id} until it terminates — exactly like the
 * Resume tab's G2 build.
 *
 * 2026-06-01: switched OFF the legacy inline `/interview-prep` endpoint.
 * That ran all 9 G3 steps synchronously inside the HTTP request, so it was
 * killed by the ~30s proxy/gateway timeout after ~2 steps and never produced
 * a pack (live diagnosis: 2 LLM calls then silence, 0 files written). The
 * queued path runs in the worker with no request timeout and persists a
 * durable interview_prep row + storage file.
 */
'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import { EmptyState } from '@/components/ui/EmptyState'
import { buildPrepPack } from '@/lib/api/studio'
import { fetchJobsRun } from '@/lib/api/workspace'
import type { WorkspaceInterviewPrep } from '@/lib/types/workspace'

export interface InterviewPrepTabProps {
  jobId: number
  interviewPrep: WorkspaceInterviewPrep | null
  applicationId: string | null
  resumeReady: boolean
}

// Mirror the Resume tab's poll cadence. G3 worst-case is ~3-5 min.
const POLL_INTERVAL_MS = 8000
const POLL_TIMEOUT_MS = 10 * 60 * 1000

export function InterviewPrepTab({
  jobId,
  interviewPrep,
  applicationId,
  resumeReady,
}: InterviewPrepTabProps) {
  const router = useRouter()
  const [busy, setBusy] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState<string>('')
  const pollTimeout = useRef<number | null>(null)
  const pollStarted = useRef<number>(0)

  // Cancel any in-flight poll when the tab unmounts.
  useEffect(
    () => () => {
      if (pollTimeout.current !== null) window.clearTimeout(pollTimeout.current)
    },
    [],
  )

  const studioHref = `/applications/${jobId}/interview-studio`

  const handleKickoff = useCallback(async () => {
    // The durable G3 path is application-scoped. Without an application there's
    // nothing to attach the prep pack to — guide the user instead of failing
    // opaquely (the old inline path accepted a bare job_id but timed out).
    if (!applicationId) {
      setErrorMsg(
        'Interview prep is tied to an application. Apply to this job first, then come back here.',
      )
      return
    }
    setBusy(true)
    setErrorMsg(null)
    setStatusMsg('Queueing G3 build…')
    try {
      const { job_run_id } = await buildPrepPack(applicationId)
      pollStarted.current = Date.now()
      pollRun(job_run_id)
    } catch (err) {
      setBusy(false)
      setStatusMsg('')
      setErrorMsg(err instanceof Error ? err.message : String(err))
    }

    // pollRun unwinds busy/status on every terminal path.
    function pollRun(runId: string) {
      if (pollTimeout.current !== null) window.clearTimeout(pollTimeout.current)
      const tick = async () => {
        try {
          const row = await fetchJobsRun(runId)
          if (row.status === 'queued') {
            setStatusMsg('Queued — waiting for a worker…')
          } else if (row.status === 'running') {
            const elapsed = Math.floor((Date.now() - pollStarted.current) / 1000)
            setStatusMsg(`Building prep pack… (${elapsed}s of ~3 min)`)
          } else if (row.status === 'succeeded') {
            setStatusMsg('Prep pack ready — refreshing…')
            setBusy(false)
            // Re-run the server component so the converged interview_prep row
            // is fetched and this tab flips to the "ready" card.
            router.refresh()
            return
          } else if (row.status === 'failed' || row.status === 'cancelled') {
            setBusy(false)
            setStatusMsg('')
            setErrorMsg(
              row.last_error
                ? `Prep ${row.status}: ${row.last_error.slice(0, 200)}`
                : `Prep ${row.status}.`,
            )
            return
          }
        } catch (err) {
          setBusy(false)
          setStatusMsg('')
          setErrorMsg(err instanceof Error ? err.message : String(err))
          return
        }
        if (Date.now() - pollStarted.current > POLL_TIMEOUT_MS) {
          setBusy(false)
          setStatusMsg('')
          setErrorMsg('Prep is taking unusually long. Refresh the page to check.')
          return
        }
        pollTimeout.current = window.setTimeout(tick, POLL_INTERVAL_MS) as unknown as number
      }
      // Kick the first tick shortly after enqueue.
      pollTimeout.current = window.setTimeout(tick, 1500) as unknown as number
    }
  }, [applicationId, router])

  // ── Pack ready: link out to the studio + offer the rendered pack. ─────
  if (interviewPrep?.has_pack) {
    // BUG-034 (2026-05-12): when the Supabase Storage upload fails, G3
    // still stores the rendered markdown in `interview_prep.prep_pack_md`
    // (see `interview_agents/g3_io.upload_prep_pack`). The "Pack" CTA
    // points at `prep_pack_url` when present; otherwise we expose an
    // inline preview so the user is never stuck without the content.
    const hasUrl = !!interviewPrep.prep_pack_url
    const hasMd = !!interviewPrep.prep_pack_md
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
            {hasUrl && (
              <a
                href={interviewPrep.prep_pack_url!}
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
        {!hasUrl && hasMd && (
          <details className="mt-3 rounded-md border border-border bg-surface p-3">
            <summary className="cursor-pointer text-2xs font-medium text-fg-muted hover:text-fg select-none">
              Storage upload unavailable — view inline (
              {(interviewPrep.prep_pack_md!.length / 1024).toFixed(1)} KB)
            </summary>
            <pre className="mt-2 text-2xs text-fg-muted leading-relaxed whitespace-pre-wrap font-mono bg-surface-raised border border-border rounded p-2 max-h-96 overflow-y-auto">
              {interviewPrep.prep_pack_md!.slice(0, 8000)}
              {interviewPrep.prep_pack_md!.length > 8000 ? '\n\n... (truncated)' : ''}
            </pre>
          </details>
        )}
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
        description="G3 builds a prep pack tailored to this role's interview format — likely questions, STAR stories from your bank, and competency gaps to anticipate. Runs in the background (~3 min, ~$0.50); you can leave this tab."
        action={
          <Button
            variant="primary"
            size="md"
            onClick={handleKickoff}
            loading={busy}
            disabled={busy}
          >
            <Icon name="rocket" size={14} />
            {busy ? 'Building…' : 'Generate prep pack'}
          </Button>
        }
        hint={
          errorMsg ? (
            <span className="text-danger">{errorMsg}</span>
          ) : busy && statusMsg ? (
            <span className="text-fg-muted">{statusMsg}</span>
          ) : statusMsg ? (
            <span className="text-success">{statusMsg}</span>
          ) : undefined
        }
      />
    </Card>
  )
}

export default InterviewPrepTab
