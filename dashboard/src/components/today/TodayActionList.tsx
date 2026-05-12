/**
 * TodayActionList — client wrapper that renders TodayActionCards under
 * an A-F letter-grade chip filter.
 *
 * Phase 2 §4.1 (G5) — keeps the parent /today page a server component
 * for fast first paint; the filtering UI lives here. Selection state is
 * local (no URL persistence yet — that's Phase 3).
 *
 * Cards without a `meta.letterGrade` (LinkedIn drafts, stale apps,
 * persona-stale, pre-G5 discovery rows) are NEVER filtered out — the
 * chip group is additive ("show me only A-grade") not exclusive.
 * Selecting any grade hides ungraded rows; clicking "All" surfaces
 * everything again.
 */
'use client'

import { useMemo, useState } from 'react'
import { LetterGradeChips } from './LetterGradeChips'
import { TodayActionCard } from './TodayActionCard'
import type { LetterGrade, TodayAction } from '@/lib/types/today'

export interface TodayActionListProps {
  actions: TodayAction[]
}

export function TodayActionList({ actions }: TodayActionListProps) {
  const [selected, setSelected] = useState<Set<LetterGrade>>(new Set())

  // Build the per-grade counts off the FULL (pre-filter) action list so
  // the chip totals stay stable as the user toggles.
  const counts = useMemo(() => {
    const acc: Partial<Record<LetterGrade, number>> = {}
    for (const a of actions) {
      const g = a.meta?.letterGrade
      if (g) acc[g] = (acc[g] ?? 0) + 1
    }
    return acc
  }, [actions])

  const visible = useMemo(() => {
    if (selected.size === 0) return actions
    return actions.filter((a) => {
      const g = a.meta?.letterGrade
      return g ? selected.has(g) : false
    })
  }, [actions, selected])

  return (
    <>
      {/* Chips render only when we actually have graded actions; on a
          page full of stale-application cards or empty state, hiding
          the strip keeps the surface uncluttered. */}
      {Object.keys(counts).length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <LetterGradeChips
            selected={selected}
            onChange={setSelected}
            counts={counts}
          />
          {selected.size > 0 && (
            <span
              className="text-2xs text-fg-subtle"
              role="status"
              aria-live="polite"
            >
              Showing {visible.length} of {actions.length}
            </span>
          )}
        </div>
      )}

      <ol
        className="flex flex-col gap-3"
        aria-label="Today's ranked actions"
      >
        {visible.map((a) => (
          <li key={a.id}>
            <TodayActionCard action={a} />
          </li>
        ))}
      </ol>
    </>
  )
}

export default TodayActionList
