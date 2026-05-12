/**
 * /applications/[id]/assist — Tier 3 G7 application-form assist surface.
 *
 * The user lands here after POST /workspace/{job_id}/apply has run and
 * returned an application_answer_id (`id` in the URL). This page is the
 * HITL approval surface: it polls the row, surfaces each question +
 * draft + persona critique, and offers per-field Approve / Edit /
 * Regenerate. When everything is approved, "Approve all & fill" posts
 * to /approve which resumes the graph past the interrupt.
 *
 * Server Component fetches the initial state via /workspace/applications/
 * {id}. All interactivity lives inside <AssistClient/>.
 */
import { notFound } from 'next/navigation'
import { AppShell } from '@/components/layout/AppShell'
import { AssistClient } from '@/components/applications/AssistClient'

export const dynamic = 'force-dynamic'

interface AssistPageProps {
  params: Promise<{ id: string }>
}

async function fetchApplicationAnswers(applicationAnswerId: string): Promise<any | null> {
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
  const SECRET_KEY = process.env.API_SECRET_KEY || ''
  try {
    const res = await fetch(
      `${API_URL}/workspace/applications/${applicationAnswerId}`,
      {
        cache: 'no-store',
        headers: {
          'Content-Type': 'application/json',
          ...(SECRET_KEY ? { 'X-Secret-Key': SECRET_KEY } : {}),
        },
      },
    )
    if (!res.ok) return null
    return await res.json()
  } catch {
    return null
  }
}

export default async function AssistPage(props: AssistPageProps) {
  const params = await props.params
  const id = params.id
  if (!id || id.length < 8) notFound()

  const initialState = await fetchApplicationAnswers(id)
  if (!initialState) notFound()

  return (
    <AppShell wide>
      <AssistClient initialState={initialState} applicationAnswerId={id} />
    </AppShell>
  )
}
