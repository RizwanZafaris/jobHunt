/**
 * LetterGradeChips — A/B/C/D/F filter chip group for /today.
 *
 * Phase 2 §4.1 (G5) of the gap-closure roadmap. Renders a horizontal
 * chip strip with one toggle per letter grade plus an "All" reset.
 * Clicking a chip filters the visible TodayAction list to that grade
 * (multi-select supported — clicking "A" then "B" shows both).
 *
 * Per the roadmap: this is the MINIMAL chip-group surface; the full
 * filter modal is deferred. Scope here is intentionally tiny:
 *   - controlled component, parent owns the selection set
 *   - no URL persistence yet (keeps the SSR-rendered /today page
 *     unchanged on a fresh hit — filtering is client-only)
 *   - keyboard accessible (chips are buttons, not links)
 */
'use client'

import { clsx } from 'clsx'
import type { LetterGrade } from '@/lib/types/today'

const GRADE_TONE: Record<LetterGrade, string> = {
  A: 'bg-success/15 text-success border-success/30',
  B: 'bg-info/15 text-info border-info/30',
  C: 'bg-surface-raised text-fg-muted border-border',
  D: 'bg-warning/15 text-warning border-warning/30',
  F: 'bg-danger/15 text-danger border-danger/30',
}

const GRADE_TONE_INACTIVE: Record<LetterGrade, string> = {
  A: 'border-border text-fg-subtle hover:border-success/30 hover:text-success',
  B: 'border-border text-fg-subtle hover:border-info/30 hover:text-info',
  C: 'border-border text-fg-subtle hover:border-fg-muted hover:text-fg-muted',
  D: 'border-border text-fg-subtle hover:border-warning/30 hover:text-warning',
  F: 'border-border text-fg-subtle hover:border-danger/30 hover:text-danger',
}

const GRADES: LetterGrade[] = ['A', 'B', 'C', 'D', 'F']

export interface LetterGradeChipsProps {
  selected: Set<LetterGrade>
  onChange: (next: Set<LetterGrade>) => void
  counts?: Partial<Record<LetterGrade, number>>
}

export function LetterGradeChips({
  selected,
  onChange,
  counts,
}: LetterGradeChipsProps) {
  const total = Object.values(counts ?? {}).reduce(
    (sum, n) => sum + (n ?? 0),
    0,
  )
  const allActive = selected.size === 0

  function toggle(g: LetterGrade) {
    const next = new Set(selected)
    if (next.has(g)) next.delete(g)
    else next.add(g)
    onChange(next)
  }

  return (
    <div
      role="group"
      aria-label="Filter by fit grade"
      className="flex flex-wrap items-center gap-1.5"
    >
      <button
        type="button"
        aria-pressed={allActive}
        onClick={() => onChange(new Set())}
        className={clsx(
          'inline-flex items-center gap-1 px-2 py-1 rounded-full text-2xs font-semibold border transition-colors min-h-7',
          allActive
            ? 'bg-fg/10 text-fg border-fg/30'
            : 'border-border text-fg-subtle hover:text-fg hover:border-fg/20',
        )}
      >
        All
        {total > 0 && (
          <span className="tabular-nums text-fg-subtle">{total}</span>
        )}
      </button>
      {GRADES.map((g) => {
        const active = selected.has(g)
        const n = counts?.[g] ?? 0
        return (
          <button
            type="button"
            key={g}
            aria-pressed={active}
            onClick={() => toggle(g)}
            className={clsx(
              'inline-flex items-center gap-1 px-2 py-1 rounded-full text-2xs font-bold border transition-colors min-h-7',
              active ? GRADE_TONE[g] : GRADE_TONE_INACTIVE[g],
            )}
            title={`Grade ${g} fit`}
          >
            {g}
            {n > 0 && (
              <span className="text-2xs font-normal tabular-nums opacity-70">
                {n}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export default LetterGradeChips
