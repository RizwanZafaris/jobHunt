// Server-side fetches (called from React Server Components in page.tsx)
// use the direct API URL with the secret. Client-side fetches must go
// through /api/proxy/* so the secret stays server-side.
const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

const serverHeaders = {
  'Content-Type': 'application/json',
  'X-Secret-Key': SECRET_KEY,
}

const clientHeaders = {
  'Content-Type': 'application/json',
}

// In the browser, window is defined → use the proxy. On the server, hit the API directly.
const isBrowser = typeof window !== 'undefined'
const baseUrl = isBrowser ? '/api/proxy' : API_URL
const headers = isBrowser ? clientHeaders : serverHeaders

export async function fetchStats() {
  const res = await fetch(`${baseUrl}/pipeline/stats`, { headers, next: { revalidate: 60 } })
  if (!res.ok) throw new Error('Failed to fetch stats')
  return res.json()
}

/**
 * /actions/today — ranked TodayAction queue for the home page.
 * Backed by api/actions.py. The shape matches dashboard/src/lib/types/today.ts.
 */
export interface FetchTodayResponse {
  actions: import('./types/today').TodayAction[]
  total: number
  counts: Record<string, number>
  generated_at: string
}

export async function fetchTodayActions(limit = 8): Promise<FetchTodayResponse> {
  const res = await fetch(`${baseUrl}/actions/today?limit=${limit}`, {
    headers,
    next: { revalidate: 30 },
  })
  if (!res.ok) throw new Error(`Failed to fetch today actions (${res.status})`)
  return res.json()
}

export async function fetchJobs(params?: {
  status?: string
  min_score?: number
  limit?: number
}) {
  const query = new URLSearchParams()
  if (params?.status) query.set('status', params.status)
  if (params?.min_score !== undefined) query.set('min_score', String(params.min_score))
  if (params?.limit !== undefined) query.set('limit', String(params.limit))

  const res = await fetch(`${baseUrl}/jobs?${query}`, { headers, next: { revalidate: 30 } })
  if (!res.ok) throw new Error('Failed to fetch jobs')
  return res.json()
}

export async function fetchCompanies() {
  const res = await fetch(`${baseUrl}/companies`, { headers, next: { revalidate: 120 } })
  if (!res.ok) throw new Error('Failed to fetch companies')
  return res.json()
}

export async function fetchDigest() {
  const res = await fetch(`${baseUrl}/digest/latest`, { headers, next: { revalidate: 3600 } })
  if (!res.ok) throw new Error('Failed to fetch digest')
  return res.json()
}

export async function triggerPipeline(options?: {
  company?: string
  role?: string
  skip_scout?: boolean
}) {
  const res = await fetch(`${baseUrl}/pipeline/run`, {
    method: 'POST',
    headers,
    body: JSON.stringify(options || {}),
  })
  if (!res.ok) throw new Error('Failed to trigger pipeline')
  return res.json()
}

export async function triggerBossAudit() {
  const res = await fetch(`${baseUrl}/boss/audit`, {
    method: 'POST',
    headers,
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error('Failed to trigger boss audit')
  return res.json()
}

export async function evaluateJob(data: {
  jd_text: string
  company: string
  job_title: string
  job_url?: string
}) {
  const res = await fetch(`${baseUrl}/pipeline/evaluate`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to evaluate job')
  return res.json()
}

export function getScoreGrade(score: number): string {
  if (score >= 85) return 'A+'
  if (score >= 75) return 'A'
  if (score >= 65) return 'B'
  if (score >= 50) return 'C'
  if (score >= 40) return 'D'
  return 'F'
}

export function getScoreClass(score: number): string {
  if (score >= 75) return 'score-a'
  if (score >= 65) return 'score-b'
  if (score >= 50) return 'score-c'
  if (score >= 40) return 'score-d'
  return 'score-f'
}

export function getStatusClass(status: string): string {
  return `status-${status}` || 'status-new'
}

// ──────────────────────────────────────────────────────────────────────
// G8 Offer Evaluation (Tier 4) — see api/offers.py
// ──────────────────────────────────────────────────────────────────────
import type {
  EvaluateOfferRequest,
  OfferDecisionRequest,
  OfferEvaluation,
} from './types/offer'

export async function evaluateOffer(
  body: EvaluateOfferRequest,
): Promise<OfferEvaluation> {
  const res = await fetch(`${baseUrl}/offers/evaluate-offer`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(`evaluateOffer failed (${res.status}): ${txt}`)
  }
  return res.json()
}

export async function fetchOfferEvaluation(
  evaluationId: string,
): Promise<OfferEvaluation> {
  const res = await fetch(`${baseUrl}/offers/${evaluationId}`, {
    headers,
    next: { revalidate: 30 },
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(`fetchOfferEvaluation failed (${res.status}): ${txt}`)
  }
  return res.json()
}

export async function updateOfferDecision(
  evaluationId: string,
  body: OfferDecisionRequest,
): Promise<{ id: string; user_decision: string; final_total_comp?: number }> {
  const res = await fetch(`${baseUrl}/offers/${evaluationId}/decision`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(`updateOfferDecision failed (${res.status}): ${txt}`)
  }
  return res.json()
}

export async function regenerateOfferEvaluation(
  evaluationId: string,
  force = false,
): Promise<OfferEvaluation> {
  const res = await fetch(`${baseUrl}/offers/${evaluationId}/regenerate`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ force }),
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(`regenerateOfferEvaluation failed (${res.status}): ${txt}`)
  }
  return res.json()
}
