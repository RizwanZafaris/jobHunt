'use client'

/**
 * OutcomeLogger — closes the learning loop after a resume submission.
 *
 * a11y rewrite:
 *   - Yes/No/? toggles are <Pill as="button" aria-pressed>
 *   - Star rating is a real role="radiogroup" with arrow-key nav
 *   - Save status announced via aria-live polite region
 *   - All inputs have <label htmlFor>
 */
import { useEffect, useId, useRef, useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import {
  fetchOutcomeByJob,
  saveOutcome,
  ResumeOutcome,
} from '@/lib/profile-api'
import { Card } from '@/components/ui/Card'
import { Pill } from '@/components/ui/Pill'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import { LiveRegion } from '@/components/ui/LiveRegion'

interface Props {
  jobId: number
  resumeBuildId?: string | null
  applicationId?: string | null
  companyName?: string | null
}

type Tri = boolean | null

export default function OutcomeLogger({
  jobId,
  resumeBuildId,
  applicationId,
  companyName,
}: Props) {
  const router = useRouter()
  const [outcome, setOutcome] = useState<ResumeOutcome | null>(null)
  const [loading, setLoading] = useState(true)
  const [savingField, setSavingField] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pending, startTransition] = useTransition()

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetchOutcomeByJob(jobId)
        if (!cancelled) setOutcome(r.outcome)
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Failed to load')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [jobId])

  async function patch(field: string, value: unknown) {
    setSavingField(field)
    setError(null)
    try {
      const payload: Record<string, unknown> = { job_id: jobId, [field]: value }
      if (resumeBuildId) payload.resume_build_id = resumeBuildId
      if (applicationId) payload.application_id = applicationId
      if (companyName) payload.company_name = companyName

      const r = await saveOutcome(payload)
      setOutcome(r.row)
      startTransition(() => router.refresh())
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSavingField(null)
    }
  }

  if (loading) {
    return (
      <Card title="Outcome">
        <p className="text-2xs text-fg-subtle italic">Loading…</p>
      </Card>
    )
  }

  if (!outcome) {
    return (
      <Card tone="warning" title="Log outcome">
        <p className="text-2xs text-fg-muted mb-3 leading-relaxed">
          Did this resume get a response? Logging outcomes is what makes the system learn — the persona synthesizer reads these weekly.
        </p>
        <Button
          variant="warning"
          size="sm"
          block
          onClick={() => patch('submitted_at', new Date().toISOString())}
          loading={savingField === 'submitted_at'}
        >
          Start tracking outcome
        </Button>
        {error && <p className="mt-2 text-2xs text-danger" role="alert">{error}</p>}
      </Card>
    )
  }

  return (
    <Card
      title="Outcome"
      actions={pending ? <span className="text-2xs text-fg-subtle">syncing…</span> : null}
    >
      <div className="space-y-4">
        <TriField
          label="Recruiter responded?"
          value={outcome.recruiter_responded}
          onChange={(v) => patch('recruiter_responded', v)}
          saving={savingField === 'recruiter_responded'}
        />

        <TriField
          label="Interview received?"
          value={outcome.interview_received}
          onChange={(v) => patch('interview_received', v)}
          saving={savingField === 'interview_received'}
        />

        {outcome.interview_received && (
          <NumberField
            label="Rounds reached"
            value={outcome.rounds_reached}
            onChange={(v) => patch('rounds_reached', v)}
            saving={savingField === 'rounds_reached'}
          />
        )}

        <TriField
          label="Offer received?"
          value={outcome.offer_received}
          onChange={(v) => patch('offer_received', v)}
          saving={savingField === 'offer_received'}
        />

        <StarField
          label="Resume quality (your read)"
          value={outcome.self_rated_quality}
          onChange={(v) => patch('self_rated_quality', v)}
          saving={savingField === 'self_rated_quality'}
        />

        {outcome.offer_received === false && (
          <TextField
            label="Rejection reason (if known)"
            value={outcome.rejected_reason || ''}
            onChange={(v) => patch('rejected_reason', v)}
            saving={savingField === 'rejected_reason'}
            placeholder="e.g. role filled internally"
          />
        )}

        <TextField
          label="Notes"
          value={outcome.notes || ''}
          onChange={(v) => patch('notes', v)}
          saving={savingField === 'notes'}
          placeholder="anything noteworthy about this application"
          multiline
        />

        <div className="text-2xs text-fg-subtle pt-3 border-t border-border">
          {outcome.logged_at && (
            <>Started {new Date(outcome.logged_at).toLocaleDateString()}</>
          )}
          {outcome.updated_at && outcome.updated_at !== outcome.logged_at && (
            <>
              {' '}· Last updated{' '}
              {new Date(outcome.updated_at).toLocaleDateString()}
            </>
          )}
        </div>

        <LiveRegion visible={false}>
          {savingField ? `Saving ${savingField}` : ''}
        </LiveRegion>

        {error && <p className="text-2xs text-danger" role="alert">{error}</p>}
      </div>
    </Card>
  )
}

// ─── Field components ─────────────────────────────────────────────────

function TriField({
  label,
  value,
  onChange,
  saving,
}: {
  label: string
  value: Tri
  onChange: (v: Tri) => void
  saving: boolean
}) {
  const groupId = useId()
  return (
    <div role="group" aria-labelledby={`${groupId}-label`}>
      <div id={`${groupId}-label`} className="block text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1.5">
        {label}
        {saving && <span className="ml-1.5 text-info normal-case font-normal">syncing…</span>}
      </div>
      <div className="flex gap-1">
        <Pill
          as="button"
          tone="success"
          pressed={value === true}
          onClick={() => onChange(value === true ? null : true)}
          size="sm"
        >
          Yes
        </Pill>
        <Pill
          as="button"
          tone="danger"
          pressed={value === false}
          onClick={() => onChange(value === false ? null : false)}
          size="sm"
        >
          No
        </Pill>
        <Pill
          as="button"
          tone="neutral"
          pressed={value === null || value === undefined}
          onClick={() => onChange(null)}
          size="sm"
          aria-label="Unknown"
        >
          ?
        </Pill>
      </div>
    </div>
  )
}

function NumberField({
  label,
  value,
  onChange,
  saving,
}: {
  label: string
  value: number | null
  onChange: (v: number | null) => void
  saving: boolean
}) {
  const v = value ?? 0
  const id = useId()
  return (
    <div>
      <label htmlFor={id} className="block text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1.5">
        {label}
        {saving && <span className="ml-1.5 text-info normal-case font-normal">syncing…</span>}
      </label>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="xs"
          variant="secondary"
          onClick={() => onChange(Math.max(0, v - 1))}
          aria-label="Decrement"
        >
          <Icon name="minus" size={12} />
        </Button>
        <span id={id} className="text-base font-semibold text-fg min-w-[2ch] text-center tnum tabular-nums" aria-live="polite">
          {v}
        </span>
        <Button
          type="button"
          size="xs"
          variant="secondary"
          onClick={() => onChange(v + 1)}
          aria-label="Increment"
        >
          <Icon name="plus" size={12} />
        </Button>
      </div>
    </div>
  )
}

function StarField({
  label,
  value,
  onChange,
  saving,
}: {
  label: string
  value: number | null
  onChange: (v: number | null) => void
  saving: boolean
}) {
  const groupId = useId()
  const refs = useRef<(HTMLButtonElement | null)[]>([])

  function onKey(e: React.KeyboardEvent, n: number) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowUp') {
      e.preventDefault()
      const next = Math.min(5, n + 1)
      refs.current[next - 1]?.focus()
      onChange(next)
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') {
      e.preventDefault()
      const prev = Math.max(1, n - 1)
      refs.current[prev - 1]?.focus()
      onChange(prev)
    } else if (e.key === 'Home') {
      e.preventDefault()
      refs.current[0]?.focus()
      onChange(1)
    } else if (e.key === 'End') {
      e.preventDefault()
      refs.current[4]?.focus()
      onChange(5)
    }
  }

  return (
    <div role="radiogroup" aria-labelledby={`${groupId}-label`}>
      <div id={`${groupId}-label`} className="block text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1.5">
        {label}
        {saving && <span className="ml-1.5 text-info normal-case font-normal">syncing…</span>}
      </div>
      <div className="flex gap-0.5">
        {[1, 2, 3, 4, 5].map((n, i) => {
          const filled = !!value && n <= value
          const isCurrent = value === n
          return (
            <button
              key={n}
              type="button"
              ref={(el) => { refs.current[i] = el }}
              role="radio"
              aria-checked={isCurrent}
              tabIndex={isCurrent || (!value && n === 1) ? 0 : -1}
              onClick={() => onChange(value === n ? null : n)}
              onKeyDown={(e) => onKey(e, n)}
              aria-label={`${n} of 5 stars`}
              className="p-1 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
            >
              <Icon
                name="star"
                size={18}
                className={filled ? 'text-warning fill-warning' : 'text-fg-subtle'}
                style={filled ? { fill: 'currentColor' } : undefined}
              />
            </button>
          )
        })}
      </div>
    </div>
  )
}

function TextField({
  label,
  value,
  onChange,
  saving,
  placeholder,
  multiline,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  saving: boolean
  placeholder?: string
  multiline?: boolean
}) {
  const [draft, setDraft] = useState(value)
  const id = useId()
  useEffect(() => setDraft(value), [value])

  function commit() {
    if (draft !== value) onChange(draft)
  }

  const baseCls =
    'w-full bg-surface text-fg placeholder:text-fg-subtle border border-border-strong rounded-md px-2.5 py-2 text-xs focus-visible:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30'

  return (
    <div>
      <label htmlFor={id} className="block text-2xs uppercase tracking-wider text-fg-subtle font-semibold mb-1.5">
        {label}
        {saving && <span className="ml-1.5 text-info normal-case font-normal">syncing…</span>}
      </label>
      {multiline ? (
        <textarea
          id={id}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault()
              commit()
            }
          }}
          placeholder={placeholder}
          rows={2}
          className={`${baseCls} resize-y`}
        />
      ) : (
        <input
          id={id}
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commit()
            }
          }}
          placeholder={placeholder}
          className={baseCls}
        />
      )}
    </div>
  )
}
