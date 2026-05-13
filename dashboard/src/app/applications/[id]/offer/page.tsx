/**
 * /applications/[id]/offer — G8 Offer Evaluation surface.
 *
 * BUG-058 fix (2026-05-13): the URL param `[id]` follows the existing
 * `/applications/[id]/workspace` convention which uses `jobs.id` (INTEGER).
 * But the G8 backend (api/offers.py) requires `application_id` (UUID).
 * We resolve the integer→UUID lookup here on the server, then pass the
 * UUID into OfferClient.
 *
 * If no applications row exists for this job yet, we show a CTA pointing
 * the user back to the workspace's "Mark as planning" flow — same pattern
 * as G7 (api/g7.py::_resolve_app_id_for_job 404 message).
 *
 * Two modes once we have an application UUID:
 *   1. No evaluation row yet → render the OfferPasteForm. User pastes
 *      the offer text + clicks Evaluate → backend runs the 5-node G8
 *      graph and returns the persisted row.
 *   2. Evaluation exists → render the comp summary, market bands,
 *      negotiation script, risk factors, recommendation, and the
 *      decision form.
 */
import Link from 'next/link'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'

import OfferClient from './OfferClient'

export const dynamic = 'force-dynamic'

interface PageProps {
  params: Promise<{ id: string }>
  searchParams: Promise<{ evaluation_id?: string }>
}

interface ApplicationRow {
  id: string
  job_id: number
  status: string
  created_at: string
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''
const serverHeaders = {
  'Content-Type': 'application/json',
  'X-Secret-Key': SECRET_KEY,
}

async function resolveApplicationId(rawId: string): Promise<ApplicationRow | null> {
  // If [id] is already a UUID, return a synthetic row + skip the lookup.
  // Helps when the page is reached via a /offers/{eval_id} → app_id direct
  // link instead of the workspace flow.
  const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
  if (uuidRe.test(rawId)) {
    return { id: rawId, job_id: 0, status: 'unknown', created_at: '' }
  }
  // Treat as integer job_id; hit the new /applications/by-job endpoint.
  const asInt = parseInt(rawId, 10)
  if (!Number.isFinite(asInt) || asInt < 1) return null
  const res = await fetch(`${API_URL}/applications/by-job/${asInt}`, {
    headers: serverHeaders,
    cache: 'no-store',
  })
  if (!res.ok) return null
  return (await res.json()) as ApplicationRow
}

async function fetchExistingEvaluation(evaluationId: string) {
  const res = await fetch(`${API_URL}/offers/${evaluationId}`, {
    headers: serverHeaders,
    cache: 'no-store',
  })
  if (!res.ok) return null
  return res.json()
}

export default async function OfferEvaluationPage({
  params,
  searchParams,
}: PageProps) {
  const { id: rawId } = await params
  const { evaluation_id: evaluationId } = await searchParams

  const appRow = await resolveApplicationId(rawId)

  if (!appRow) {
    // No application row for this job yet. G8 needs an applications row
    // to pin the evaluation to. Direct the user back to the workspace
    // to mark the job as "planning" first (same flow as G7).
    return (
      <AppShell>
        <PageHeader
          eyebrow={`Job ${rawId}`}
          title="Offer evaluation"
          description="Before evaluating an offer, mark this job as 'planning' in the workspace so we have a row to pin the evaluation to."
        />
        <div className="rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          <p className="text-fg">
            No application row exists for job <code className="font-mono">{rawId}</code> yet.
          </p>
          <p className="mt-1 text-fg-subtle">
            Go to the workspace and click <strong>Mark as planning</strong>{' '}
            (or apply directly) to create the application row first.
          </p>
          <div className="mt-3">
            <Link
              href={`/applications/${rawId}/workspace`}
              className="inline-flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-2xs font-semibold text-accent-fg hover:bg-accent-hover min-h-9"
            >
              Open workspace →
            </Link>
          </div>
        </div>
      </AppShell>
    )
  }

  const applicationUuid = appRow.id
  const existing = evaluationId ? await fetchExistingEvaluation(evaluationId) : null

  return (
    <AppShell>
      <PageHeader
        eyebrow={
          appRow.job_id > 0
            ? `Job ${appRow.job_id} · Application ${applicationUuid.slice(0, 8)}…`
            : `Application ${applicationUuid.slice(0, 8)}…`
        }
        title="Offer evaluation"
        description="The 5-node G8 graph parses your offer, pulls market bands, drafts a negotiation script, scores risk, and synthesises a recommendation. Costs ~$0.40."
      />

      <nav className="text-2xs text-fg-subtle">
        <Link
          href={`/applications/${rawId}/workspace`}
          className="hover:text-fg underline-offset-2 hover:underline"
        >
          ← Back to application workspace
        </Link>
      </nav>

      <OfferClient
        applicationId={applicationUuid}
        initialEvaluation={existing}
      />
    </AppShell>
  )
}
