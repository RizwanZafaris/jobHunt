/**
 * Job Rater API client — FRD-14 URL Job Rater.
 *
 * Two backend endpoints (api/job_rater.py):
 *   POST /jobs/rate-url       {url?, jd_text?} → ephemeral rating + rate_token
 *   POST /jobs/rate-url/save   {rate_token}      → promote into the jobs pipeline
 *
 * Same X-Secret-Key + /api/proxy pattern as the rest of the dashboard:
 *   - Browser → /api/proxy/* (secret injected server-side by the proxy route).
 *   - Server  → API_URL directly with the secret header.
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

const isBrowser = typeof window !== 'undefined'
const baseUrl = isBrowser ? '/api/proxy' : API_URL

function buildHeaders(): Record<string, string> {
  const base: Record<string, string> = { 'Content-Type': 'application/json' }
  if (!isBrowser && SECRET_KEY) base['X-Secret-Key'] = SECRET_KEY
  return base
}

// ── Types ────────────────────────────────────────────────────────────────
export interface ExtractedJD {
  title: string | null
  company: string | null
  seniority: string | null
  location: string | null
  comp_range: string | null
  responsibilities: string[]
  requirements: string[]
  ats_keywords: string[]
  raw_jd_md: string
}

export interface RatingBreakdown {
  composite: number
  letter_grade: 'A' | 'B' | 'C' | 'D' | 'F'
  role_fit?: number
  growth?: number
  comp?: number
  culture?: number
  remote?: number
  trajectory?: number
  rationale?: Record<string, string>
  [k: string]: unknown
}

/** Successful ephemeral rating. */
export interface RateResult {
  kind: 'rated'
  rating: RatingBreakdown
  extracted: ExtractedJD
  rate_token: string
}

/** URL fetch failed/thin — UI should reveal the paste-JD box. */
export interface NeedsJdText {
  kind: 'needs_jd_text'
  reason: string
  url: string | null
  message: string
}

export type RateUrlResponse = RateResult | NeedsJdText

export interface SaveResult {
  job_id: number
  deduped: boolean
}

// ── Calls ────────────────────────────────────────────────────────────────
export async function rateUrl(input: { url?: string; jd_text?: string }): Promise<RateUrlResponse> {
  const res = await fetch(`${baseUrl}/jobs/rate-url`, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(input),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    // 422 (both empty) or other backend error — surface the detail.
    const detail = typeof data?.detail === 'string' ? data.detail : `Request failed (${res.status})`
    throw new Error(detail)
  }
  if (data?.needs_jd_text) {
    return {
      kind: 'needs_jd_text',
      reason: String(data.reason ?? 'unknown'),
      url: data.url ?? null,
      message: String(data.message ?? 'Could not read that URL. Paste the job description text instead.'),
    }
  }
  return {
    kind: 'rated',
    rating: data.rating as RatingBreakdown,
    extracted: data.extracted as ExtractedJD,
    rate_token: String(data.rate_token),
  }
}

export async function saveRatedJob(rateToken: string): Promise<SaveResult> {
  const res = await fetch(`${baseUrl}/jobs/rate-url/save`, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ rate_token: rateToken }),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : `Save failed (${res.status})`
    throw new Error(detail)
  }
  return { job_id: data.job_id as number, deduped: Boolean(data.deduped) }
}
