/**
 * TodayActionSection — one dedicated region per card kind on /today.
 *
 * Replaces the old single flat ranked list. Each section has:
 *   • Header: icon + label + count + tone-coloured accent
 *   • Subtitle: one-line "why this matters"
 *   • Body: top N cards in this kind, ranked by _effective_score
 *   • Empty state: shown when count_total === 0
 *   • Collapsible: state persisted to localStorage per kind
 *   • "Show all N" link when has_more
 *
 * Data comes from /actions/today/sections (api/actions.py).
 */
'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { Icon, type IconName } from '@/components/ui/Icon'
import { TodayActionCard } from './TodayActionCard'
import type { TodaySection, TodaySectionTone } from '@/lib/types/today'

const STORAGE_KEY_PREFIX = 'today:section-collapsed:'

const TONE_BG: Record<TodaySectionTone, string> = {
  danger:  'bg-danger/5  border-danger/30',
  warning: 'bg-warning/5 border-warning/30',
  info:    'bg-info/5    border-info/30',
  success: 'bg-success/5 border-success/30',
  neutral: 'bg-surface   border-border',
}

const TONE_ICON_BG: Record<TodaySectionTone, string> = {
  danger:  'bg-danger/15  text-danger',
  warning: 'bg-warning/15 text-warning',
  info:    'bg-info/15    text-info',
  success: 'bg-success/15 text-success',
  neutral: 'bg-surface-raised text-fg-muted',
}

const TONE_COUNT_BG: Record<TodaySectionTone, string> = {
  danger:  'bg-danger/20  text-danger',
  warning: 'bg-warning/20 text-warning',
  info:    'bg-info/20    text-info',
  success: 'bg-success/20 text-success',
  neutral: 'bg-surface-raised text-fg-muted',
}

export interface TodayActionSectionProps {
  section: TodaySection
  /** Hide the entire section when zero cards. Default false — empty
   *  sections render with their empty_state copy so the user always
   *  sees the same 8 regions in the same order. */
  hideWhenEmpty?: boolean
}

export function TodayActionSection({ section, hideWhenEmpty = false }: TodayActionSectionProps) {
  const { kind, label, subtitle, icon, tone, cards, count_total, has_more, empty_state } = section
  const [collapsed, setCollapsed] = useState<boolean>(false)
  const [hydrated, setHydrated] = useState(false)

  // Read collapse state from localStorage on mount. Per-kind so state
  // doesn't bleed across sections.
  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const stored = window.localStorage.getItem(`${STORAGE_KEY_PREFIX}${kind}`)
      if (stored === '1') setCollapsed(true)
    } catch {
      /* localStorage disabled — ignore */
    }
    setHydrated(true)
  }, [kind])

  function toggleCollapsed() {
    const next = !collapsed
    setCollapsed(next)
    try {
      window.localStorage.setItem(`${STORAGE_KEY_PREFIX}${kind}`, next ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  if (hideWhenEmpty && count_total === 0) return null

  const isEmpty = count_total === 0
  const headingId = `section-${kind}-heading`

  return (
    <section
      aria-labelledby={headingId}
      className={clsx(
        'rounded-lg border overflow-hidden',
        TONE_BG[tone],
      )}
    >
      {/* Header — clickable to collapse */}
      <button
        type="button"
        onClick={toggleCollapsed}
        aria-expanded={!collapsed}
        aria-controls={`section-${kind}-body`}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-surface-raised/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors"
      >
        <span
          aria-hidden
          className={clsx(
            'inline-flex items-center justify-center w-8 h-8 rounded-md shrink-0',
            TONE_ICON_BG[tone],
          )}
        >
          <Icon name={icon as IconName} size={16} />
        </span>
        <div className="min-w-0 flex-1 text-left">
          <div className="flex items-center gap-2">
            <h2
              id={headingId}
              className="text-sm font-semibold text-fg leading-tight"
            >
              {label}
            </h2>
            {count_total > 0 && (
              <span
                className={clsx(
                  'inline-flex items-center justify-center min-w-5 h-5 px-1.5 rounded-full text-2xs font-bold tabular-nums',
                  TONE_COUNT_BG[tone],
                )}
              >
                {count_total}
              </span>
            )}
          </div>
          <p className="text-2xs text-fg-muted mt-0.5 leading-relaxed">
            {subtitle}
          </p>
        </div>
        <span
          aria-hidden
          className="shrink-0 text-fg-subtle"
          // Suppress the chevron until hydrated to avoid SSR mismatch
          style={{ visibility: hydrated ? 'visible' : 'hidden' }}
        >
          <Icon name={collapsed ? 'chevron-right' : 'chevron-down'} size={14} />
        </span>
      </button>

      {/* Body */}
      {!collapsed && (
        <div
          id={`section-${kind}-body`}
          className="px-4 pb-4 pt-1 flex flex-col gap-2"
        >
          {isEmpty ? (
            <p className="text-xs text-fg-subtle italic px-1 py-2">{empty_state}</p>
          ) : (
            <>
              <ol className="flex flex-col gap-2" aria-label={`${label} actions`}>
                {cards.map((a) => (
                  <li key={a.id}>
                    <TodayActionCard action={a} />
                  </li>
                ))}
              </ol>
              {has_more && (
                <div className="pt-1">
                  <Link
                    href={hrefForKind(kind)}
                    className="inline-flex items-center gap-1 text-2xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
                  >
                    Show all {count_total}
                    <Icon name="arrow-right" size={10} />
                  </Link>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}

/** Deep-link target for "Show all N" per kind. Falls back to /applications. */
function hrefForKind(kind: string): string {
  switch (kind) {
    case 'follow_up_overdue':
    case 'follow_up_urgent':
      return '/applications?filter=follow_up'
    case 'linkedin_post_due':
      return '/linkedin'
    case 'persona_stale':
      return '/insights?tab=personas'
    case 'stale_application':
      return '/applications?status=applied'
    case 'resume_ready':
    case 'score_high_no_resume':
    case 'score_below_threshold':
    default:
      return '/applications'
  }
}

export default TodayActionSection
