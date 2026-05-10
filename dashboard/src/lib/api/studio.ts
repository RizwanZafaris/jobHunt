/**
 * Phase 3 — Interview Studio API client.
 *
 * Backed by api/interview_studio.py. Mirrors the proxy / direct-call pattern
 * used in dashboard/src/lib/api.ts so server components can fetch directly
 * with the secret key while client components route through /api/proxy/*.
 *
 * DO NOT edit dashboard/src/lib/api.ts — keep this file self-contained.
 */
import type {
  BuildPrepPackResponse,
  ConceptLevel,
  InterviewStudio,
  LogOutcomeResponse,
  OutcomeLogInput,
  TutorChatResponse,
} from '@/lib/types/interview-studio'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

const serverHeaders = {
  'Content-Type': 'application/json',
  'X-Secret-Key': SECRET_KEY,
}

const clientHeaders = {
  'Content-Type': 'application/json',
}

const isBrowser = typeof window !== 'undefined'
const baseUrl = isBrowser ? '/api/proxy' : API_URL
const headers = isBrowser ? clientHeaders : serverHeaders

function studioPath(applicationId: string, suffix = ''): string {
  const trimmed = suffix.startsWith('/') ? suffix : suffix ? `/${suffix}` : ''
  return `${baseUrl}/interview-studio/${encodeURIComponent(applicationId)}${trimmed}`
}

async function asJson<T>(res: Response, label: string): Promise<T> {
  if (!res.ok) {
    let detail = ''
    try {
      const body = (await res.json()) as { detail?: unknown }
      if (typeof body?.detail === 'string') detail = `: ${body.detail}`
    } catch {
      // ignore
    }
    throw new Error(`${label} failed (${res.status})${detail}`)
  }
  return (await res.json()) as T
}

/**
 * GET /interview-studio/{application_id}
 *
 * Server component: fetched once on first paint with `next: { revalidate: 30 }`.
 * Browser: revalidate on user actions (router.refresh()) — no auto-poll.
 */
export async function fetchInterviewStudio(
  applicationId: string,
): Promise<InterviewStudio> {
  const res = await fetch(studioPath(applicationId), {
    headers,
    next: { revalidate: 30 },
  })
  return asJson<InterviewStudio>(res, 'fetchInterviewStudio')
}

/**
 * POST /interview-studio/{application_id}/tutor-chat
 *
 * One tutor turn. Caller passes the user's message and the concept level
 * they want the tutor to engage at; the response includes the recommended
 * next level so the UI can suggest a bump.
 */
export async function tutorChat(
  applicationId: string,
  message: string,
  conceptLevel: ConceptLevel,
): Promise<TutorChatResponse> {
  const res = await fetch(studioPath(applicationId, 'tutor-chat'), {
    method: 'POST',
    headers,
    body: JSON.stringify({ message, concept_level: conceptLevel }),
  })
  return asJson<TutorChatResponse>(res, 'tutorChat')
}

/**
 * POST /interview-studio/{application_id}/log-outcome
 *
 * Inserts an interview_outcomes row and triggers credit_outcome inline.
 * The credit_summary in the response tells the UI how many knowledge rows
 * were credited so we can show "n knowledge rows updated".
 */
export async function logOutcome(
  applicationId: string,
  payload: OutcomeLogInput,
): Promise<LogOutcomeResponse> {
  const res = await fetch(studioPath(applicationId, 'log-outcome'), {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })
  return asJson<LogOutcomeResponse>(res, 'logOutcome')
}

/**
 * POST /interview-studio/{application_id}/build-prep-pack
 *
 * Enqueues a G3 prep-pack build. Returns the jobs_runs.id so the UI can
 * poll status (out-of-scope here — Phase 1 surfaces queue health on /today).
 */
export async function buildPrepPack(
  applicationId: string,
  options: { roundType?: string; roundNumber?: number; maxCostUsd?: number } = {},
): Promise<BuildPrepPackResponse> {
  const body = {
    round_type: options.roundType ?? 'hm',
    round_number: options.roundNumber ?? 1,
    max_cost_usd: options.maxCostUsd,
  }
  const res = await fetch(studioPath(applicationId, 'build-prep-pack'), {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  return asJson<BuildPrepPackResponse>(res, 'buildPrepPack')
}
