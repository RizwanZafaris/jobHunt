/**
 * LinkedInClient — interactive shell for /linkedin.
 *
 * The /linkedin page itself is a server component (loads mocks, renders
 * the header). This file is the client wrapper that owns:
 *   - tab state (Drafts | Scheduled | Posted)
 *   - the GenerateModal (kicks off /linkedin/drafts/generate)
 *   - per-card state lifts so optimistic UI works without a fetcher yet
 *
 * Replace the MOCK_* imports + handleGenerate stub with real fetches once
 * api/linkedin.py is wired into api/server.py.
 */
'use client'

import { useMemo, useState } from 'react'
import { clsx } from 'clsx'
import { Button, Icon } from '@/components/ui'
import { DraftCard } from './DraftCard'
import { GenerateModal } from './GenerateModal'
import {
  ANGLE_LABEL,
  DAY_OF_WEEK_LABEL,
  type LinkedInAngle,
  type LinkedInDraft,
  type LinkedInDraftStatus,
  type LinkedInPostingSchedule,
} from '@/lib/types/linkedin'

type TabId = 'drafts' | 'scheduled' | 'posted'

const TABS: { id: TabId; label: string; matches: LinkedInDraftStatus[] }[] = [
  { id: 'drafts', label: 'Drafts', matches: ['draft'] },
  { id: 'scheduled', label: 'Scheduled', matches: ['approved', 'scheduled'] },
  { id: 'posted', label: 'Posted', matches: ['posted', 'expired', 'rejected'] },
]

export interface LinkedInClientProps {
  initialDrafts: LinkedInDraft[]
  schedule: LinkedInPostingSchedule
}

export function LinkedInClient({ initialDrafts, schedule }: LinkedInClientProps) {
  const [drafts, setDrafts] = useState<LinkedInDraft[]>(initialDrafts)
  const [tab, setTab] = useState<TabId>('drafts')
  const [generateOpen, setGenerateOpen] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  const visible = useMemo(() => {
    const tabDef = TABS.find((t) => t.id === tab)
    if (!tabDef) return drafts
    return drafts.filter((d) => tabDef.matches.includes(d.status))
  }, [drafts, tab])

  // Counts shown next to tab labels.
  const tabCounts = useMemo(() => {
    const m: Record<TabId, number> = { drafts: 0, scheduled: 0, posted: 0 }
    for (const d of drafts) {
      for (const t of TABS) {
        if (t.matches.includes(d.status)) m[t.id]++
      }
    }
    return m
  }, [drafts])

  const handleCardChange = (next: LinkedInDraft) => {
    setDrafts((cur) => cur.map((d) => (d.id === next.id ? next : d)))
  }

  // A2/BUG-LinkedIn-Generate fix (2026-05-13): the old stub showed an
  // alert(); user reported "click generate a new post it load with no
  // results no working end to end". Now wired to the real backend.
  const handleGenerate = async (args: {
    angle?: LinkedInAngle
    targetCompanyId?: string
    count: number
  }) => {
    setGenerateOpen(false)
    setFeedback('Generating draft…')
    try {
      const res = await fetch('/api/proxy/linkedin/drafts/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          angle: args.angle,
          count: args.count,
          company_id: args.targetCompanyId,
        }),
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `HTTP ${res.status}`)
      }
      const data = await res.json()
      // BUG-Generate-UX fix (2026-05-13): The user reported clicking
      // Generate and seeing "loaded for a few minutes but no result on
      // screen". Two reasons:
      //   1. G4 draft generation takes ~30-60s (Anthropic + research)
      //      so reload-after-2s shows an empty list (worker still running)
      //   2. The page was reading MOCK_DRAFTS so refresh wouldn't show
      //      anything new anyway (fixed by BUG-LinkedIn-Mock).
      // Set explicit expectation in the banner and refresh later.
      const queued = data.queued ?? args.count
      setFeedback(
        `Queued ${queued} draft${queued === 1 ? '' : 's'}. ` +
        `Generation takes ~30-60s — the new draft will appear here when ready.`,
      )
      // Reload after 45s so the draft typically lands BEFORE refresh,
      // not the other way around. The user can also refresh manually
      // earlier if they're impatient.
      setTimeout(() => {
        if (typeof window !== 'undefined') window.location.reload()
      }, 45_000)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setFeedback(`Failed: ${msg}`)
      setTimeout(() => setFeedback(null), 8000)
    }
  }

  // Group scheduled drafts by day of week for the calendar view.
  const scheduledByDay = useMemo(() => {
    const map = new Map<number, LinkedInDraft[]>()
    for (const d of drafts) {
      if (d.status !== 'scheduled' && d.status !== 'approved') continue
      if (!d.scheduledFor) continue
      const day = new Date(d.scheduledFor).getDay() // JS: Sun=0..Sat=6
      const list = map.get(day) ?? []
      list.push(d)
      map.set(day, list)
    }
    return map
  }, [drafts])

  return (
    <div className="space-y-6">
      {/* A2: Generate-request feedback banner (success / error / loading) */}
      {feedback && (
        <div className="rounded-lg border border-accent bg-surface-raised px-4 py-2.5 text-sm text-fg flex items-center gap-2">
          <Icon name="info" size={14} aria-hidden />
          {feedback}
        </div>
      )}

      {/* Tab bar + Generate CTA */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div role="tablist" aria-label="LinkedIn drafts" className="flex items-center gap-0.5 border-b border-border flex-1">
          {TABS.map((t) => {
            const active = t.id === tab
            return (
              <button
                key={t.id}
                role="tab"
                type="button"
                aria-selected={active}
                onClick={() => setTab(t.id)}
                className={clsx(
                  'inline-flex items-center gap-1.5 px-3.5 py-2.5 text-2xs font-medium -mb-px border-b-2 transition-colors',
                  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                  active
                    ? 'border-accent text-fg'
                    : 'border-transparent text-fg-muted hover:text-fg hover:border-border-strong',
                )}
              >
                {t.label}
                <span className="text-fg-subtle tnum">({tabCounts[t.id]})</span>
              </button>
            )
          })}
        </div>
        <Button variant="primary" size="sm" onClick={() => setGenerateOpen(true)}>
          <Icon name="sparkles" size={12} aria-hidden /> Generate new draft
        </Button>
      </div>

      {/* Tab body */}
      {tab === 'drafts' && (
        <DraftsGrid drafts={visible} onChange={handleCardChange} emptyText="No drafts pending review. Hit Generate to make one." />
      )}
      {tab === 'scheduled' && (
        <ScheduledCalendar
          drafts={visible}
          schedule={schedule}
          byDay={scheduledByDay}
          onChange={handleCardChange}
        />
      )}
      {tab === 'posted' && (
        <DraftsGrid drafts={visible} onChange={handleCardChange} emptyText="No posts yet. Approve a draft to start your archive." />
      )}

      {generateOpen && (
        <GenerateModal
          onClose={() => setGenerateOpen(false)}
          onSubmit={handleGenerate}
        />
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Drafts grid (used by drafts + posted tabs)
// ─────────────────────────────────────────────────────────────────────────
function DraftsGrid({
  drafts,
  onChange,
  emptyText,
}: {
  drafts: LinkedInDraft[]
  onChange: (next: LinkedInDraft) => void
  emptyText: string
}) {
  if (drafts.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-raised p-8 text-center">
        <p className="text-sm text-fg-muted">{emptyText}</p>
      </div>
    )
  }
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      {drafts.map((d) => (
        <DraftCard key={d.id} draft={d} onChange={onChange} />
      ))}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────
// Scheduled calendar (week view)
// ─────────────────────────────────────────────────────────────────────────
function ScheduledCalendar({
  drafts,
  schedule,
  byDay,
  onChange,
}: {
  drafts: LinkedInDraft[]
  schedule: LinkedInPostingSchedule
  byDay: Map<number, LinkedInDraft[]>
  onChange: (next: LinkedInDraft) => void
}) {
  // Render Mon..Sun strip; default schedule is Mon/Wed/Fri.
  const slotDays = new Set(schedule.slots.map((s) => s.dayOfWeek))
  const days = [1, 2, 3, 4, 5, 6, 0] // Mon..Sun

  if (drafts.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-raised p-8 text-center">
        <p className="text-sm text-fg-muted">
          Nothing scheduled yet. Approve a draft to slot it into your next posting window.
        </p>
        <p className="text-2xs text-fg-subtle mt-2">
          Default cadence: {schedule.postsPerWeek} posts/week ·{' '}
          {schedule.slots.map((s) => DAY_OF_WEEK_LABEL[s.dayOfWeek]).join(' / ')}
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <p className="text-2xs text-fg-subtle">
        Cadence: {schedule.postsPerWeek}/week ·{' '}
        {schedule.slots.map((s) => DAY_OF_WEEK_LABEL[s.dayOfWeek]).join(' / ')}
        {schedule.nextSlot && (
          <>
            {' · '}Next slot: {new Date(schedule.nextSlot).toLocaleString('en-US', { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC', hour12: false })}
          </>
        )}
      </p>
      <div className="grid grid-cols-1 md:grid-cols-7 gap-3">
        {days.map((dow) => {
          const list = byDay.get(dow) ?? []
          const hasSlot = slotDays.has(dow)
          return (
            <div
              key={dow}
              className={clsx(
                'rounded-lg border bg-surface flex flex-col gap-2 p-3 min-h-32',
                hasSlot ? 'border-border' : 'border-dashed border-border opacity-70',
              )}
            >
              <div className="flex items-center justify-between">
                <span className="text-2xs font-semibold uppercase tracking-wide text-fg-subtle">
                  {DAY_OF_WEEK_LABEL[dow]}
                </span>
                {hasSlot && (
                  <span className="text-2xs text-fg-muted tnum">
                    {schedule.slots.find((s) => s.dayOfWeek === dow)?.timeOfDay.slice(0, 5)}
                  </span>
                )}
              </div>
              {list.length === 0 ? (
                <p className="text-2xs text-fg-subtle italic mt-auto">
                  {hasSlot ? 'open slot' : 'no slot'}
                </p>
              ) : (
                list.map((d) => (
                  <ScheduledChip key={d.id} draft={d} onChange={onChange} />
                ))
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ScheduledChip({
  draft,
  onChange,
}: {
  draft: LinkedInDraft
  onChange: (next: LinkedInDraft) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-md bg-surface-raised border border-border p-2 text-2xs flex flex-col gap-1">
      <span className="font-medium text-fg line-clamp-2">{draft.hook}</span>
      <span className="text-fg-subtle uppercase tracking-wide">
        {ANGLE_LABEL[draft.angle]}
      </span>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="self-start text-fg-muted hover:text-fg underline-offset-2 hover:underline"
      >
        {open ? 'Collapse' : 'View / edit'}
      </button>
      {open && (
        <div className="mt-2">
          <DraftCard draft={draft} onChange={onChange} />
        </div>
      )}
    </div>
  )
}
