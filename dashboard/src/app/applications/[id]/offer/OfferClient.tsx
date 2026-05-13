/**
 * OfferClient — client-side state machine for the G8 offer evaluation
 * surface.
 *
 * Two modes:
 *   1. No `initialEvaluation` → show the paste-offer form. On submit
 *      hits POST /offers/evaluate-offer and flips to mode 2.
 *   2. Has `initialEvaluation` → render the full evaluation view:
 *      comp summary, market bands, negotiation script, risk factors,
 *      recommendation, decision form.
 */
'use client'

import { useState, useTransition } from 'react'
import {
  evaluateOffer,
  updateOfferDecision,
  regenerateOfferEvaluation,
} from '@/lib/api'
import type {
  OfferEvaluation,
  UserDecision,
  Recommendation,
} from '@/lib/types/offer'

interface OfferClientProps {
  applicationId: string
  initialEvaluation: OfferEvaluation | null
}

function formatUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return `$${Math.round(n).toLocaleString()}`
}

function recommendationBadgeClass(rec: Recommendation | null | undefined): string {
  switch (rec) {
    case 'accept':
      return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
    case 'negotiate':
      return 'bg-amber-500/15 text-amber-300 border-amber-500/40'
    case 'decline':
      return 'bg-rose-500/15 text-rose-300 border-rose-500/40'
    case 'consider':
    default:
      return 'bg-sky-500/15 text-sky-300 border-sky-500/40'
  }
}

export default function OfferClient({
  applicationId,
  initialEvaluation,
}: OfferClientProps) {
  const [evaluation, setEvaluation] = useState<OfferEvaluation | null>(
    initialEvaluation,
  )
  const [offerText, setOfferText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isPending, startTransition] = useTransition()
  const [force, setForce] = useState(false)

  // ── Mode 1 — paste-offer form ──────────────────────────────────────
  if (!evaluation) {
    const handleEvaluate = async (e: React.FormEvent) => {
      e.preventDefault()
      if (offerText.trim().length < 10) {
        setError('Paste at least 10 characters of offer text.')
        return
      }
      setError(null)
      startTransition(async () => {
        try {
          const result = await evaluateOffer({
            application_id: applicationId,
            offer_text: offerText,
            force,
          })
          setEvaluation(result)
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err))
        }
      })
    }

    return (
      <form
        onSubmit={handleEvaluate}
        className="space-y-3 rounded-lg border border-border bg-card p-4"
      >
        <h2 className="text-sm font-semibold text-fg">Paste the offer</h2>
        <p className="text-2xs text-fg-subtle">
          Email, PDF text, or notes. Costs ~$0.40 to run the 5-node graph.
          The wallet guard blocks evaluation if the parent job is closed —
          tick &ldquo;Force evaluation&rdquo; to override.
        </p>
        <textarea
          value={offerText}
          onChange={(e) => setOfferText(e.target.value)}
          rows={10}
          placeholder="Paste your offer text here…"
          className="w-full rounded-md border border-border bg-bg px-3 py-2 text-xs text-fg outline-none focus:ring-2 focus:ring-accent"
          disabled={isPending}
        />
        <label className="flex items-center gap-2 text-2xs text-fg-subtle">
          <input
            type="checkbox"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
            disabled={isPending}
          />
          Force evaluation (skip closed-job wallet guard)
        </label>
        {error && (
          <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-2xs text-rose-300">
            {error}
          </div>
        )}
        <button
          type="submit"
          disabled={isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-2xs font-semibold text-accent-fg transition-colors hover:bg-accent-hover disabled:opacity-50 min-h-9"
        >
          {isPending ? 'Evaluating…' : 'Evaluate offer'}
        </button>
      </form>
    )
  }

  // ── Mode 2 — full evaluation view ──────────────────────────────────
  const handleDecision = async (decision: UserDecision, finalComp?: number) => {
    setError(null)
    startTransition(async () => {
      try {
        await updateOfferDecision(evaluation.id, {
          user_decision: decision,
          final_total_comp: finalComp,
        })
        setEvaluation({
          ...evaluation,
          user_decision: decision,
          final_total_comp: finalComp ?? evaluation.final_total_comp ?? null,
          user_decision_at: new Date().toISOString(),
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    })
  }

  const handleRegenerate = async () => {
    setError(null)
    startTransition(async () => {
      try {
        const result = await regenerateOfferEvaluation(evaluation.id, force)
        setEvaluation(result)
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err))
      }
    })
  }

  return (
    <div className="space-y-4">
      {/* ── Recommendation banner ─────────────────────────────────── */}
      <div
        className={`rounded-lg border px-4 py-3 ${recommendationBadgeClass(
          evaluation.recommendation,
        )}`}
      >
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-3xs uppercase tracking-wider opacity-70">
              Recommendation
            </div>
            <div className="text-base font-bold">
              {evaluation.recommendation ?? '—'}
            </div>
          </div>
          <div className="text-right">
            <div className="text-3xs uppercase tracking-wider opacity-70">
              Confidence
            </div>
            <div className="text-base font-bold tabular-nums">
              {evaluation.confidence !== null && evaluation.confidence !== undefined
                ? `${Math.round(evaluation.confidence * 100)}%`
                : '—'}
            </div>
          </div>
          <div className="text-right">
            <div className="text-3xs uppercase tracking-wider opacity-70">
              Sleep-on-it
            </div>
            <div className="text-base font-bold tabular-nums">
              {evaluation.sleep_on_it_score !== null &&
              evaluation.sleep_on_it_score !== undefined
                ? `${Math.round(evaluation.sleep_on_it_score * 100)}%`
                : '—'}
            </div>
          </div>
        </div>
      </div>

      {/* ── Comp summary ───────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold text-fg">Compensation</h3>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
          <div>
            <dt className="text-fg-subtle">Base</dt>
            <dd className="font-mono tabular-nums text-fg">
              {formatUsd(evaluation.base_salary_usd)}
            </dd>
          </div>
          <div>
            <dt className="text-fg-subtle">Bonus</dt>
            <dd className="font-mono tabular-nums text-fg">
              {formatUsd(evaluation.target_bonus_usd)}
            </dd>
          </div>
          <div>
            <dt className="text-fg-subtle">Equity / yr</dt>
            <dd className="font-mono tabular-nums text-fg">
              {formatUsd(evaluation.equity_grant_usd)}
            </dd>
          </div>
          <div>
            <dt className="text-fg-subtle">Sign-on</dt>
            <dd className="font-mono tabular-nums text-fg">
              {formatUsd(evaluation.sign_on_bonus_usd)}
            </dd>
          </div>
          <div className="col-span-2">
            <dt className="text-fg-subtle">Total comp Y1</dt>
            <dd className="font-mono tabular-nums text-fg font-bold">
              {formatUsd(evaluation.total_comp_year_1)}
            </dd>
          </div>
          <div className="col-span-2">
            <dt className="text-fg-subtle">Risk-adjusted Y1</dt>
            <dd className="font-mono tabular-nums text-fg">
              {formatUsd(evaluation.risk_adjusted_total_comp)}
            </dd>
          </div>
        </dl>
      </section>

      {/* ── Market bands ───────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold text-fg">Market bands</h3>
        {evaluation.market_summary && (
          <p className="mt-1 text-2xs text-fg-subtle">{evaluation.market_summary}</p>
        )}
        <dl className="mt-2 grid grid-cols-4 gap-x-4 text-xs">
          {(['p25', 'p50', 'p75', 'p90'] as const).map((p) => {
            const k = `market_${p}` as const
            return (
              <div key={p}>
                <dt className="text-fg-subtle">{p}</dt>
                <dd className="font-mono tabular-nums text-fg">
                  {formatUsd(evaluation[k])}
                </dd>
              </div>
            )
          })}
        </dl>
        {evaluation.market_citations && evaluation.market_citations.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-3xs text-fg-subtle">
            {evaluation.market_citations.slice(0, 5).map((c, i) => (
              <li key={`${c.id ?? c.url ?? 'cite'}-${i}`} className="truncate">
                cite: {c.title ?? c.url ?? c.id ?? '—'}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* ── Negotiation script ─────────────────────────────────────── */}
      {evaluation.negotiation_script && (
        <section className="rounded-lg border border-border bg-card p-4">
          <div className="flex items-baseline justify-between">
            <h3 className="text-sm font-semibold text-fg">Negotiation plan</h3>
            <div className="text-2xs text-fg-subtle">
              anchor {formatUsd(evaluation.negotiation_anchor)} · walkaway{' '}
              {formatUsd(evaluation.negotiation_walkaway)}
            </div>
          </div>
          <div className="mt-2 whitespace-pre-wrap rounded-md bg-bg px-3 py-2 text-xs text-fg">
            {evaluation.negotiation_script}
          </div>
          {evaluation.archetype_strategy && (
            <p className="mt-1 text-3xs text-fg-subtle">
              Framing: {evaluation.archetype_strategy}
            </p>
          )}
        </section>
      )}

      {/* ── Risk factors ───────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-semibold text-fg">Risk</h3>
          <div className="text-2xs text-fg-subtle">
            score{' '}
            {evaluation.risk_score !== null && evaluation.risk_score !== undefined
              ? evaluation.risk_score.toFixed(2)
              : '—'}
          </div>
        </div>
        {evaluation.risk_factors && evaluation.risk_factors.length > 0 ? (
          <table className="mt-2 w-full text-xs">
            <thead className="text-fg-subtle">
              <tr>
                <th className="text-left font-normal">Kind</th>
                <th className="text-left font-normal">Severity</th>
                <th className="text-left font-normal">Description</th>
              </tr>
            </thead>
            <tbody className="text-fg">
              {evaluation.risk_factors.map((f, i) => (
                <tr key={`${f.kind}-${i}`}>
                  <td className="py-0.5 pr-2 font-mono text-3xs">{f.kind}</td>
                  <td className="py-0.5 pr-2 tabular-nums">{f.severity.toFixed(2)}</td>
                  <td className="py-0.5">{f.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mt-1 text-2xs text-fg-subtle">No risk factors flagged.</p>
        )}
      </section>

      {/* ── Rationale ──────────────────────────────────────────────── */}
      {evaluation.rationale_md && (
        <section className="rounded-lg border border-border bg-card p-4">
          <h3 className="text-sm font-semibold text-fg">Rationale</h3>
          <div className="mt-2 whitespace-pre-wrap text-xs text-fg">
            {evaluation.rationale_md}
          </div>
        </section>
      )}

      {/* ── Decision form ──────────────────────────────────────────── */}
      <section className="rounded-lg border border-border bg-card p-4">
        <h3 className="text-sm font-semibold text-fg">Record your decision</h3>
        {evaluation.user_decision ? (
          <p className="mt-1 text-xs text-fg-subtle">
            You logged{' '}
            <span className="font-semibold text-fg">
              {evaluation.user_decision}
            </span>{' '}
            on{' '}
            {evaluation.user_decision_at
              ? new Date(evaluation.user_decision_at).toLocaleDateString()
              : '—'}
            {evaluation.final_total_comp !== null &&
            evaluation.final_total_comp !== undefined
              ? ` for ${formatUsd(evaluation.final_total_comp)}.`
              : '.'}
          </p>
        ) : (
          <DecisionForm
            onSubmit={handleDecision}
            disabled={isPending}
          />
        )}
      </section>

      {/* ── Errors + regenerate ────────────────────────────────────── */}
      {error && (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-2xs text-rose-300">
          {error}
        </div>
      )}
      <div className="flex items-center justify-between pt-2">
        <label className="flex items-center gap-2 text-2xs text-fg-subtle">
          <input
            type="checkbox"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
            disabled={isPending}
          />
          Force on regenerate (closed-job override)
        </label>
        <button
          type="button"
          onClick={handleRegenerate}
          disabled={isPending}
          className="rounded-md border border-border px-3 py-1.5 text-2xs text-fg-muted hover:text-fg disabled:opacity-50"
        >
          {isPending ? 'Working…' : 'Regenerate evaluation'}
        </button>
      </div>
    </div>
  )
}

// ── Inline decision form ─────────────────────────────────────────────
interface DecisionFormProps {
  onSubmit: (decision: UserDecision, finalComp?: number) => void
  disabled?: boolean
}

function DecisionForm({ onSubmit, disabled }: DecisionFormProps) {
  const [decision, setDecision] = useState<UserDecision>('accepted')
  const [finalComp, setFinalComp] = useState<string>('')

  const handle = (e: React.FormEvent) => {
    e.preventDefault()
    const parsed = finalComp ? Number(finalComp.replace(/[^0-9.]/g, '')) : undefined
    onSubmit(decision, Number.isFinite(parsed) ? parsed : undefined)
  }

  return (
    <form onSubmit={handle} className="mt-2 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={decision}
          onChange={(e) => setDecision(e.target.value as UserDecision)}
          disabled={disabled}
          className="rounded-md border border-border bg-bg px-2 py-1 text-xs text-fg"
        >
          <option value="accepted">accepted</option>
          <option value="negotiated_accepted">negotiated_accepted</option>
          <option value="negotiated_declined">negotiated_declined</option>
          <option value="declined">declined</option>
          <option value="expired">expired</option>
        </select>
        <input
          type="text"
          inputMode="numeric"
          value={finalComp}
          onChange={(e) => setFinalComp(e.target.value)}
          placeholder="Final total comp (optional)"
          disabled={disabled}
          className="rounded-md border border-border bg-bg px-2 py-1 text-xs text-fg"
        />
        <button
          type="submit"
          disabled={disabled}
          className="rounded-md bg-accent px-3 py-1 text-2xs font-semibold text-accent-fg hover:bg-accent-hover disabled:opacity-50"
        >
          Save decision
        </button>
      </div>
    </form>
  )
}
