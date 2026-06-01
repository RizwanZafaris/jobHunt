/**
 * GenerateModal — angle picker + optional target company for the
 * "Generate new draft" button on /linkedin.
 *
 * Lightweight inline modal — no portal / focus-trap library yet because
 * the rest of the app doesn't have one. Replace with a primitive when
 * the design system ships its <Dialog /> later.
 *
 * TODO: replace handleSubmit stub with API call to
 *       POST /linkedin/drafts/generate.
 */
'use client'

import type { FormEvent } from 'react'
import { useEffect, useState } from 'react'
import { Button, Icon } from '@/components/ui'
import { ANGLE_LABEL, LINKEDIN_ANGLES, type LinkedInAngle } from '@/lib/types/linkedin'

export interface CompanyOption {
  id: string  // UUID
  name: string
  priority?: string | null  // 'high' | 'medium' | 'low'
}

export interface GenerateModalProps {
  onClose: () => void
  onSubmit: (args: { angle?: LinkedInAngle; targetCompanyId?: string; count: number }) => void | Promise<void>
  /**
   * Optional list of the user's target companies. When provided, the
   * "Target company" field renders as a searchable dropdown of names
   * (submitting the UUID). When absent, falls back to a free-text UUID
   * input (legacy behaviour).
   *
   * 2026-05-14 fix: was a plain text field. User typed "Mastercard"
   * as text → backend filter `.eq("company_id", "Mastercard")` returned
   * 0 rows → graph fell back to all candidates → Marqeta won by data
   * volume + placeholder priming. Real fix is a searchable dropdown
   * keyed by NAME with UUID submitted under the hood.
   */
  companies?: CompanyOption[]
}

export function GenerateModal({ onClose, onSubmit, companies }: GenerateModalProps) {
  const [angle, setAngle] = useState<LinkedInAngle | ''>('')
  const [targetCompanyId, setTargetCompanyId] = useState('')
  const [count, setCount] = useState(1)
  const [busy, setBusy] = useState(false)

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      await onSubmit({
        angle: angle || undefined,
        targetCompanyId: targetCompanyId.trim() || undefined,
        count,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="generate-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-fg/40"
      onClick={(e) => {
        // Close on backdrop click only.
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <form
        onSubmit={submit}
        className="bg-surface border border-border shadow-lg rounded-lg w-full max-w-md max-h-[90vh] overflow-y-auto p-5 flex flex-col gap-4"
      >
        <div className="flex items-center justify-between">
          <h2 id="generate-modal-title" className="text-base font-semibold text-fg">
            Generate new draft
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex items-center justify-center w-8 h-8 rounded-md text-fg-muted hover:text-fg hover:bg-surface-raised"
          >
            <Icon name="x" size={16} />
          </button>
        </div>

        <p className="text-2xs text-fg-muted">
          The G4 graph picks a recent news anchor + an angle the user can credibly take. Override either below if you want a specific shape.
        </p>

        {/* Angle */}
        <label className="flex flex-col gap-1.5">
          <span className="text-2xs font-medium uppercase tracking-wide text-fg-subtle">
            Angle (optional)
          </span>
          <select
            value={angle}
            onChange={(e) => setAngle(e.target.value as LinkedInAngle | '')}
            className="bg-surface-raised border border-border rounded-md px-3 py-2 text-sm text-fg focus:outline-none focus:border-accent"
          >
            <option value="">Auto-pick</option>
            {LINKEDIN_ANGLES.map((a) => (
              <option key={a} value={a}>
                {ANGLE_LABEL[a]}
              </option>
            ))}
          </select>
        </label>

        {/* Target company */}
        <label className="flex flex-col gap-1.5">
          <span className="text-2xs font-medium uppercase tracking-wide text-fg-subtle">
            Target company (optional)
          </span>
          {companies && companies.length > 0 ? (
            <select
              value={targetCompanyId}
              onChange={(e) => setTargetCompanyId(e.target.value)}
              className="bg-surface-raised border border-border rounded-md px-3 py-2 text-sm text-fg focus:outline-none focus:border-accent"
            >
              <option value="">Any (auto-pick from all targets)</option>
              {companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.priority ? ` · ${c.priority}` : ''}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={targetCompanyId}
              onChange={(e) => setTargetCompanyId(e.target.value)}
              placeholder="UUID — companies list unavailable"
              className="bg-surface-raised border border-border rounded-md px-3 py-2 text-sm text-fg placeholder:text-fg-subtle focus:outline-none focus:border-accent"
            />
          )}
          <span className="text-2xs text-fg-subtle">
            Pick a company to pin the news anchor, or leave on Any.
          </span>
        </label>

        {/* Count */}
        <label className="flex flex-col gap-1.5">
          <span className="text-2xs font-medium uppercase tracking-wide text-fg-subtle">
            How many drafts
          </span>
          <select
            value={count}
            onChange={(e) => setCount(Number.parseInt(e.target.value, 10))}
            className="bg-surface-raised border border-border rounded-md px-3 py-2 text-sm text-fg focus:outline-none focus:border-accent"
          >
            {[1, 2, 3].map((n) => (
              <option key={n} value={n}>
                {n} draft{n > 1 ? 's' : ''}
              </option>
            ))}
          </select>
        </label>

        <div className="flex items-center justify-end gap-2 pt-2">
          <Button type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" size="sm" loading={busy}>
            Generate
          </Button>
        </div>
      </form>
    </div>
  )
}

export default GenerateModal
