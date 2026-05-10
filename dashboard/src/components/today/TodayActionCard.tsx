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
import { clsx } from 'clsx'
import { Icon, IconName } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import type {
  TodayAction,
  TodayActionKind,
  TodayActionState,
} from '@/lib/types/today'

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
              {meta?.company && (
                <span className="text-2xs text-fg-subtle font-medium tracking-wide uppercase">
                  {meta.company}
                </span>
              )}
              {meta?.score !== undefined && (
                <span className="text-2xs text-fg-subtle tnum">
                  Score <span className="text-fg-muted font-semibold">{meta.score}</span>
                </span>
              )}
              {meta?.date && (
                <span className="text-2xs text-fg-subtle tnum">{meta.date}</span>
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
        <div className="flex items-center gap-2 shrink-0 sm:ml-auto">
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
