/**
 * Phase 3 — /applications/[id]/interview-studio
 *
 * Async Server Component. Fetches the studio payload once (revalidate=30s)
 * and renders <StudioClient /> with the data inline so the first paint is
 * fully hydrated.
 *
 * Loading: Suspense boundary with a Skeleton shell.
 * Error:   Card tone="danger" with the message + a "Try again" link.
 */
import { fetchInterviewStudio } from '@/lib/api/studio'
import StudioClient from '@/components/interview-studio/StudioClient'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import Link from 'next/link'

export const dynamic = 'force-dynamic'

interface PageProps {
  params: Promise<{ id: string }>
}

export default async function InterviewStudioPage({ params }: PageProps) {
  const { id } = await params

  let data
  let error: string | null = null
  try {
    data = await fetchInterviewStudio(id)
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load interview studio'
  }

  if (error) {
    return (
      <AppShell wide>
        <PageHeader
          eyebrow="Interview Studio"
          title="Studio unavailable"
          description="We could not load this application's interview studio."
        />
        <Card tone="danger" padding="md">
          <p className="text-xs text-danger">{error}</p>
          <p className="text-2xs text-fg-muted mt-2">
            <Link href={`/applications/${id}/workspace`} className="underline hover:text-fg">
              Go to workspace
            </Link>{' '}
            ·{' '}
            <Link href="/applications" className="underline hover:text-fg">
              All applications
            </Link>
          </p>
        </Card>
      </AppShell>
    )
  }

  if (!data) {
    return (
      <AppShell wide>
        <PageHeader eyebrow="Interview Studio" title="Loading studio…" />
      </AppShell>
    )
  }

  const company = data.application.company || data.job.company || 'this application'
  const role = data.application.role || data.job.title || ''
  const subtitle = [company, role].filter(Boolean).join(' — ')

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Interview Studio"
        title={subtitle || 'Interview prep + tutor'}
        description="Read the prep pack, ask the tutor at the depth you want, and log each round's outcome — your persona evolves on the next Sunday cron."
        actions={
          <Link
            href={`/applications/${id}/workspace`}
            className="text-2xs underline hover:text-fg text-fg-muted"
          >
            Back to workspace
          </Link>
        }
      />
      <StudioClient applicationId={id} initial={data} />
    </AppShell>
  )
}
