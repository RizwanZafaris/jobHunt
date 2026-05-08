'use client'

/**
 * OutcomeLogger — the dashboard form that closes the learning loop.
 *
 * Renders on /jobs/[id]. User logs:
 *   - Did the recruiter respond?
 *   - Did you get an interview? (and how many rounds)
 *   - Did you get an offer?
 *   - Self-rated resume quality 1–5
 *   - Notes / rejection reason
 *
 * Persists to public.resume_outcomes via /resumes/outcomes (upsert by
 * job_id when no resume_build_id exists yet — supports legacy resumes
 * built by the pre-G2 path).
 *
 * Auto-saves on each field change so the user never loses partial logs.
 */
import { useEffect, useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import {
  fetchOutcomeByJob,
  saveOutcome,
  ResumeOutcome,
} from '@/lib/profile-api'

interface Props {
  jobId: number
  resumeBuildId?: string | null   // present after G2 has run; null for legacy
  applicationId?: string | null
  companyName?: string | null
}

type Tri = boolean | null   // Yes / No / Unknown (null)

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

  // ── Load existing outcome on mount ──────────────────────────────────
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const r = await fetchOutcomeByJob(jobId)
        if (!cancelled) setOutcome(r.outcome)
      } catch (e: any) {
        if (!cancelled) setError(e?.message || 'Failed to load')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [jobId])

  // ── Save handler — auto-saves on each field change ──────────────────
  async function patch(field: string, value: any) {
    setSavingField(field)
    setError(null)
    try {
      const payload: any = { job_id: jobId, [field]: value }
      if (resumeBuildId) payload.resume_build_id = resumeBuildId
      if (applicationId) payload.application_id = applicationId
      if (companyName) payload.company_name = companyName

      const r = await saveOutcome(payload)
      setOutcome(r.row)
      // Reload SSR so the conversion funnel chart refreshes
      startTransition(() => router.refresh())
    } catch (e: any) {
      setError(e?.message || 'Save failed')
    } finally {
      setSavingField(null)
    }
  }

  if (loading) {
    return (
      <section className="bg-gray-900 border border-gray-800 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-white mb-2">📊 Outcome</h3>
        <p className="text-xs text-gray-500 italic">Loading…</p>
      </section>
    )
  }

  // Pre-G2 + no logging started yet — show CTA
  if (!outcome) {
    return (
      <section className="bg-gray-900 border border-amber-900/50 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-amber-300 mb-2">
          📊 Log Outcome
        </h3>
        <p className="text-[11px] text-gray-400 mb-3 leading-relaxed">
          Did this resume get a response? Logging outcomes is what makes the
          system learn — the persona synthesizer reads these weekly.
        </p>
        <button
          onClick={() =>
            patch('submitted_at', new Date().toISOString())
          }
          disabled={savingField === 'submitted_at'}
          className="w-full text-xs bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg font-medium"
        >
          {savingField === 'submitted_at'
            ? 'Starting…'
            : 'Start Tracking Outcome'}
        </button>
        {error && <p className="mt-2 text-[11px] text-red-400">{error}</p>}
      </section>
    )
  }

  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-white">📊 Outcome</h3>
        {pending && (
          <span className="text-[10px] text-gray-500">syncing…</span>
        )}
      </div>

      <div className="space-y-3">
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

        <div className="text-[10px] text-gray-500 pt-2 border-t border-gray-800">
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

        {error && <p className="text-[11px] text-red-400">{error}</p>}
      </div>
    </section>
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
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-wider text-gray-500 mb-1">
        {label}
        {saving && <span className="ml-1 text-blue-400">syncing…</span>}
      </label>
      <div className="flex gap-1">
        <Pill
          active={value === true}
          color="emerald"
          onClick={() => onChange(value === true ? null : true)}
        >
          Yes
        </Pill>
        <Pill
          active={value === false}
          color="red"
          onClick={() => onChange(value === false ? null : false)}
        >
          No
        </Pill>
        <Pill
          active={value === null || value === undefined}
          color="gray"
          onClick={() => onChange(null)}
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
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-wider text-gray-500 mb-1">
        {label}
        {saving && <span className="ml-1 text-blue-400">syncing…</span>}
      </label>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onChange(Math.max(0, v - 1))}
          className="bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 w-7 h-7 rounded text-sm"
        >
          −
        </button>
        <span className="text-base font-bold text-white min-w-[2ch] text-center">
          {v}
        </span>
        <button
          onClick={() => onChange(v + 1)}
          className="bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 w-7 h-7 rounded text-sm"
        >
          +
        </button>
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
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-wider text-gray-500 mb-1">
        {label}
        {saving && <span className="ml-1 text-blue-400">syncing…</span>}
      </label>
      <div className="flex gap-0.5">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            onClick={() => onChange(value === n ? null : n)}
            className={`text-lg ${
              value && n <= value
                ? 'text-amber-400 hover:text-amber-300'
                : 'text-gray-600 hover:text-gray-500'
            }`}
            title={`${n}/5`}
          >
            ★
          </button>
        ))}
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
  useEffect(() => setDraft(value), [value])

  function commit() {
    if (draft !== value) onChange(draft)
  }

  const Tag = multiline ? 'textarea' : 'input'
  return (
    <div>
      <label className="block text-[10px] uppercase tracking-wider text-gray-500 mb-1">
        {label}
        {saving && <span className="ml-1 text-blue-400">syncing…</span>}
      </label>
      <Tag
        value={draft}
        onChange={(e: any) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e: any) => {
          if (!multiline && e.key === 'Enter') {
            e.preventDefault()
            commit()
          }
          if (multiline && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            commit()
          }
        }}
        placeholder={placeholder}
        rows={multiline ? 2 : undefined}
        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
      />
    </div>
  )
}

function Pill({
  active,
  color,
  children,
  onClick,
}: {
  active: boolean
  color: 'emerald' | 'red' | 'gray'
  children: React.ReactNode
  onClick: () => void
}) {
  const colorMap = {
    emerald: active
      ? 'bg-emerald-700 border-emerald-600 text-white'
      : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-emerald-700',
    red: active
      ? 'bg-red-700 border-red-600 text-white'
      : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-red-700',
    gray: active
      ? 'bg-gray-700 border-gray-600 text-white'
      : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600',
  }
  return (
    <button
      onClick={onClick}
      className={`text-xs border px-2.5 py-1 rounded-md font-medium transition ${colorMap[color]}`}
    >
      {children}
    </button>
  )
}
