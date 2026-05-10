'use client'

/**
 * OutcomeLogger — right pane of the Interview Studio.
 *
 * Lets the user log this round's outcome and shows already-logged rounds.
 * Note: the project already has a top-level dashboard/src/components/OutcomeLogger.tsx
 * for resume outcomes. This one is interview-round scoped and lives under
 * components/interview-studio/ to keep the surfaces separate.
 *
 * Form:
 *   round_number       — defaults to max(existing) + 1
 *   round_type         — select (recruiter / hm / panel / exec / technical / take_home)
 *   passed             — yes / no / unknown (Pill toggle)
 *   questions_asked    — tag-style chip input (Enter to commit)
 *   feedback_text      — textarea
 *   "Save outcome"
 *
 * On success: shows a success card with the credit summary + the
 * "refines persona on next Sunday cron" message and calls router.refresh().
 */
import { useMemo, useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'

import { logOutcome } from '@/lib/api/studio'
import {
  ROUND_TYPE_LABEL,
  type InterviewRoundOutcome,
  type LogOutcomeResponse,
} from '@/lib/types/interview-studio'
import { Button, Card, Pill, Select, TextInput, Textarea } from '@/components/ui'

interface OutcomeLoggerProps {
  applicationId: string
  existingOutcomes: InterviewRoundOutcome[]
  companyName: string
}

const ROUND_TYPE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'recruiter', label: ROUND_TYPE_LABEL.recruiter },
  { value: 'hm', label: ROUND_TYPE_LABEL.hm },
  { value: 'panel', label: ROUND_TYPE_LABEL.panel },
  { value: 'exec', label: ROUND_TYPE_LABEL.exec },
  { value: 'technical', label: ROUND_TYPE_LABEL.technical },
  { value: 'take_home', label: ROUND_TYPE_LABEL.take_home },
]

type PassedState = 'yes' | 'no' | 'unknown'

function PassedToggle({
  value,
  onChange,
  disabled,
}: {
  value: PassedState
  onChange: (next: PassedState) => void
  disabled?: boolean
}) {
  const items: Array<{ key: PassedState; label: string; tone: 'success' | 'danger' | 'neutral' }> = [
    { key: 'yes', label: 'Passed', tone: 'success' },
    { key: 'no', label: 'Did not pass', tone: 'danger' },
    { key: 'unknown', label: 'Unknown', tone: 'neutral' },
  ]
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Passed this round?">
      {items.map((it) => (
        <Pill
          key={it.key}
          as="button"
          tone={it.tone}
          pressed={value === it.key}
          size="sm"
          role="radio"
          aria-checked={value === it.key}
          disabled={disabled}
          onClick={() => onChange(it.key)}
        >
          {it.label}
        </Pill>
      ))}
    </div>
  )
}

function ChipInput({
  values,
  onChange,
  placeholder,
  disabled,
}: {
  values: string[]
  onChange: (next: string[]) => void
  placeholder: string
  disabled?: boolean
}) {
  const [draft, setDraft] = useState('')
  function commit() {
    const v = draft.trim()
    if (!v) return
    if (values.includes(v)) {
      setDraft('')
      return
    }
    onChange([...values, v])
    setDraft('')
  }
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap gap-1.5">
        {values.map((v, i) => (
          <span
            key={`${v}-${i}`}
            className="inline-flex items-center gap-1 text-2xs px-2 py-0.5 rounded-md border border-border bg-surface-raised text-fg"
          >
            <span className="max-w-[180px] truncate" title={v}>
              {v}
            </span>
            <button
              type="button"
              aria-label={`Remove "${v}"`}
              onClick={() => onChange(values.filter((x) => x !== v))}
              className="text-fg-subtle hover:text-danger"
              disabled={disabled}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <TextInput
        label="Add question"
        srLabel
        placeholder={placeholder}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            commit()
          }
        }}
        onBlur={commit}
        disabled={disabled}
      />
    </div>
  )
}

export default function OutcomeLogger({
  applicationId,
  existingOutcomes,
  companyName,
}: OutcomeLoggerProps) {
  const router = useRouter()
  const nextRound = useMemo(() => {
    if (!existingOutcomes.length) return 1
    return Math.max(...existingOutcomes.map((o) => o.round_number)) + 1
  }, [existingOutcomes])

  const [roundNumber, setRoundNumber] = useState<number>(nextRound)
  const [roundType, setRoundType] = useState<string>('hm')
  const [passed, setPassed] = useState<PassedState>('unknown')
  const [questions, setQuestions] = useState<string[]>([])
  const [feedback, setFeedback] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<LogOutcomeResponse | null>(null)
  const [isSubmitting, startSubmit] = useTransition()

  function reset() {
    setRoundNumber(nextRound + 1)
    setRoundType('hm')
    setPassed('unknown')
    setQuestions([])
    setFeedback('')
  }

  function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError(null)
    setSuccess(null)
    startSubmit(async () => {
      try {
        const result = await logOutcome(applicationId, {
          round_number: roundNumber,
          round_type: roundType,
          passed: passed === 'unknown' ? null : passed === 'yes',
          questions_asked: questions,
          feedback_text: feedback || null,
        })
        setSuccess(result)
        reset()
        router.refresh()
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'Failed to log outcome')
      }
    })
  }

  return (
    <div className="space-y-4">
      {existingOutcomes.length > 0 && (
        <Card padding="sm" tone="muted">
          <p className="text-2xs text-fg-muted mb-2">Already logged</p>
          <ul className="space-y-1.5">
            {existingOutcomes.map((o) => {
              const tone =
                o.passed === true
                  ? 'success'
                  : o.passed === false
                    ? 'danger'
                    : 'neutral'
              return (
                <li key={o.id} className="flex items-center justify-between gap-2 text-2xs">
                  <span className="truncate">
                    Round {o.round_number} ·{' '}
                    {ROUND_TYPE_LABEL[o.round_type ?? ''] || o.round_type}
                  </span>
                  <Pill tone={tone} size="xs">
                    {o.passed === true ? 'Passed' : o.passed === false ? 'Failed' : 'Unknown'}
                  </Pill>
                </li>
              )
            })}
          </ul>
        </Card>
      )}

      <form onSubmit={handleSubmit} className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <TextInput
            label="Round #"
            type="number"
            min={1}
            max={20}
            value={roundNumber}
            onChange={(e) => setRoundNumber(Number(e.target.value) || 1)}
            disabled={isSubmitting}
          />
          <Select
            label="Round type"
            value={roundType}
            onChange={(e) => setRoundType(e.target.value)}
            disabled={isSubmitting}
          >
            {ROUND_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </div>

        <div>
          <label className="block text-2xs font-medium text-fg-muted uppercase tracking-wider mb-1">
            Outcome
          </label>
          <PassedToggle value={passed} onChange={setPassed} disabled={isSubmitting} />
        </div>

        <div>
          <label className="block text-2xs font-medium text-fg-muted uppercase tracking-wider mb-1">
            Questions asked
          </label>
          <ChipInput
            values={questions}
            onChange={setQuestions}
            placeholder="Enter to add a question"
            disabled={isSubmitting}
          />
        </div>

        <Textarea
          label="Feedback / notes"
          rows={3}
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder="What worked, what didn't, what they pushed back on…"
          disabled={isSubmitting}
        />

        {error && (
          <p className="text-2xs text-danger" role="alert">
            {error}
          </p>
        )}

        <Button type="submit" block loading={isSubmitting} disabled={isSubmitting}>
          Save outcome
        </Button>
      </form>

      {success && (
        <Card padding="md" tone="success">
          <p className="text-xs text-fg font-medium">Outcome saved.</p>
          <p className="text-2xs text-fg-muted mt-1 leading-relaxed">
            Credited{' '}
            <strong>{success.credit_summary?.credited_knowledge_count ?? 0}</strong>{' '}
            knowledge {(success.credit_summary?.credited_knowledge_count ?? 0) === 1 ? 'row' : 'rows'}{' '}
            (Δ{(success.credit_summary?.delta_per_row ?? 0).toFixed(3)}). Your{' '}
            <strong>{companyName}</strong> persona will refine on the next Sunday cron.
          </p>
        </Card>
      )}
    </div>
  )
}
