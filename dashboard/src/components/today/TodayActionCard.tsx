/**
 * TodayActionCard — single ranked action on the /today surface.
 *
 * Layout: state colour bar on the left edge, body in the middle, actions
 * stacked right. Accent strip uses semantic tokens so it theme-shifts.
 *
 * Interaction handlers ('copy' | 'kickoff_g2' | 'log_outcome') are stubs
 * pending the real endpoints. Until those ship the click just prompts
 * via the live region.
 *
 * TODO: wire onClick handlers to real endpoints — see _pending_endpoints.md
 */
'use client'

import Link from 'next/link'
import { useState } from 'react'
import { clsx } from 'clsx'
import { Icon, IconName } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import type {
  TodayAction,
  TodayActionKind,
  TodayActionState,
} from '@/lib/types/today'

// 2026-05-26 stale-card fix — dismiss reasons match server-side enum
// (api/actions.py::_VALID_REASONS). Adding/removing one here requires
// the matching server change too.
type DismissReason =
  | 'not_interested'
  | 'maybe_later'
  | 'wrong_seniority'
  | 'wrong_location'
  | 'wrong_comp'
  | 'closed_already'
  | 'other'

interface DismissOption {
  label: string
  reason: DismissReason
  snoozeDays?: number  // omit for permanent dismissal
}

const DISMISS_OPTIONS: DismissOption[] = [
  { label: 'Not interested', reason: 'not_interested' },
  { label: 'Snooze 3 days',  reason: 'maybe_later', snoozeDays: 3 },
  { label: 'Snooze 1 week',  reason: 'maybe_later', snoozeDays: 7 },
  { label: 'Wrong seniority', reason: 'wrong_seniority' },
  { label: 'Wrong location',  reason: 'wrong_location' },
  { label: 'Already closed',  reason: 'closed_already' },
]

async function dismissJob(
  jobId: number,
  reason: DismissReason,
  snoozeDays?: number,
): Promise<void> {
  const r = await fetch(`/api/proxy/actions/today/dismiss/${jobId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, snooze_days: snoozeDays ?? null }),
  })
  if (!r.ok) {
    const body = await r.text().catch(() => '')
    throw new Error(`dismiss failed: ${r.status} ${body}`)
  }
}

function ageDays(iso?: string): number | null {
  if (!iso) return null
  const created = new Date(iso).getTime()
  if (Number.isNaN(created)) return null
  return Math.max(0, Math.floor((Date.now() - created) / (1000 * 60 * 60 * 24)))
}

const STATE_BAR_CLS: Record<TodayActionState, string> = {
  ready: 'bg-success',
  blocked: 'bg-warning',
  stale: 'bg-fg-subtle',
  pending: 'bg-info',
}

const STATE_LABEL: Record<TodayActionState, string> = {
  ready: 'Ready',
  blocked: 'Blocked',
  stale: 'Stale',
  pending: 'Pending',
}

const STATE_PILL_TONE: Record<TodayActionState, 'success' | 'warning' | 'neutral' | 'info'> = {
  ready: 'success',
  blocked: 'warning',
  stale: 'neutral',
  pending: 'info',
}

const KIND_ICON: Record<TodayActionKind, IconName> = {
  resume_ready: 'rocket',
  score_high_no_resume: 'sparkles',
  score_below_threshold: 'alert-triangle',
  stale_application: 'mail',
  persona_stale: 'refresh',
  linkedin_post_due: 'note',
  // 2026-05-26 hotfix: PR #134 extended TodayActionKind with two new
  // follow-up kinds for the sectioned dashboard, but missed updating
  // this Record-exhaustive map → Vercel build #dpl_ZbJMmSWNHFAYAAeNkJV7wKc8iXzu
  // failed with "Type ... is missing the following properties: follow_up_overdue,
  // follow_up_urgent". Both render with alert-triangle (tone differentiates).
  follow_up_overdue: 'alert-triangle',
  follow_up_urgent: 'alert-triangle',
}

// BUG-021: the "company" chip on /today is rendered in uppercase. When
// upstream extractors mis-classified a free-text field, values like
// "UK MAY 2026" or "REMOTE · LONDON" leaked into the chip and looked
// like a category label. Drop the chip when the value smells like a
// location/date fragment rather than a real company name.
const MONTH_TOKENS = /\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b/
const COUNTRY_LOCATION_TOKENS = /\b(UK|US|USA|EU|EMEA|APAC|REMOTE|HYBRID|ONSITE)\b/

function isUntrustworthyCompanyChip(value: string): boolean {
  const v = value.trim()
  if (!v) return true
  // Pure 4-digit year → definitely not a company name
  if (/^\d{4}$/.test(v)) return true
  // ALL-CAPS short token that contains a month or location marker
  if (v === v.toUpperCase() && (MONTH_TOKENS.test(v) || COUNTRY_LOCATION_TOKENS.test(v))) return true
  return false
}

function handleStubClick(kind: TodayAction['primary']['onClick']) {
  // TODO: replace with real handlers (POST /jobs/:id/build, POST /applications/:id/outcome, copy-to-clipboard)
  if (typeof window === 'undefined') return
  const message = kind === 'copy'
    ? 'Copy handler not yet wired — see _pending_endpoints.md'
    : kind === 'kickoff_g2'
    ? 'G2 kickoff endpoint not yet wired — see _pending_endpoints.md'
    : kind === 'log_outcome'
    ? 'Outcome logger endpoint not yet wired — see _pending_endpoints.md'
    : 'Handler not yet wired'
  // Use alert for now — replace with toast/LiveRegion when those land.
  // eslint-disable-next-line no-alert
  window.alert(message)
}

export interface TodayActionCardProps {
  action: TodayAction
}

export function TodayActionCard({ action }: TodayActionCardProps) {
  const { kind, title, subtitle, state, primary, secondary, meta } = action
  const iconName = KIND_ICON[kind]

  // 2026-05-26 stale-card fix UX state
  const [dismissOpen, setDismissOpen] = useState(false)
  const [dismissing, setDismissing] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [dismissError, setDismissError] = useState<string | null>(null)

  const jobId = meta?.jobId
  const isDismissibleKind =
    kind === 'resume_ready' ||
    kind === 'score_high_no_resume' ||
    kind === 'score_below_threshold'
  const showDismiss = isDismissibleKind && jobId !== undefined

  const days = ageDays(meta?.createdAt)
  const showAgePill = days !== null && days >= 3
  const showSurfacePill =
    meta?.surfaceCount !== undefined && meta.surfaceCount >= 3

  async function handleDismiss(opt: DismissOption) {
    if (!jobId || dismissing) return
    setDismissing(true)
    setDismissError(null)
    try {
      await dismissJob(jobId, opt.reason, opt.snoozeDays)
      setDismissed(true)
      setDismissOpen(false)
    } catch (e) {
      setDismissError(e instanceof Error ? e.message : 'dismiss failed')
    } finally {
      setDismissing(false)
    }
  }

  // Optimistic hide after successful dismissal — the card stays in the
  // server response until the user's next /today fetch, but they don't
  // need to see it again right now.
  if (dismissed) {
    return null
  }

  return (
    <article
      aria-labelledby={`action-${action.id}-title`}
      className={clsx(
        'relative flex items-stretch gap-4 rounded-lg border border-border bg-surface shadow-sm overflow-hidden',
        'hover:border-border-strong transition-colors',
      )}
    >
      {/* State colour bar — semantic edge indicator */}
      <span
        aria-hidden
        className={clsx('w-1.5 shrink-0', STATE_BAR_CLS[state])}
      />

      {/* Body */}
      <div className="flex-1 min-w-0 py-4 pr-4 pl-1 sm:pl-2 flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <div className="inline-flex items-center justify-center w-8 h-8 rounded-md bg-surface-raised text-fg-muted shrink-0 mt-0.5">
            <Icon name={iconName} size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <Pill tone={STATE_PILL_TONE[state]} size="xs">
                {STATE_LABEL[state]}
              </Pill>
              {meta?.company && !isUntrustworthyCompanyChip(meta.company) && (
                <span className="text-2xs text-fg-subtle font-medium tracking-wide uppercase">
                  {meta.company}
                </span>
              )}
              {meta?.score !== undefined && (
                <span className="text-2xs text-fg-subtle tnum">
                  Score <span className="text-fg-muted font-semibold">{meta.score}</span>
                </span>
              )}
              {meta?.letterGrade && (
                /* Phase 2 §4.1 — G5 letter grade badge. Colour-coded by
                 * grade so A/B reads as positive, D/F reads as warning.
                 * Title gives the user a hover hint that this is the
                 * dimensional-breakdown grade, distinct from the
                 * legacy single-number match_score. */
                <span
                  title={`Fit grade ${meta.letterGrade} (G5 dimensional breakdown)`}
                  className={clsx(
                    'inline-flex items-center justify-center w-5 h-5 rounded text-2xs font-bold leading-none tabular-nums',
                    meta.letterGrade === 'A' && 'bg-success/15 text-success',
                    meta.letterGrade === 'B' && 'bg-info/15 text-info',
                    meta.letterGrade === 'C' && 'bg-surface-raised text-fg-muted',
                    meta.letterGrade === 'D' && 'bg-warning/15 text-warning',
                    meta.letterGrade === 'F' && 'bg-danger/15 text-danger',
                  )}
                >
                  {meta.letterGrade}
                </span>
              )}
              {meta?.date && (
                <span className="text-2xs text-fg-subtle tnum">{meta.date}</span>
              )}
              {/* 2026-05-26 stale-card fix — visible "shown for N days"
                 + "surfaced N×" pills give the user a hint of why a card
                 is sinking down /today. */}
              {showAgePill && (
                <span
                  title={`Job first surfaced ${days} day${days === 1 ? '' : 's'} ago. Older cards rank lower on /today.`}
                  className="text-2xs text-fg-subtle tnum"
                >
                  {days}d
                </span>
              )}
              {showSurfacePill && (
                <span
                  title={`Shown ${meta?.surfaceCount}× without action. Repeatedly-shown cards rank lower.`}
                  className="text-2xs text-fg-subtle tnum"
                >
                  ×{meta?.surfaceCount}
                </span>
              )}
            </div>
            <h3
              id={`action-${action.id}-title`}
              className="text-sm font-semibold text-fg leading-snug"
            >
              {title}
            </h3>
            {subtitle && (
              <p className="text-xs text-fg-muted mt-1 leading-relaxed">{subtitle}</p>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0 sm:ml-auto relative">
          {/* 2026-05-26 stale-card fix — dismiss / snooze popover.
              Only shown for job kinds with a jobId in meta. */}
          {showDismiss && (
            <>
              <button
                type="button"
                aria-label="Dismiss this card"
                title="Not interested / snooze"
                onClick={() => setDismissOpen(v => !v)}
                disabled={dismissing}
                className="inline-flex items-center justify-center w-7 h-7 rounded-md text-fg-subtle hover:text-fg hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
              >
                <Icon name="x" size={14} />
              </button>
              {dismissOpen && (
                <div
                  role="menu"
                  className="absolute right-0 top-9 z-10 w-48 rounded-md border border-border bg-surface shadow-lg p-1"
                >
                  {DISMISS_OPTIONS.map(opt => (
                    <button
                      key={`${opt.reason}-${opt.snoozeDays ?? 'perm'}`}
                      type="button"
                      role="menuitem"
                      onClick={() => handleDismiss(opt)}
                      disabled={dismissing}
                      className="block w-full text-left text-xs px-2 py-1.5 rounded hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
                    >
                      {opt.label}
                    </button>
                  ))}
                  {dismissError && (
                    <p className="text-2xs text-danger px-2 py-1">{dismissError}</p>
                  )}
                </div>
              )}
            </>
          )}
          {secondary && (secondary.href ? (
            <Link
              href={secondary.href}
              className="inline-flex items-center text-2xs font-medium text-fg-muted hover:text-fg px-2 py-1 rounded-md hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors"
            >
              {secondary.label}
            </Link>
          ) : null)}
          {primary.href ? (
            <Link
              href={primary.href}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors min-h-9"
            >
              {primary.label}
              <Icon name="arrow-right" size={12} />
            </Link>
          ) : (
            <button
              type="button"
              onClick={() => handleStubClick(primary.onClick)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors min-h-9"
            >
              {primary.label}
              <Icon name="arrow-right" size={12} />
            </button>
          )}
        </div>
      </div>
    </article>
  )
}

export default TodayActionCard
