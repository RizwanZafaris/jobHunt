const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

const headers = {
  'Content-Type': 'application/json',
  'X-Secret-Key': SECRET_KEY,
}

export async function fetchStats() {
  const res = await fetch(`${API_URL}/pipeline/stats`, { headers, next: { revalidate: 60 } })
  if (!res.ok) throw new Error('Failed to fetch stats')
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

  const res = await fetch(`${API_URL}/jobs?${query}`, { headers, next: { revalidate: 30 } })
  if (!res.ok) throw new Error('Failed to fetch jobs')
  return res.json()
}

export async function fetchCompanies() {
  const res = await fetch(`${API_URL}/companies`, { headers, next: { revalidate: 120 } })
  if (!res.ok) throw new Error('Failed to fetch companies')
  return res.json()
}

export async function fetchDigest() {
  const res = await fetch(`${API_URL}/digest/latest`, { headers, next: { revalidate: 3600 } })
  if (!res.ok) throw new Error('Failed to fetch digest')
  return res.json()
}

export async function triggerPipeline(options?: {
  company?: string
  role?: string
  skip_scout?: boolean
}) {
  const res = await fetch(`${API_URL}/pipeline/run`, {
    method: 'POST',
    headers,
    body: JSON.stringify(options || {}),
  })
  if (!res.ok) throw new Error('Failed to trigger pipeline')
  return res.json()
}

export async function triggerBossAudit() {
  const res = await fetch(`${API_URL}/boss/audit`, {
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
  const res = await fetch(`${API_URL}/pipeline/evaluate`, {
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
