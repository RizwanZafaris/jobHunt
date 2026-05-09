/**
 * ResumeWorkspace — view / edit / download / rate a generated resume.
 *
 * Closes the loop after a G2 graph build. Loads the build record + its
 * markdown content via /resume-builds/* endpoints (proxy), then surfaces:
 *
 *   - Tabs: Resume · Cover Email · Build Info
 *   - Resume tab: rendered markdown preview, Edit toggle, Save, Copy, Download .md
 *   - Cover Email tab: rendered cover_email_md
 *   - Build Info tab: status, iters, cost, latency, error
 *   - Feedback panel: 1-5 stars + free text, persists to user_rating/user_feedback
 *
 * a11y:
 *   - Tabs use role=tablist / role=tab + aria-selected + arrow-key nav
 *   - Star rating is a radiogroup with arrow-key nav + aria-pressed pills
 *   - Save status announced via LiveRegion
 *   - Edit textarea has a real <label htmlFor>
 */
'use client'

import { KeyboardEvent, useCallback, useEffect, useId, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Pill, type PillTone } from '@/components/ui/Pill'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { EmptyState } from '@/components/ui/EmptyState'

interface ResumeBuild {
  id: string
  job_id: number
  company_name: string | null
  status: string
  iterations: number | null
  cost_usd_total: number
  latency_ms_total: number | null
  resume_pdf_url: string | null
  resume_docx_url: string | null
  cover_email_md: string | null
  user_rating: number | null
  user_feedback: string | null
  user_edited_md: string | null
  user_edited_at: string | null
  created_at: string | null
  finalized_at: string | null
  error: string | null
}

interface MarkdownPayload {
  markdown: string
  source: 'user_edit' | 'storage' | 'missing'
  byte_size: number
  fetch_status?: number
  error?: string
}

type Tab = 'resume' | 'cover' | 'info'

const STATUS_TONE: Record<string, PillTone> = {
  converged:    'success',
  cost_capped:  'warning',
  exhausted:    'warning',
  running:      'info',
  failed:       'danger',
}

const TEXTAREA_BASE =
  'w-full bg-surface text-fg placeholder:text-fg-subtle border border-border-strong rounded-md px-3 py-2 text-xs font-mono ' +
  'focus-visible:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 ' +
  'disabled:opacity-50 transition-colors'

export interface ResumeWorkspaceProps {
  jobId: number
  jobTitle: string
  company: string
}

export default function ResumeWorkspace({ jobId, jobTitle, company }: ResumeWorkspaceProps) {
  const [builds, setBuilds] = useState<ResumeBuild[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [build, setBuild] = useState<ResumeBuild | null>(null)
  const [markdown, setMarkdown] = useState<string>('')
  const [mdSource, setMdSource] = useState<MarkdownPayload['source'] | 'loading'>('loading')

  const [tab, setTab] = useState<Tab>('resume')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string>('')
  const [saving, setSaving] = useState(false)
  const [savingFeedback, setSavingFeedback] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const [rating, setRating] = useState<number | null>(null)
  const [feedback, setFeedback] = useState<string>('')

  const tabsId = useId()
  const editorId = useId()
  const feedbackId = useId()

  // ─── Load build list ────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false
    fetch(`/api/proxy/resume-builds/by-job/${jobId}`, { cache: 'no-store' })
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return
        const list: ResumeBuild[] = data?.builds || []
        setBuilds(list)
        // Default-select the most recent CONVERGED build, else most recent
        const converged = list.find((b) => b.status === 'converged')
        const target = converged?.id || list[0]?.id || null
        setSelectedId(target)
      })
      .catch((err) => {
        if (!cancelled) setError(`Failed to load builds: ${err}`)
      })
    return () => {
      cancelled = true
    }
  }, [jobId])

  // ─── Load selected build details + markdown ─────────────────────────
  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    setMdSource('loading')
    setMarkdown('')
    setBuild(null)

    Promise.all([
      fetch(`/api/proxy/resume-builds/${selectedId}`, { cache: 'no-store' }).then((r) => r.json()),
      fetch(`/api/proxy/resume-builds/${selectedId}/markdown`, { cache: 'no-store' }).then((r) => r.json()),
    ])
      .then(([buildRow, mdPayload]: [ResumeBuild, MarkdownPayload]) => {
        if (cancelled) return
        setBuild(buildRow)
        setMarkdown(mdPayload?.markdown || '')
        setMdSource(mdPayload?.source || 'missing')
        setRating(buildRow?.user_rating ?? null)
        setFeedback(buildRow?.user_feedback ?? '')
        setEditing(false)
        setDraft('')
      })
      .catch((err) => {
        if (!cancelled) setError(`Failed to load build: ${err}`)
      })
    return () => {
      cancelled = true
    }
  }, [selectedId])

  // ─── Save edits ────────────────────────────────────────────────────
  const onStartEdit = useCallback(() => {
    setDraft(markdown)
    setEditing(true)
    setStatusMsg('')
  }, [markdown])

  const onCancelEdit = useCallback(() => {
    setEditing(false)
    setDraft('')
    setStatusMsg('')
  }, [])

  const onSaveEdit = useCallback(async () => {
    if (!selectedId) return
    if (!draft.trim()) {
      setError('Resume cannot be empty.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const res = await fetch(`/api/proxy/resume-builds/${selectedId}/markdown`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_edited_md: draft }),
      })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`)
      }
      setMarkdown(draft)
      setMdSource('user_edit')
      setEditing(false)
      setStatusMsg('Saved')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }, [selectedId, draft])

  // ─── Save feedback ─────────────────────────────────────────────────
  const onSaveFeedback = useCallback(async () => {
    if (!selectedId) return
    if (rating == null && !feedback.trim()) {
      setError('Add a rating or feedback before saving.')
      return
    }
    setSavingFeedback(true)
    setError(null)
    try {
      const res = await fetch(`/api/proxy/resume-builds/${selectedId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_rating: rating,
          user_feedback: feedback.trim() || null,
        }),
      })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`)
      }
      const updated: ResumeBuild = await res.json()
      setBuild(updated)
      setStatusMsg('Feedback saved')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSavingFeedback(false)
    }
  }, [selectedId, rating, feedback])

  // ─── Copy / Download ──────────────────────────────────────────────
  const onCopyMarkdown = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(markdown)
      setStatusMsg('Copied resume markdown to clipboard')
    } catch {
      setStatusMsg('Copy failed — your browser blocked clipboard access')
    }
  }, [markdown])

  const downloadHref = useMemo(() => {
    if (!selectedId) return '#'
    return `/api/proxy/resume-builds/${selectedId}/download?fmt=md`
  }, [selectedId])

  // ─── Tab key nav ──────────────────────────────────────────────────
  const TAB_ORDER: Tab[] = ['resume', 'cover', 'info']
  const onTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>, current: Tab) => {
    const idx = TAB_ORDER.indexOf(current)
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      setTab(TAB_ORDER[(idx + 1) % TAB_ORDER.length])
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      setTab(TAB_ORDER[(idx - 1 + TAB_ORDER.length) % TAB_ORDER.length])
    }
  }

  // ─── Star rating key nav ──────────────────────────────────────────
  const onRatingKey = (e: KeyboardEvent<HTMLButtonElement>, val: number) => {
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
      e.preventDefault()
      setRating((prev) => Math.min(5, (prev ?? val) + 1))
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
      e.preventDefault()
      setRating((prev) => Math.max(1, (prev ?? val) - 1))
    }
  }

  // ─── Render guards ────────────────────────────────────────────────
  if (builds === null) {
    return (
      <Card>
        <p className="text-fg-muted text-sm">Loading resume builds…</p>
      </Card>
    )
  }
  if (builds.length === 0) {
    return (
      <EmptyState
        title="No resume built yet"
        description={`Click "Generate Resume" on the job page to kick off a G2 build for ${jobTitle} @ ${company}. Each build runs ~$1 and 5 minutes.`}
      />
    )
  }
  if (!build) {
    return (
      <Card>
        <p className="text-fg-muted text-sm">Loading build…</p>
      </Card>
    )
  }

  const statusTone = STATUS_TONE[build.status] || 'neutral'
  const isMissing = mdSource === 'missing' && !markdown
  const isUserEdited = build.user_edited_md != null

  return (
    <div className="flex flex-col gap-4">
      {/* Build picker + status */}
      <Card padding="md">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
              Build
            </span>
            {builds.length === 1 ? (
              <span className="text-fg text-sm font-medium">
                {build.id.slice(0, 8)}
              </span>
            ) : (
              <select
                value={selectedId ?? ''}
                onChange={(e) => setSelectedId(e.target.value)}
                className="bg-surface text-fg border border-border-strong rounded-md px-2 py-1 text-xs focus-visible:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30"
                aria-label="Select build"
              >
                {builds.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.id.slice(0, 8)} · {b.status} · ${b.cost_usd_total.toFixed(2)} ·{' '}
                    {b.created_at ? new Date(b.created_at).toLocaleString() : '—'}
                  </option>
                ))}
              </select>
            )}
            <Pill tone={statusTone}>{build.status}</Pill>
            {isUserEdited && <Pill tone="info">edited</Pill>}
            {build.iterations != null && (
              <span className="text-2xs text-fg-subtle">iter {build.iterations}</span>
            )}
            <span className="text-2xs text-fg-subtle">
              ${build.cost_usd_total.toFixed(2)}
            </span>
            {build.latency_ms_total != null && (
              <span className="text-2xs text-fg-subtle">
                {(build.latency_ms_total / 1000).toFixed(1)}s
              </span>
            )}
          </div>
          {build.error && (
            <Pill tone="danger" title={build.error}>
              has error
            </Pill>
          )}
        </div>
      </Card>

      {/* Tabs */}
      <div role="tablist" aria-labelledby={tabsId} className="flex gap-1 border-b border-border">
        {(
          [
            { id: 'resume' as Tab, label: 'Resume' },
            { id: 'cover'  as Tab, label: 'Cover Email' },
            { id: 'info'   as Tab, label: 'Build Info' },
          ] as const
        ).map(({ id, label }) => {
          const active = tab === id
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={`${tabsId}-${id}`}
              tabIndex={active ? 0 : -1}
              onClick={() => setTab(id)}
              onKeyDown={(e) => onTabKeyDown(e, id)}
              className={
                'px-4 py-2 text-2xs font-medium uppercase tracking-wider transition-colors -mb-px ' +
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ' +
                (active
                  ? 'text-fg border-b-2 border-accent'
                  : 'text-fg-subtle hover:text-fg border-b-2 border-transparent')
              }
            >
              {label}
            </button>
          )
        })}
      </div>

      {/* Resume tab */}
      {tab === 'resume' && (
        <div
          id={`${tabsId}-resume`}
          role="tabpanel"
          aria-labelledby={`${tabsId}-resume-tab`}
          className="flex flex-col gap-3"
        >
          {/* Toolbar */}
          <div className="flex items-center gap-2 flex-wrap">
            {!editing ? (
              <>
                <Button variant="primary" size="sm" onClick={onStartEdit} disabled={isMissing}>
                  Edit
                </Button>
                <Button variant="secondary" size="sm" onClick={onCopyMarkdown} disabled={isMissing}>
                  Copy markdown
                </Button>
                <a
                  href={isMissing ? '#' : downloadHref}
                  aria-disabled={isMissing}
                  onClick={(e) => isMissing && e.preventDefault()}
                  download
                  className={
                    'inline-flex items-center justify-center font-medium rounded-md text-2xs h-9 px-3 ' +
                    'bg-surface text-fg hover:bg-surface-raised border border-border-strong ' +
                    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ' +
                    (isMissing ? 'opacity-50 pointer-events-none' : 'cursor-pointer')
                  }
                >
                  Download .md
                </a>
              </>
            ) : (
              <>
                <Button variant="primary" size="sm" onClick={onSaveEdit} disabled={saving}>
                  {saving ? 'Saving…' : 'Save'}
                </Button>
                <Button variant="ghost" size="sm" onClick={onCancelEdit} disabled={saving}>
                  Cancel
                </Button>
              </>
            )}
            <span className="ml-auto text-3xs text-fg-subtle">
              {mdSource === 'user_edit' && 'Showing your edited version'}
              {mdSource === 'storage' && 'Showing the agent draft'}
              {mdSource === 'missing' && 'Markdown unavailable'}
              {mdSource === 'loading' && 'Loading…'}
            </span>
          </div>

          {/* View / Edit */}
          {!editing ? (
            <Card padding="lg">
              {isMissing ? (
                <p className="text-fg-muted text-sm">
                  Resume markdown couldn&apos;t be loaded. The Storage signed URL on
                  this build may have expired or never been written.
                </p>
              ) : (
                <ResumePreview markdown={markdown} />
              )}
            </Card>
          ) : (
            <Card padding="md">
              <label htmlFor={editorId} className="sr-only">
                Edit resume markdown
              </label>
              <textarea
                id={editorId}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                rows={28}
                className={TEXTAREA_BASE}
                aria-describedby={`${editorId}-hint`}
              />
              <p id={`${editorId}-hint`} className="mt-1 text-3xs text-fg-subtle">
                Edits are stored on the build (not the original Storage object) so the
                agent draft remains intact for audit.
              </p>
            </Card>
          )}
        </div>
      )}

      {/* Cover Email tab */}
      {tab === 'cover' && (
        <div
          id={`${tabsId}-cover`}
          role="tabpanel"
          className="flex flex-col gap-3"
        >
          <div className="flex items-center gap-2 flex-wrap">
            <Button
              variant="secondary"
              size="sm"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(build.cover_email_md || '')
                  setStatusMsg('Copied cover email to clipboard')
                } catch {
                  setStatusMsg('Copy failed')
                }
              }}
              disabled={!build.cover_email_md}
            >
              Copy email
            </Button>
          </div>
          <Card padding="lg">
            {build.cover_email_md ? (
              <div className="text-fg whitespace-pre-wrap text-sm leading-relaxed">
                {build.cover_email_md}
              </div>
            ) : (
              <p className="text-fg-muted text-sm">
                No cover email on this build.
              </p>
            )}
          </Card>
        </div>
      )}

      {/* Build Info tab */}
      {tab === 'info' && (
        <div
          id={`${tabsId}-info`}
          role="tabpanel"
          className="flex flex-col gap-3"
        >
          <Card padding="lg">
            <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <InfoRow label="Build ID" value={build.id} mono />
              <InfoRow label="Job" value={`${jobTitle} @ ${company}`} />
              <InfoRow label="Status" value={build.status} />
              <InfoRow label="Iterations" value={String(build.iterations ?? '—')} />
              <InfoRow label="Cost" value={`$${build.cost_usd_total.toFixed(4)}`} />
              <InfoRow
                label="Total latency"
                value={
                  build.latency_ms_total != null
                    ? `${(build.latency_ms_total / 1000).toFixed(1)}s`
                    : '—'
                }
              />
              <InfoRow
                label="Created"
                value={build.created_at ? new Date(build.created_at).toLocaleString() : '—'}
              />
              <InfoRow
                label="Finalized"
                value={build.finalized_at ? new Date(build.finalized_at).toLocaleString() : '—'}
              />
              {build.user_edited_at && (
                <InfoRow
                  label="Last edited by you"
                  value={new Date(build.user_edited_at).toLocaleString()}
                />
              )}
              {build.error && (
                <div className="sm:col-span-2">
                  <dt className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                    Error
                  </dt>
                  <dd className="mt-1 text-danger text-xs whitespace-pre-wrap font-mono">
                    {build.error}
                  </dd>
                </div>
              )}
            </dl>
          </Card>
        </div>
      )}

      {/* Feedback panel — always visible */}
      <Card
        padding="md"
        title="How was this build?"
        description="Your rating + notes feed the persona-evolution Sunday cron. Be specific."
      >
        <div className="flex flex-col gap-3">
          <div
            role="radiogroup"
            aria-label="Rate this build 1 to 5"
            className="flex items-center gap-1"
          >
            {[1, 2, 3, 4, 5].map((n) => {
              const active = (rating ?? 0) >= n
              return (
                <button
                  key={n}
                  type="button"
                  role="radio"
                  aria-checked={rating === n}
                  aria-label={`${n} star${n === 1 ? '' : 's'}`}
                  tabIndex={rating === n || (rating == null && n === 1) ? 0 : -1}
                  onClick={() => setRating(rating === n ? null : n)}
                  onKeyDown={(e) => onRatingKey(e, n)}
                  className={
                    'inline-flex items-center justify-center w-9 h-9 rounded-md border transition-colors ' +
                    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ' +
                    (active
                      ? 'bg-accent text-accent-fg border-accent'
                      : 'bg-surface text-fg-muted border-border-strong hover:bg-surface-raised')
                  }
                >
                  ★
                </button>
              )
            })}
            {rating != null && (
              <button
                type="button"
                onClick={() => setRating(null)}
                className="ml-2 text-3xs text-fg-subtle hover:text-fg underline"
              >
                clear
              </button>
            )}
          </div>

          <div>
            <label htmlFor={feedbackId} className="block text-2xs uppercase tracking-wider text-fg-muted font-medium mb-1">
              What worked, what didn&apos;t
            </label>
            <textarea
              id={feedbackId}
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              rows={3}
              placeholder="e.g. wrong location at top; too many bullets in the Daraz role; cover letter sounds generic…"
              className={TEXTAREA_BASE.replace('font-mono ', '')}
            />
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="primary"
              size="sm"
              onClick={onSaveFeedback}
              disabled={savingFeedback}
            >
              {savingFeedback ? 'Saving…' : 'Save feedback'}
            </Button>
            {build.user_rating != null && (
              <span className="text-3xs text-fg-subtle">
                Last saved: {build.user_rating} ★
              </span>
            )}
          </div>
        </div>
      </Card>

      {error && (
        <div role="alert">
          <Pill tone="danger">{error}</Pill>
        </div>
      )}

      <LiveRegion>{statusMsg}</LiveRegion>
    </div>
  )
}

function InfoRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
        {label}
      </dt>
      <dd className={'mt-0.5 text-fg text-xs ' + (mono ? 'font-mono break-all' : '')}>
        {value}
      </dd>
    </div>
  )
}

/**
 * Simple markdown preview using react-markdown. Styling done inline so we
 * don't need the @tailwindcss/typography plugin.
 */
function ResumePreview({ markdown }: { markdown: string }) {
  return (
    <article className="text-fg text-sm leading-relaxed [&_h1]:text-2xl [&_h1]:font-semibold [&_h1]:mt-0 [&_h1]:mb-2 [&_h1]:text-fg [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-fg [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1.5 [&_h3]:text-fg [&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-2 [&_li]:my-0.5 [&_strong]:font-semibold [&_strong]:text-fg [&_a]:text-accent [&_a:hover]:underline [&_hr]:border-border [&_hr]:my-4">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
    </article>
  )
}
