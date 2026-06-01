/**
 * RateUrlClient — FRD-14 URL Job Rater UI.
 *
 * Flow:
 *   1. User enters a job URL (primary) → POST /jobs/rate-url.
 *      • success → render the 6-dimension rating + extracted facts.
 *      • needs_jd_text (fetch failed/thin) → reveal a "paste JD text" box
 *        and an explanatory message (FRD-14 R1 fallback).
 *   2. User can also paste JD text directly at any time.
 *   3. After a rating, "Save to pipeline" promotes it into tracked jobs.
 *
 * All backend calls go through src/lib/api/jobRater.ts → /api/proxy.
 */
'use client'

import { useState } from 'react'
import Link from 'next/link'
import {
  Card,
  PageHeader,
  Button,
  TextInput,
  Textarea,
  Pill,
  Icon,
  type PillTone,
} from '@/components/ui'
import {
  rateUrl,
  saveRatedJob,
  type RateResult,
  type RatingBreakdown,
} from '@/lib/api/jobRater'

const DIMENSIONS: { key: keyof RatingBreakdown; label: string }[] = [
  { key: 'role_fit', label: 'Role fit' },
  { key: 'growth', label: 'Growth' },
  { key: 'comp', label: 'Comp' },
  { key: 'culture', label: 'Culture' },
  { key: 'remote', label: 'Remote' },
  { key: 'trajectory', label: 'Trajectory' },
]

function gradeTone(grade: string): PillTone {
  if (grade === 'A') return 'success'
  if (grade === 'B') return 'accent'
  if (grade === 'C') return 'neutral'
  return 'danger' // D / F
}

function scoreColor(n: number): string {
  if (n >= 75) return 'text-success'
  if (n >= 50) return 'text-fg'
  if (n >= 30) return 'text-warning'
  return 'text-danger'
}

export default function RateUrlClient() {
  const [url, setUrl] = useState('')
  const [jdText, setJdText] = useState('')
  const [showPaste, setShowPaste] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [result, setResult] = useState<RateResult | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedJobId, setSavedJobId] = useState<number | null>(null)
  const [savedDeduped, setSavedDeduped] = useState(false)

  async function handleRate(useText: boolean) {
    setError(null)
    setNotice(null)
    setResult(null)
    setSavedJobId(null)
    const payload = useText ? { jd_text: jdText.trim() } : { url: url.trim() }
    if (useText && !jdText.trim()) {
      setError('Paste the job description text first.')
      return
    }
    if (!useText && !url.trim()) {
      setError('Enter a job posting URL first.')
      return
    }
    setLoading(true)
    try {
      const res = await rateUrl(payload)
      if (res.kind === 'needs_jd_text') {
        // URL fetch failed/thin → reveal paste box + explain.
        setShowPaste(true)
        setNotice(res.message)
      } else {
        setResult(res)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong rating this job.')
    } finally {
      setLoading(false)
    }
  }

  async function handleSave() {
    if (!result) return
    setSaving(true)
    setError(null)
    try {
      const { job_id, deduped } = await saveRatedJob(result.rate_token)
      setSavedJobId(job_id)
      setSavedDeduped(deduped)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save this job.')
    } finally {
      setSaving(false)
    }
  }

  function reset() {
    setResult(null)
    setSavedJobId(null)
    setError(null)
    setNotice(null)
    setUrl('')
    setJdText('')
    setShowPaste(false)
  }

  const rating = result?.rating
  const extracted = result?.extracted

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Job Rater"
        title="Rate a job by link"
        description="Paste a job posting URL and get an instant 6-dimension fit rating. If the link can't be read, paste the description text instead."
      />

      {/* ── Input card ─────────────────────────────────────────── */}
      <Card padding="lg" className="space-y-4">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[260px]">
            <TextInput
              label="Job posting URL"
              type="url"
              inputMode="url"
              placeholder="https://company.com/careers/senior-pm"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={loading}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !loading) handleRate(false)
              }}
            />
          </div>
          <Button
            variant="primary"
            loading={loading && !showPaste}
            onClick={() => handleRate(false)}
          >
            <Icon name="search" size={14} /> Rate
          </Button>
        </div>

        {/* Paste-JD fallback toggle (also available up-front) */}
        {!showPaste && (
          <button
            type="button"
            className="text-xs text-accent hover:underline"
            onClick={() => setShowPaste(true)}
          >
            Or paste the job description text instead
          </button>
        )}

        {showPaste && (
          <div className="space-y-3 border-t border-border pt-4">
            {notice && (
              <p className="text-xs text-warning flex items-start gap-1.5">
                <Icon name="link" size={14} className="mt-0.5 shrink-0" />
                <span>{notice}</span>
              </p>
            )}
            <Textarea
              label="Job description text"
              placeholder="Paste the full job description here…"
              rows={8}
              value={jdText}
              onChange={(e) => setJdText(e.target.value)}
              disabled={loading}
            />
            <Button
              variant="primary"
              loading={loading}
              onClick={() => handleRate(true)}
            >
              <Icon name="sparkles" size={14} /> Rate pasted text
            </Button>
          </div>
        )}

        {error && (
          <p className="text-xs text-danger" role="alert">
            {error}
          </p>
        )}
      </Card>

      {/* ── Result ─────────────────────────────────────────────── */}
      {rating && extracted && (
        <Card padding="lg" className="space-y-5">
          {/* Header: grade + composite + title/company */}
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-fg truncate">
                {extracted.title || 'Untitled role'}
              </h2>
              <p className="text-sm text-fg-muted">
                {extracted.company || 'Unknown company'}
                {extracted.location ? ` · ${extracted.location}` : ''}
                {extracted.seniority ? ` · ${extracted.seniority}` : ''}
              </p>
            </div>
            <div className="flex items-center gap-3">
              <Pill tone={gradeTone(rating.letter_grade)} size="sm">
                Grade {rating.letter_grade}
              </Pill>
              <div className="text-right">
                <div className={`text-2xl font-semibold ${scoreColor(rating.composite)}`}>
                  {rating.composite}
                </div>
                <div className="text-2xs uppercase tracking-wide text-fg-subtle">composite</div>
              </div>
            </div>
          </div>

          {/* 6 dimensions */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {DIMENSIONS.map(({ key, label }) => {
              const v = rating[key]
              const n = typeof v === 'number' ? v : null
              const why = rating.rationale?.[key as string]
              return (
                <div key={key as string} className="rounded-md border border-border bg-surface-raised p-3">
                  <div className="flex items-baseline justify-between">
                    <span className="text-xs text-fg-muted">{label}</span>
                    <span className={`text-sm font-semibold ${n !== null ? scoreColor(n) : 'text-fg-subtle'}`}>
                      {n !== null ? n : '—'}
                    </span>
                  </div>
                  {why && <p className="mt-1 text-2xs text-fg-subtle leading-snug line-clamp-3">{why}</p>}
                </div>
              )
            })}
          </div>

          {/* Extracted facts */}
          {(extracted.comp_range || extracted.ats_keywords.length > 0) && (
            <div className="border-t border-border pt-4 space-y-2">
              {extracted.comp_range && (
                <p className="text-xs text-fg-muted">
                  <span className="text-fg-subtle">Comp:</span> {extracted.comp_range}
                </p>
              )}
              {extracted.ats_keywords.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {extracted.ats_keywords.slice(0, 15).map((kw) => (
                    <Pill key={kw} tone="neutral" size="xs">{kw}</Pill>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Save / saved state */}
          <div className="border-t border-border pt-4 flex items-center gap-3 flex-wrap">
            {savedJobId === null ? (
              <>
                <Button variant="success" loading={saving} onClick={handleSave}>
                  <Icon name="check" size={14} /> Save to pipeline
                </Button>
                <Button variant="ghost" onClick={reset} disabled={saving}>
                  Rate another
                </Button>
                <span className="text-2xs text-fg-subtle">
                  This rating is not saved until you add it to your pipeline.
                </span>
              </>
            ) : (
              <>
                <Pill tone="success" size="sm">
                  <Icon name="check" size={12} /> {savedDeduped ? 'Already in pipeline' : 'Saved to pipeline'}
                </Pill>
                <Link
                  href={`/applications/${savedJobId}/workspace`}
                  className="text-xs text-accent hover:underline"
                >
                  Open workspace →
                </Link>
                <Button variant="ghost" onClick={reset}>Rate another</Button>
              </>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}
