'use client'

/**
 * AssistClient — Tier 3 G7 HITL approval UI.
 *
 * Per-question card: classification pill, label, draft answer (editable),
 * persona critique flags, per-field Approve / Edit / Regenerate.
 * Bottom action bar: "Approve all & fill" — calls /approve which resumes
 * the LangGraph past the interrupt and runs form_filler + persist.
 *
 * Style: deliberately minimal. The point is to be readable and to ship —
 * not to be the prettiest screen on the dashboard. Owners of the design
 * system can polish later.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'

type Cite = { kind: string; id: string }

type PersonaCritique = {
  verdict?: 'accept' | 'revise'
  fabricated_metrics?: string[]
  banned_keywords_in_answer?: string[]
  fix?: string | null
}

type G7Question = {
  idx: number
  field_id: string
  field_label: string
  field_type: string
  required?: boolean
  options?: string[] | null
  classification: string
  retrieved_context?: any[]
  cites?: Cite[]
  generated_answer: string
  persona_critique?: PersonaCritique
  user_edited?: string | null
  approved: boolean
  ats_filled_ok?: boolean | null
  fill_error?: string | null
}

type ApplicationAnswers = {
  id: string
  user_id: string
  application_id: number
  form_url: string
  ats_type: string
  questions: G7Question[]
  status: string
  total_cost_usd: number
  outcome?: string | null
}

interface Props {
  applicationAnswerId: string
  initialState: ApplicationAnswers
}

const CATEGORY_COLOR: Record<string, string> = {
  basic_info: 'bg-slate-100 text-slate-800',
  cover_letter: 'bg-indigo-100 text-indigo-800',
  why_this_company: 'bg-amber-100 text-amber-800',
  why_this_role: 'bg-amber-100 text-amber-800',
  salary: 'bg-emerald-100 text-emerald-800',
  behavioral: 'bg-sky-100 text-sky-800',
  technical: 'bg-violet-100 text-violet-800',
  dei_values: 'bg-rose-100 text-rose-800',
  portfolio_url: 'bg-slate-100 text-slate-800',
  custom: 'bg-gray-100 text-gray-800',
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/proxy${path}`, {
    ...(init || {}),
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  })
  if (!res.ok) {
    const t = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${t.slice(0, 200)}`)
  }
  return res.json() as Promise<T>
}

export function AssistClient({ applicationAnswerId, initialState }: Props) {
  const [row, setRow] = useState<ApplicationAnswers>(initialState)
  const [busyIdx, setBusyIdx] = useState<number | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Poll while status is drafting (graph still running pre-interrupt).
  useEffect(() => {
    if (row.status !== 'drafting') return
    let cancelled = false
    const tick = async () => {
      try {
        const next = await api<ApplicationAnswers>(`/workspace/applications/${applicationAnswerId}`)
        if (!cancelled) setRow(next)
      } catch (e) {
        // swallow
      }
    }
    const id = setInterval(tick, 2_500)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [applicationAnswerId, row.status])

  const allApproved = useMemo(
    () => (row.questions || []).every(q => q.approved),
    [row.questions],
  )
  const approvedCount = (row.questions || []).filter(q => q.approved).length

  const handleEdit = useCallback(async (idx: number, user_edited: string) => {
    setBusyIdx(idx)
    setError(null)
    try {
      const next = await api<ApplicationAnswers>(
        `/workspace/applications/${applicationAnswerId}/questions/${idx}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ user_edited }),
        },
      )
      setRow(next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyIdx(null)
    }
  }, [applicationAnswerId])

  const handleApprove = useCallback(async (idx: number, approved: boolean) => {
    setBusyIdx(idx)
    setError(null)
    try {
      const next = await api<ApplicationAnswers>(
        `/workspace/applications/${applicationAnswerId}/questions/${idx}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ approved }),
        },
      )
      setRow(next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyIdx(null)
    }
  }, [applicationAnswerId])

  const handleRegenerate = useCallback(async (idx: number) => {
    setBusyIdx(idx)
    setError(null)
    try {
      await api(`/workspace/applications/${applicationAnswerId}/regenerate/${idx}`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      const next = await api<ApplicationAnswers>(`/workspace/applications/${applicationAnswerId}`)
      setRow(next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusyIdx(null)
    }
  }, [applicationAnswerId])

  const handleApproveAll = useCallback(async () => {
    setBulkBusy(true)
    setError(null)
    try {
      const approvals = (row.questions || []).map(q => ({
        idx: q.idx,
        approved: true,
        user_edited: q.user_edited ?? null,
      }))
      const next = await api<any>(`/workspace/applications/${applicationAnswerId}/approve`, {
        method: 'POST',
        body: JSON.stringify({ approvals }),
      })
      setRow(prev => ({
        ...prev,
        ...(next.questions ? { questions: next.questions } : {}),
        status: next.status || 'filled',
        total_cost_usd: next.cost_usd_total ?? prev.total_cost_usd,
      }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBulkBusy(false)
    }
  }, [applicationAnswerId, row.questions])

  const handleCancel = useCallback(async () => {
    setBulkBusy(true)
    try {
      await api(`/workspace/applications/${applicationAnswerId}/cancel`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      const next = await api<ApplicationAnswers>(`/workspace/applications/${applicationAnswerId}`)
      setRow(next)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBulkBusy(false)
    }
  }, [applicationAnswerId])

  return (
    <div className="space-y-6 pb-24">
      {/* Header */}
      <header className="flex items-center justify-between gap-4">
        <div>
          <Link href="/applications" className="text-xs text-muted hover:underline">← Applications</Link>
          <h1 className="text-2xl font-semibold mt-1">Application assist · G7</h1>
          <p className="text-sm text-muted">
            Form: <a href={row.form_url} target="_blank" rel="noreferrer" className="underline">{row.form_url}</a>
            {' · '}ATS: <code>{row.ats_type}</code>
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="px-2 py-1 rounded bg-slate-100 text-slate-800">
            status: <strong>{row.status}</strong>
          </span>
          <span className="px-2 py-1 rounded bg-emerald-100 text-emerald-800">
            ${(row.total_cost_usd || 0).toFixed(3)}
          </span>
        </div>
      </header>

      {/* Status banners */}
      {row.status === 'drafting' && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm">
          Drafting answers… The graph is still running. This typically takes
          8–15 seconds. Polling automatically.
        </div>
      )}
      {row.status === 'filled' && (
        <div className="rounded border border-emerald-300 bg-emerald-50 p-3 text-sm">
          Form filled. Switch to the browser window opened by the agent,
          review every field, then click Submit on the Greenhouse page
          yourself. (HITL — we never auto-submit.)
        </div>
      )}
      {row.status === 'cancelled' && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm">
          Run cancelled.
        </div>
      )}
      {error && (
        <div className="rounded border border-red-300 bg-red-50 p-3 text-sm">
          Error: {error}
        </div>
      )}

      {/* Question list */}
      <ol className="space-y-3">
        {(row.questions || []).map(q => (
          <QuestionCard
            key={q.idx}
            q={q}
            busy={busyIdx === q.idx}
            disabled={row.status === 'cancelled' || row.status === 'submitted'}
            onEdit={handleEdit}
            onApprove={handleApprove}
            onRegenerate={handleRegenerate}
          />
        ))}
        {(row.questions || []).length === 0 && row.status !== 'drafting' && (
          <li className="rounded border border-slate-200 bg-slate-50 p-4 text-sm text-muted">
            No questions extracted from the form. Check the URL.
          </li>
        )}
      </ol>

      {/* Bottom action bar */}
      <div className="fixed bottom-0 left-0 right-0 border-t border-slate-200 bg-white/95 backdrop-blur px-6 py-3 flex items-center justify-between text-sm">
        <span>
          {approvedCount} / {(row.questions || []).length} approved
          {' · '}Cost so far: <strong>${(row.total_cost_usd || 0).toFixed(3)}</strong>
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleCancel}
            disabled={bulkBusy || row.status === 'cancelled' || row.status === 'filled'}
            className="px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-100 disabled:opacity-50"
          >
            Cancel run
          </button>
          <button
            type="button"
            onClick={handleApproveAll}
            disabled={
              bulkBusy ||
              row.status === 'filled' ||
              row.status === 'cancelled' ||
              !allApproved ||
              (row.questions || []).length === 0
            }
            className="px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-slate-400"
          >
            {bulkBusy ? 'Filling…' : 'Approve all & fill'}
          </button>
        </div>
      </div>
    </div>
  )
}

function QuestionCard({
  q, busy, disabled, onEdit, onApprove, onRegenerate,
}: {
  q: G7Question
  busy: boolean
  disabled: boolean
  onEdit: (idx: number, v: string) => void
  onApprove: (idx: number, approved: boolean) => void
  onRegenerate: (idx: number) => void
}) {
  const draft = q.user_edited ?? q.generated_answer ?? ''
  const critique = q.persona_critique || {}
  const flagFab = (critique.fabricated_metrics || []).length > 0
  const flagBanned = (critique.banned_keywords_in_answer || []).length > 0

  return (
    <li className={`rounded border p-4 ${q.approved ? 'border-emerald-300 bg-emerald-50/40' : 'border-slate-200 bg-white'}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded ${CATEGORY_COLOR[q.classification] || CATEGORY_COLOR.custom}`}>
              {q.classification}
            </span>
            {q.required && <span className="text-[10px] uppercase text-red-600">required</span>}
            <code className="text-[11px] text-slate-500 truncate">#{q.field_id}</code>
            <span className="text-[11px] text-slate-500">[{q.field_type}]</span>
          </div>
          <h3 className="text-sm font-medium leading-snug">{q.field_label}</h3>
        </div>
        <div className="flex gap-1 shrink-0">
          <button
            type="button"
            onClick={() => onRegenerate(q.idx)}
            disabled={busy || disabled}
            className="text-xs px-2 py-1 rounded border border-slate-300 hover:bg-slate-100 disabled:opacity-50"
            title="Re-run answer_generator for just this question"
          >
            Regenerate
          </button>
          <button
            type="button"
            onClick={() => onApprove(q.idx, !q.approved)}
            disabled={busy || disabled}
            className={`text-xs px-2 py-1 rounded ${q.approved ? 'bg-emerald-600 text-white hover:bg-emerald-700' : 'bg-slate-700 text-white hover:bg-slate-800'} disabled:opacity-50`}
          >
            {q.approved ? 'Approved ✓' : 'Approve'}
          </button>
        </div>
      </div>

      {/* Editable draft */}
      <textarea
        rows={Math.min(8, Math.max(2, Math.ceil(draft.length / 80)))}
        value={draft}
        onChange={e => onEdit(q.idx, e.target.value)}
        disabled={busy || disabled || q.approved}
        className="mt-3 w-full text-sm rounded border border-slate-300 p-2 font-mono leading-relaxed disabled:bg-slate-50"
      />

      {/* Critique flags */}
      {(flagFab || flagBanned || critique.fix) && (
        <div className="mt-2 text-xs space-y-1">
          {flagFab && (
            <div className="rounded bg-red-50 border border-red-200 px-2 py-1 text-red-700">
              Fabricated metrics flagged: <code>{(critique.fabricated_metrics || []).join(', ')}</code> — will be redacted on fill.
            </div>
          )}
          {flagBanned && (
            <div className="rounded bg-amber-50 border border-amber-200 px-2 py-1 text-amber-800">
              Banned company keywords: <code>{(critique.banned_keywords_in_answer || []).join(', ')}</code>
            </div>
          )}
          {critique.fix && (
            <div className="rounded bg-sky-50 border border-sky-200 px-2 py-1 text-sky-800">
              Critique: {critique.fix}
            </div>
          )}
        </div>
      )}

      {/* Cite breadcrumbs */}
      {(q.cites || []).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {(q.cites || []).map((c, i) => (
            <span key={i} className="text-[10px] bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded">
              cite:{c.kind}_id={(c.id || '').slice(0, 8)}
            </span>
          ))}
        </div>
      )}

      {/* Filler error */}
      {q.ats_filled_ok === false && q.fill_error && (
        <div className="mt-2 text-xs rounded bg-red-50 border border-red-200 px-2 py-1 text-red-700">
          Fill failed: {q.fill_error}
        </div>
      )}
    </li>
  )
}
