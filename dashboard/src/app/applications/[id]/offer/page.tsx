/**
 * /applications/[id]/offer — G8 Offer Evaluation surface.
 *
 * Two modes:
 *   1. No evaluation row yet → render the OfferPasteForm. User pastes
 *      the offer text + clicks Evaluate → backend runs the 5-node G8
 *      graph and returns the persisted row.
 *   2. Evaluation exists → render the comp summary, market bands,
 *      negotiation script, risk factors, recommendation, and the
 *      decision form.
 *
 * Backend: api/offers.py — POST /offers/evaluate-offer + GET + PATCH +
 * POST /regenerate.
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

async function fetchExistingEvaluation(evaluationId: string) {
  // Server-side direct fetch
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
  const SECRET_KEY = process.env.API_SECRET_KEY || ''
  const res = await fetch(`${API_URL}/offers/${evaluationId}`, {
    headers: {
      'Content-Type': 'application/json',
      'X-Secret-Key': SECRET_KEY,
    },
    cache: 'no-store',
  })
  if (!res.ok) return null
  return res.json()
}

export default async function OfferEvaluationPage({
  params,
  searchParams,
}: PageProps) {
  const { id: applicationId } = await params
  const { evaluation_id: evaluationId } = await searchParams

  const existing = evaluationId
    ? await fetchExistingEvaluation(evaluationId)
    : null

  return (
    <AppShell>
      <PageHeader
        eyebrow={`Application ${applicationId.slice(0, 8)}…`}
        title="Offer evaluation"
        description="The 5-node G8 graph parses your offer, pulls market bands, drafts a negotiation script, scores risk, and synthesises a recommendation. Costs ~$0.40."
      />

      <nav className="text-2xs text-fg-subtle">
        <Link
          href={`/applications/${applicationId}/workspace`}
          className="hover:text-fg underline-offset-2 hover:underline"
        >
          ← Back to application workspace
        </Link>
      </nav>

      <OfferClient
        applicationId={applicationId}
        initialEvaluation={existing}
      />
    </AppShell>
  )
}
