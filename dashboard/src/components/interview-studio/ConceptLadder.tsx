'use client'

/**
 * ConceptLadder — three-rung level selector.
 *
 * Renders three pills with a dotted vertical connector between them. Clicking
 * a rung sets the active concept level so the next tutor turn engages at
 * that depth. Persisted server-side via tutorChat() (see api/interview_studio.py).
 *
 * Design choice: the three buttons are real <button>s (Pill as="button"),
 * keyboard accessible, with aria-pressed for AT.
 */
import {
  CONCEPT_LEVELS,
  CONCEPT_LEVEL_DESC,
  CONCEPT_LEVEL_LABEL,
  type ConceptLevel,
} from '@/lib/types/interview-studio'
import { Pill } from '@/components/ui'

interface ConceptLadderProps {
  value: ConceptLevel
  onChange: (next: ConceptLevel) => void
  /** When true, disables the rungs (e.g. while the tutor is replying). */
  disabled?: boolean
}

export default function ConceptLadder({ value, onChange, disabled }: ConceptLadderProps) {
  return (
    <div
      role="radiogroup"
      aria-label="Tutor concept level"
      className="flex flex-col gap-1.5"
    >
      <div className="flex items-stretch gap-2">
        {CONCEPT_LEVELS.map((level, i) => {
          const active = value === level
          return (
            <div key={level} className="flex-1 min-w-0 flex items-center gap-2">
              <Pill
                as="button"
                tone={active ? 'accent' : 'neutral'}
                pressed={active}
                size="sm"
                role="radio"
                aria-checked={active}
                disabled={disabled}
                onClick={() => onChange(level)}
                className="w-full justify-center"
              >
                {CONCEPT_LEVEL_LABEL[level]}
              </Pill>
              {/* dotted connector — only between rungs, not after the last */}
              {i < CONCEPT_LEVELS.length - 1 && (
                <span
                  aria-hidden
                  className="hidden md:block h-0 border-t border-dotted border-border-strong w-3 shrink-0"
                />
              )}
            </div>
          )
        })}
      </div>
      <p className="text-2xs text-fg-subtle leading-relaxed">
        {CONCEPT_LEVEL_DESC[value]}
      </p>
    </div>
  )
}
