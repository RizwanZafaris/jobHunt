/**
 * IntroDraftModal — modal that calls a stub `draftIntro(path)` action,
 * displays the generated email with subject + body + tone notes, and
 * exposes "Copy" + "Open in mail client" buttons.
 *
 * Loading and error states are handled gracefully; on `cost_capped` /
 * `draft_failed` we render a quiet inline error and let the user retry.
 *
 * The `draftIntro` action is intentionally a prop here so the page can
 * stub it (mock) or wire it to /network/draft-intro once that endpoint
 * lands in api/network.py. Keep this component pure-presentational.
 */
'use client'

import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Pill } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'
import type { IntroDraft, ReferralPath } from '@/lib/types/network'

export interface IntroDraftModalProps {
  open: boolean
  path: ReferralPath | null
  draftIntro: (path: ReferralPath) => Promise<IntroDraft>
  onClose: () => void
}

type Status = 'idle' | 'loading' | 'ready' | 'error'

export function IntroDraftModal({ open, path, draftIntro, onClose }: IntroDraftModalProps) {
  const [status, setStatus] = useState<Status>('idle')
  const [draft, setDraft] = useState<IntroDraft | null>(null)
  const [errMsg, setErrMsg] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  const closeBtnRef = useRef<HTMLButtonElement>(null)

  // Kick off the draft when the modal opens with a path.
  useEffect(() => {
    if (!open || !path) return
    let cancelled = false
    setStatus('loading')
    setDraft(null)
    setErrMsg(null)
    setCopied(false)
    draftIntro(path)
      .then((d) => {
        if (cancelled) return
        if (d.error) {
          setStatus('error')
          setErrMsg(
            d.error === 'cost_capped'
              ? `Cost cap reached ($${(d.cost_usd ?? 0).toFixed(3)} > $${(d as { max_cost_usd?: number }).max_cost_usd ?? 0.05}). Try again later.`
              : (d.detail ?? 'Drafting failed. Try again in a moment.'),
          )
          setDraft(null)
        } else {
          setDraft(d)
          setStatus('ready')
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setStatus('error')
        setErrMsg(err instanceof Error ? err.message : 'Drafting failed.')
      })
    return () => {
      cancelled = true
    }
  }, [open, path, draftIntro])

  // Focus the close button on open for keyboard users.
  useEffect(() => {
    if (open) closeBtnRef.current?.focus()
  }, [open, status])

  // Esc to close.
  useEffect(() => {
    if (!open) return
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open || !path) return null

  const target = path.target_employee
  const targetCompany = path.target_company_name ?? target.current_company ?? '—'
  const introducer = path.path[1]

  async function handleCopy() {
    if (!draft) return
    const text = draft.subject ? `${draft.subject}\n\n${draft.body}` : draft.body
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      // Older browsers — fall back to opening a textarea is overkill for V1.
      setErrMsg('Could not copy to clipboard. Select the text manually.')
    }
  }

  function handleOpenMail() {
    if (!draft) return
    const subject = encodeURIComponent(draft.subject ?? '')
    const body = encodeURIComponent(draft.body ?? '')
    // We don't know the introducer's email; the user fills it in client-side.
    // mailto:?subject=...&body=... pops the default client with our copy.
    window.location.href = `mailto:?subject=${subject}&body=${body}`
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="intro-draft-title"
      className="fixed inset-0 z-50 flex items-center justify-center px-4 py-6"
    >
      {/* backdrop */}
      <button
        type="button"
        aria-label="Close intro draft"
        onClick={onClose}
        className="absolute inset-0 bg-fg/40 backdrop-blur-sm"
      />
      {/* panel */}
      <div className="relative w-full max-w-xl bg-surface border border-border rounded-lg shadow-lg overflow-hidden flex flex-col max-h-[90vh]">
        <header className="flex items-start justify-between gap-3 px-5 pt-5 pb-3 border-b border-border">
          <div className="min-w-0 flex-1">
            <p className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1">
              Warm intro draft
            </p>
            <h2 id="intro-draft-title" className="text-base font-semibold text-fg leading-snug">
              {target.full_name} <span className="text-fg-muted font-normal">·</span>{' '}
              <span className="text-fg-muted">{targetCompany}</span>
            </h2>
            <p className="text-xs text-fg-muted mt-1 leading-relaxed">
              {path.hop_count === 1
                ? 'Direct first-degree connection.'
                : introducer
                ? `Via ${introducer.full_name}${introducer.headline ? ` · ${introducer.headline}` : ''}`
                : `${path.hop_count}-hop path.`}
            </p>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex items-center justify-center w-9 h-9 rounded-md text-fg-muted hover:text-fg hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <Icon name="x" size={16} />
          </button>
        </header>

        <div className="overflow-y-auto px-5 py-4 space-y-4 flex-1">
          {status === 'loading' && (
            <div className="space-y-2" role="status" aria-live="polite">
              <p className="text-xs text-fg-muted">Drafting a warm-intro email — usually under 4s.</p>
              <div className="h-3 bg-surface-raised rounded animate-pulse w-3/4" />
              <div className="h-3 bg-surface-raised rounded animate-pulse w-full" />
              <div className="h-3 bg-surface-raised rounded animate-pulse w-5/6" />
              <div className="h-3 bg-surface-raised rounded animate-pulse w-2/3" />
            </div>
          )}

          {status === 'error' && (
            <div
              role="alert"
              className="rounded-md border border-danger-border bg-danger-bg/60 px-3 py-2.5 text-xs text-danger leading-relaxed"
            >
              <strong className="font-semibold">Couldn&apos;t draft this email.</strong>{' '}
              {errMsg ?? 'Try again in a moment.'}
            </div>
          )}

          {status === 'ready' && draft && (
            <>
              {draft.subject && (
                <div>
                  <p className="text-2xs font-medium uppercase tracking-wider text-fg-subtle mb-1">
                    Subject
                  </p>
                  <p className="text-sm font-semibold text-fg leading-snug">{draft.subject}</p>
                </div>
              )}
              <div>
                <p className="text-2xs font-medium uppercase tracking-wider text-fg-subtle mb-1">
                  Body ({draft.length_words} words)
                </p>
                <pre className="text-xs text-fg leading-relaxed whitespace-pre-wrap font-sans bg-surface-raised border border-border rounded-md p-3">
                  {draft.body}
                </pre>
              </div>
              {draft.tone_notes && (
                <div className="flex items-start gap-2">
                  <Pill tone="info" size="xs">
                    Tone
                  </Pill>
                  <p className="text-2xs text-fg-muted leading-relaxed flex-1">
                    {draft.tone_notes}
                  </p>
                </div>
              )}
              {typeof draft.cost_usd === 'number' && (
                <p className="text-2xs text-fg-subtle tnum">
                  Drafted with {draft.model ?? 'claude-opus-4-7'} ·{' '}
                  ${draft.cost_usd.toFixed(4)}
                </p>
              )}
            </>
          )}
        </div>

        <footer className="border-t border-border px-5 py-3 flex flex-wrap items-center justify-between gap-2 bg-surface">
          <div className="flex items-center gap-2 text-2xs text-fg-subtle">
            {copied && (
              <span role="status" className="inline-flex items-center gap-1 text-success">
                <Icon name="check" size={12} /> Copied
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleCopy}
              disabled={status !== 'ready'}
            >
              Copy
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleOpenMail}
              disabled={status !== 'ready'}
            >
              Open in mail
            </Button>
          </div>
        </footer>
      </div>
    </div>
  )
}

export default IntroDraftModal
