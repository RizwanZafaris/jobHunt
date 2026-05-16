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

export async function fetchNetworkPeople() {
  const res = await fetch(`${baseUrl}/network/people`, { headers, next: { revalidate: 60 } })
  if (!res.ok) throw new Error(`Failed to fetch network people (${res.status})`)
  return res.json()
}

export async function fetchTargetCoverage() {
  const res = await fetch(`${baseUrl}/network/target-coverage`, { headers, next: { revalidate: 60 } })
  if (!res.ok) throw new Error(`Failed to fetch target coverage (${res.status})`)
  return res.json()
}

export async function fetchNetworkPathsForTarget(targetCompanyId: string) {
  const res = await fetch(
    `${baseUrl}/network/paths?target_company_id=${encodeURIComponent(targetCompanyId)}`,
    { headers, next: { revalidate: 60 } },
  )
  if (!res.ok) throw new Error(`Failed to fetch network paths (${res.status})`)
  return res.json()
}

export async function submitDraftIntro(data: {
  target_company_id: string
  introducer_person_id: string
  target_role_summary?: string
}) {
  const res = await fetch(`${baseUrl}/network/draft-intro`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Failed to draft intro (${res.status})`)
  }
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

// ──────────────────────────────────────────────────────────────────────
// Pattern Analytics (Audit Gap §4.3) — see api/analytics.py
// ──────────────────────────────────────────────────────────────────────
import type {
  FunnelResponse,
  PartitionedFunnelResponse,
  RejectionPatternsResponse,
  CostEfficiencyResponse,
} from './types/analytics'

export async function fetchFunnel(): Promise<FunnelResponse> {
  const res = await fetch(`${baseUrl}/analytics/funnel`, {
    headers,
    next: { revalidate: 60 },
  })
  if (!res.ok) throw new Error(`fetchFunnel failed (${res.status})`)
  return res.json()
}

export async function fetchFunnelByGrade(): Promise<PartitionedFunnelResponse> {
  const res = await fetch(`${baseUrl}/analytics/funnel-by-grade`, {
    headers,
    next: { revalidate: 60 },
  })
  if (!res.ok) throw new Error(`fetchFunnelByGrade failed (${res.status})`)
  return res.json()
}

export async function fetchFunnelByArchetype(): Promise<PartitionedFunnelResponse> {
  const res = await fetch(`${baseUrl}/analytics/funnel-by-archetype`, {
    headers,
    next: { revalidate: 60 },
  })
  if (!res.ok) throw new Error(`fetchFunnelByArchetype failed (${res.status})`)
  return res.json()
}

export async function fetchFunnelBySize(): Promise<PartitionedFunnelResponse> {
  const res = await fetch(`${baseUrl}/analytics/funnel-by-size`, {
    headers,
    next: { revalidate: 60 },
  })
  if (!res.ok) throw new Error(`fetchFunnelBySize failed (${res.status})`)
  return res.json()
}

export async function fetchRejectionPatterns(
  minSample = 5,
): Promise<RejectionPatternsResponse> {
  const res = await fetch(
    `${baseUrl}/analytics/rejection-patterns?min_sample=${minSample}`,
    { headers, next: { revalidate: 60 } },
  )
  if (!res.ok) throw new Error(`fetchRejectionPatterns failed (${res.status})`)
  return res.json()
}

export async function fetchCostEfficiency(
  minOutcomes = 1,
): Promise<CostEfficiencyResponse> {
  const res = await fetch(
    `${baseUrl}/analytics/cost-efficiency?min_outcomes=${minOutcomes}`,
    { headers, next: { revalidate: 60 } },
  )
  if (!res.ok) throw new Error(`fetchCostEfficiency failed (${res.status})`)
  return res.json()
}

// ──────────────────────────────────────────────────────────────────────
// LinkedIn (BUG-LinkedIn-Mock fix 2026-05-13)
// Was previously mocked via @/lib/mock/linkedin — page now hits the
// real /linkedin/* endpoints (already wired in api/server.py).
// ──────────────────────────────────────────────────────────────────────
import type { LinkedInDraft } from './types/linkedin'

/**
 * Map a snake_case linkedin_drafts row from PostgREST → the camelCase
 * LinkedInDraft shape the dashboard components expect. Without this,
 * fields like `draft.imageBrief`, `draft.sourceCompanyName`,
 * `draft.scheduledFor`, `draft.whyItWorks` are undefined → the
 * ImageBriefPanel never renders, the source pill shows blank, etc.
 *
 * Defensive against partial rows: every field that should be present
 * gets a sensible default; unknown extras pass through.
 */
function mapDraftRow(row: Record<string, unknown>): LinkedInDraft {
  const imageBriefRaw = (row.image_brief ?? row.imageBrief) as Record<string, unknown> | null | undefined
  const imageBrief = imageBriefRaw
    ? {
        kind: (imageBriefRaw.kind ?? 'data_viz') as LinkedInDraft['imageBrief'] extends infer T
          ? T extends null
            ? never
            : T extends { kind: infer K }
              ? K
              : never
          : never,
        prompt: String(imageBriefRaw.prompt ?? ''),
        compositionNotes: String(
          imageBriefRaw.composition_notes ?? imageBriefRaw.compositionNotes ?? '',
        ),
        dataAnchors: Array.isArray(imageBriefRaw.data_anchors ?? imageBriefRaw.dataAnchors)
          ? (imageBriefRaw.data_anchors ?? imageBriefRaw.dataAnchors)
          : [],
        referenceUrl:
          (imageBriefRaw.reference_url ?? imageBriefRaw.referenceUrl ?? null) as string | null,
        altText: String(imageBriefRaw.alt_text ?? imageBriefRaw.altText ?? ''),
        recommendedProvider: String(
          imageBriefRaw.recommended_provider ?? imageBriefRaw.recommendedProvider ?? 'dall-e-3',
        ),
        aiDisclosureRecommended: Boolean(
          imageBriefRaw.ai_disclosure_recommended ?? imageBriefRaw.aiDisclosureRecommended ?? false,
        ),
        rationale: String(imageBriefRaw.rationale ?? ''),
      }
    : null
  return {
    id: String(row.id ?? ''),
    userId: String(row.user_id ?? row.userId ?? ''),
    sourceCompanyId: (row.source_company_id ?? row.sourceCompanyId ?? null) as string | null,
    sourceKnowledgeId: (row.source_knowledge_id ?? row.sourceKnowledgeId ?? null) as string | null,
    sourceCompanyName: (row.source_company_name ?? row.sourceCompanyName ?? null) as string | null,
    sourceUrl: (row.source_url ?? row.sourceUrl ?? null) as string | null,
    angle: (row.angle ?? 'industry_analysis') as LinkedInDraft['angle'],
    hook: String(row.hook ?? ''),
    body: String(row.body ?? ''),
    cta: (row.cta ?? null) as string | null,
    hashtags: Array.isArray(row.hashtags) ? (row.hashtags as string[]) : [],
    status: (row.status ?? 'draft') as LinkedInDraft['status'],
    critique: (row.critique ?? null) as LinkedInDraft['critique'],
    polishRound: Number(row.polish_round ?? row.polishRound ?? 0),
    scheduledFor: (row.scheduled_for ?? row.scheduledFor ?? null) as string | null,
    postedAt: (row.posted_at ?? row.postedAt ?? null) as string | null,
    postedUrl: (row.posted_url ?? row.postedUrl ?? null) as string | null,
    manualCopyAt: (row.manual_copy_at ?? row.manualCopyAt ?? null) as string | null,
    engagementMetrics: (row.engagement_metrics ?? row.engagementMetrics ?? null) as LinkedInDraft['engagementMetrics'],
    whyItWorks: (row.why_it_works ?? row.whyItWorks ?? null) as string | null,
    imageBrief: imageBrief as LinkedInDraft['imageBrief'],
    createdAt: String(row.created_at ?? row.createdAt ?? ''),
    updatedAt: String(row.updated_at ?? row.updatedAt ?? ''),
  }
}

export async function fetchLinkedInDrafts(): Promise<LinkedInDraft[]> {
  const res = await fetch(`${baseUrl}/linkedin/drafts`, {
    headers,
    next: { revalidate: 30 },
  })
  if (!res.ok) throw new Error(`fetchLinkedInDrafts failed (${res.status})`)
  const data = await res.json()
  // Backend (api/linkedin.py::list_drafts) returns
  // { items, total, limit, offset }. Earlier draft of this fetcher read
  // `data.drafts` which is undefined → page rendered Drafts (0) even when
  // the DB had real rows. Accept the actual shape + a few common variants.
  const rows = (Array.isArray(data) ? data : (data.items ?? data.drafts ?? [])) as Array<Record<string, unknown>>
  return rows.map(mapDraftRow)
}

// ── Draft lifecycle fetchers (BUG: handlers were mocks until 2026-05-14) ─

export async function patchLinkedInDraft(
  draftId: string,
  body: { hook?: string; body?: string; cta?: string | null; hashtags?: string[] },
): Promise<LinkedInDraft> {
  const res = await fetch(`${baseUrl}/linkedin/drafts/${draftId}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`patchLinkedInDraft failed (${res.status}): ${text}`)
  }
  return mapDraftRow(await res.json())
}

export async function approveLinkedInDraft(
  draftId: string,
  scheduledFor?: string | null,
): Promise<LinkedInDraft> {
  const res = await fetch(`${baseUrl}/linkedin/drafts/${draftId}/approve`, {
    method: 'POST',
    headers,
    body: JSON.stringify(scheduledFor ? { scheduled_for: scheduledFor } : {}),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`approveLinkedInDraft failed (${res.status}): ${text}`)
  }
  return mapDraftRow(await res.json())
}

export async function rejectLinkedInDraft(draftId: string, reason?: string): Promise<LinkedInDraft> {
  const res = await fetch(`${baseUrl}/linkedin/drafts/${draftId}/reject`, {
    method: 'POST',
    headers,
    body: JSON.stringify(reason ? { reason } : {}),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`rejectLinkedInDraft failed (${res.status}): ${text}`)
  }
  return mapDraftRow(await res.json())
}

export async function recordCopyLinkedInDraft(draftId: string): Promise<LinkedInDraft | null> {
  // Records manual_copy_at server-side. Non-fatal — clipboard already
  // worked client-side, so failures here just lose the telemetry.
  try {
    const res = await fetch(`${baseUrl}/linkedin/drafts/${draftId}/copy`, {
      method: 'POST',
      headers,
      body: '{}',
    })
    if (!res.ok) return null
    return mapDraftRow(await res.json())
  } catch {
    return null
  }
}

export async function fetchLinkedInSchedule(): Promise<
  import('./types/linkedin').LinkedInPostingSchedule
> {
  const res = await fetch(`${baseUrl}/linkedin/posting-schedule`, {
    headers,
    next: { revalidate: 300 },
  })
  if (!res.ok) throw new Error(`fetchLinkedInSchedule failed (${res.status})`)
  // Backend returns snake_case: { slots, posts_per_week, next_slot }
  // Map to the camelCase shape the dashboard expects. Earlier draft
  // returned the raw response which made stats show
  // "Cadence target: undefined/week".
  const raw = await res.json()
  return {
    slots: raw.slots ?? [],
    postsPerWeek: raw.postsPerWeek ?? raw.posts_per_week ?? 0,
    nextSlot: raw.nextSlot ?? raw.next_slot ?? null,
  }
}

// LangGraph traces (Stream C) — see api/traces.py
// ──────────────────────────────────────────────────────────────────────
import type {
  RecentRunsResponse,
  TraceDetailResponse,
  ErrorsResponse,
} from './types/traces'

export async function fetchRecentRuns(limit = 50): Promise<RecentRunsResponse> {
  const res = await fetch(`${baseUrl}/traces/recent?limit=${limit}`, {
    headers,
    next: { revalidate: 15 },
  })
  if (!res.ok) throw new Error(`fetchRecentRuns failed (${res.status})`)
  return res.json()
}

export async function fetchTraceDetail(
  graph: string,
  runKey: string,
): Promise<TraceDetailResponse> {
  const res = await fetch(
    `${baseUrl}/traces/${encodeURIComponent(graph)}/${encodeURIComponent(runKey)}`,
    { headers, next: { revalidate: 15 } },
  )
  if (!res.ok) throw new Error(`fetchTraceDetail failed (${res.status})`)
  return res.json()
}

export async function fetchTraceErrors(limit = 20): Promise<ErrorsResponse> {
  const res = await fetch(`${baseUrl}/traces/errors?limit=${limit}`, {
    headers,
    next: { revalidate: 15 },
  })
  if (!res.ok) throw new Error(`fetchTraceErrors failed (${res.status})`)
  return res.json()
}

// ──────────────────────────────────────────────────────────────────────
// /today post-of-the-day + /applications incoming (Stream D, 2026-05-13)
// ──────────────────────────────────────────────────────────────────────

export interface PostOfTheDay {
  id: string
  hook: string
  body_excerpt: string
  angle: string | null
  company: string | null
  status: string
  scheduled_for: string | null
  created_at: string | null
}

export interface IncomingJob {
  job_id: number
  title: string
  company: string
  score: number
  letter_grade: string | null
  archetype: string | null
  created_at: string | null
}

export async function fetchPostOfTheDay(): Promise<PostOfTheDay | null> {
  const res = await fetch(`${baseUrl}/actions/today/linkedin-post`, {
    headers,
    next: { revalidate: 60 },
  })
  if (!res.ok) return null
  const data = await res.json()
  return data.post ?? null
}

export async function fetchIncomingJobs(limit = 10): Promise<IncomingJob[]> {
  const res = await fetch(`${baseUrl}/actions/incoming?limit=${limit}`, {
    headers,
    next: { revalidate: 60 },
  })
  if (!res.ok) return []
  const data = await res.json()
  return data.jobs ?? []
}
