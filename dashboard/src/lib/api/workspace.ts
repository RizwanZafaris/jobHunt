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
  FullRebuildRequest,
  FullRebuildResponse,
  MarkAppliedResponse,
  RebuildSectionRequest,
  RebuildSectionResponse,
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
 * A retryable build gate (e.g. persona quality too low, posting closed,
 * validation failed). Carries the structured backend payload so the UI can
 * render the real message and offer a "Build anyway" (force=true) retry.
 */
export class ResumeBuildGateError extends Error {
  code: string
  status: number
  retryWithForce: boolean
  raw: Record<string, unknown>
  constructor(args: {
    code: string
    status: number
    message: string
    retryWithForce: boolean
    raw: Record<string, unknown>
  }) {
    super(args.message)
    this.name = 'ResumeBuildGateError'
    this.code = args.code
    this.status = args.status
    this.retryWithForce = args.retryWithForce
    this.raw = args.raw
  }
}

/**
 * Throw a typed error from a non-2xx response, including the body when
 * possible. Centralised so every callsite reports failures consistently.
 *
 * When the backend returns a STRUCTURED detail object with a `code` (the
 * persona-quality / posting-closed / validation-failed gates), throw a typed
 * {@link ResumeBuildGateError} that preserves `retry_with_force` so callers can
 * offer a force retry — instead of flattening it to an opaque string.
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
        const dt = json.detail as Record<string, unknown>
        const message =
          typeof dt.message === 'string'
            ? dt.message
            : typeof dt.code === 'string'
              ? (dt.code as string)
              : detail
        if (typeof dt.code === 'string') {
          // 409 gates (posting_closed/validation_failed) all say "pass
          // force=true to override"; 400 persona gate sets retry_with_force.
          const retryWithForce =
            dt.retry_with_force === true || res.status === 409
          throw new ResumeBuildGateError({
            code: dt.code as string,
            status: res.status,
            message,
            retryWithForce,
            raw: dt,
          })
        }
        detail = message
      }
    } catch (e) {
      if (e instanceof ResumeBuildGateError) throw e
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

// ── POST /workspace/{job_id}/rebuild-section ──────────────────────────────
/**
 * Rebuild ONE H2 section synchronously. The endpoint runs a 3-call
 * mini-graph (writer → critic → polish) over just the named section
 * and returns the full resume markdown after splicing the rebuilt
 * section back in.
 *
 * Wall-clock budget: ~30-60s. The endpoint itself has a 60s cap; if
 * the pipeline overruns we get a 504-shaped error and the caller
 * surfaces "switch to a smaller section or fall back to Quick tweak".
 */
export async function rebuildSection(
  jobId: number | string,
  body: RebuildSectionRequest,
): Promise<RebuildSectionResponse> {
  const url = `${baseUrl}/workspace/${jobId}/rebuild-section`
  const res = await fetch(url, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(body),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'rebuildSection')
  return (await res.json()) as RebuildSectionResponse
}

// ── POST /workspace/{job_id}/full-rebuild ─────────────────────────────────
/**
 * Enqueue a full G2 rebuild from scratch. Returns immediately with the
 * jobs_runs id; the caller polls /jobs-runs/{run_id} every ~8s same
 * as the "Build resume" button on the Resume tab.
 *
 * Cost: ~$1, ~3-5 min. Always force=true on the backend so the
 * idempotency hash differs from any prior run on the same job.
 */
export async function fullRebuildResume(
  jobId: number | string,
  body?: FullRebuildRequest,
): Promise<FullRebuildResponse> {
  const url = `${baseUrl}/workspace/${jobId}/full-rebuild`
  const res = await fetch(url, {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(body ?? {}),
    cache: 'no-store',
  })
  if (!res.ok) await throwForStatus(res, 'fullRebuildResume')
  return (await res.json()) as FullRebuildResponse
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
