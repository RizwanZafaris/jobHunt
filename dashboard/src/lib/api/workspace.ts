/**
 * Workspace API client — all five `/workspace/*` endpoints.
 *
 * Lives at dashboard/src/lib/api/workspace.ts (NOT dashboard/src/lib/api.ts —
 * that file is owned by the Phase-1 / Phase-3 agents this session must
 * not collide with). Import via:
 *
 *   import { fetchWorkspace } from '@/lib/api/workspace'
 *
 * Same X-Secret-Key + /api/proxy pattern as the rest of the dashboard:
 *   - Server Components hit the API directly with the secret header.
 *   - Client Components hit /api/proxy/* (the secret stays server-side).
 */
import type {
  BuildResumeResponse,
  EditResumeRequest,
  EditResumeResponse,
  MarkAppliedResponse,
  SaveResumeEditResponse,
  Workspace,
} from '../types/workspace'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

const isBrowser = typeof window !== 'undefined'
const baseUrl = isBrowser ? '/api/proxy' : API_URL

function buildHeaders(extra?: Record<string, string>): Record<string, string> {
  // On the server we attach the secret; in the browser we let the proxy
  // route inject it (so no secret leaks to client JS).
  const base: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (!isBrowser && SECRET_KEY) {
    base['X-Secret-Key'] = SECRET_KEY
  }
  return extra ? { ...base, ...extra } : base
}

/**
 * Throw a typed error from a non-2xx response, including the body when
 * possible. Centralised so every callsite reports failures consistently.
 */
async function throwForStatus(res: Response, op: string): Promise<never> {
  let body: string | null = null
  try {
    body = await res.text()
  } catch {
    body = null
  }
  let detail: string = `${res.status} ${res.statusText}`
  if (body) {
    try {
      const json = JSON.parse(body) as { detail?: unknown }
      if (typeof json.detail === 'string') {
        detail = json.detail
      } else if (json.detail && typeof json.detail === 'object') {
        const dt = json.detail as { message?: unknown; code?: unknown }
        if (typeof dt.message === 'string') detail = dt.message
        else if (typeof dt.code === 'string') detail = dt.code
      }
    } catch {
      detail = body.slice(0, 240)
    }
  }
  throw new Error(`${op} failed (${res.status}): ${detail}`)
}

// ── GET /workspace/{job_id} ────────────────────────────────────────────────
export async function fetchWorkspace(jobId: number | string): Promise<Workspace> {
  const url = `${baseUrl}/workspace/${jobId}`
  const res = await fetch(url, {
    headers: buildHeaders(),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'fetchWorkspace')
  return (await res.json()) as Workspace
}

// ── POST /workspace/{job_id}/build-resume ─────────────────────────────────
export async function buildResume(
  jobId: number | string,
  options?: { force?: boolean; maxCostUsd?: number },
): Promise<BuildResumeResponse> {
  const params = new URLSearchParams()
  if (options?.force) params.set('force', 'true')
  if (options?.maxCostUsd !== undefined) {
    params.set('max_cost_usd', String(options.maxCostUsd))
  }
  const qs = params.toString()
  const url = `${baseUrl}/workspace/${jobId}/build-resume${qs ? `?${qs}` : ''}`
  const res = await fetch(url, {
    method: 'POST',
    headers: buildHeaders(),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'buildResume')
  return (await res.json()) as BuildResumeResponse
}

// ── POST /workspace/{job_id}/edit-resume ──────────────────────────────────
export async function editResume(
  jobId: number | string,
  body: EditResumeRequest,
): Promise<EditResumeResponse> {
  const payload: EditResumeRequest = {
    mode: body.mode ?? 'quick_tweak',
    instruction: body.instruction,
    current_md: body.current_md,
    chat_history: body.chat_history,
  }
  const url = `${baseUrl}/workspace/${jobId}/edit-resume`
  const res = await fetch(url, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(payload),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'editResume')
  return (await res.json()) as EditResumeResponse
}

// ── POST /workspace/{job_id}/save-resume-edit ─────────────────────────────
export async function saveResumeEdit(
  jobId: number | string,
  editedMd: string,
  buildId?: string,
): Promise<SaveResumeEditResponse> {
  const url = `${baseUrl}/workspace/${jobId}/save-resume-edit`
  const res = await fetch(url, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ edited_md: editedMd, build_id: buildId }),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'saveResumeEdit')
  return (await res.json()) as SaveResumeEditResponse
}

// ── POST /workspace/{job_id}/mark-applied ─────────────────────────────────
export async function markApplied(
  jobId: number | string,
  body?: { applied_date?: string; notes?: string },
): Promise<MarkAppliedResponse> {
  const url = `${baseUrl}/workspace/${jobId}/mark-applied`
  const res = await fetch(url, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(body ?? {}),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'markApplied')
  return (await res.json()) as MarkAppliedResponse
}

// ── Helpers exposed for the UI's polling loop ────────────────────────────

/**
 * Poll a jobs_runs row until it terminates. Returns the final row or
 * throws if the timeout is hit. Used by the Resume tab's "Building…"
 * state — the workspace bundle returns only the last build, but a fresh
 * G2 may take ~5 minutes, so the UI polls.
 *
 * Defaults: 8s interval, 10 minute ceiling.
 */
export interface JobsRunRow {
  id: string
  user_id: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  attempts?: number
  last_error?: string | null
  result?: Record<string, unknown> | null
  started_at?: string | null
  finished_at?: string | null
  created_at?: string | null
}

export async function fetchJobsRun(runId: string): Promise<JobsRunRow> {
  const url = `${baseUrl}/jobs-runs/${runId}`
  const res = await fetch(url, {
    headers: buildHeaders(),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'fetchJobsRun')
  return (await res.json()) as JobsRunRow
}

// ── Download URLs ─────────────────────────────────────────────────────────
/**
 * Build the absolute download URL for a given format.
 *
 * Browser-side: through the proxy (preserves auth header injection).
 * Server-side: not used (the Server Component renders <a href> for the
 * browser to follow).
 */
export function downloadResumeHref(
  jobId: number | string,
  fmt: 'md' | 'pdf' | 'docx',
): string {
  return `/api/proxy/workspace/${jobId}/resume.${fmt}`
}
