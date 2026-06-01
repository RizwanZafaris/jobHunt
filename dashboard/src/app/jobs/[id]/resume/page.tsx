/**
 * /jobs/[id]/resume — view / edit / download / rate the resume built for a job.
 *
 * Closes the loop after a G2 graph build. The component does its own data
 * fetching from /resume-builds/* via the proxy; this page just wraps it
 * in the design-system shell + a "back to job" breadcrumb.
 */
import Link from 'next/link'
import { fetchJobDetail } from '@/lib/profile-api'
import ResumeWorkspace from '@/components/ResumeWorkspace'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { EmptyState } from '@/components/ui/EmptyState'

export const dynamic = 'force-dynamic'

interface JobDetailLike {
  job?: {
    id: number
    title: string
    company: string
  }
}

export default async function JobResumePage(props: { params: Promise<{ id: string }> }) {
  const params = await props.params;
  const id = Number(params.id)
  if (!Number.isFinite(id)) {
    return (
      <AppShell>
        <PageHeader title="Resume" description="Invalid job id." />
      </AppShell>
    )
  }

  let data: JobDetailLike | undefined
  try {
    data = (await fetchJobDetail(id)) as JobDetailLike
  } catch {
    data = undefined
  }
  const job = data?.job

  if (!job) {
    return (
      <AppShell>
        <PageHeader title="Resume" />
        <div className="mt-6">
          <EmptyState
            title="Job not found"
            description="This job may have been deleted or the API is unreachable."
            action={
              <Link
                href="/"
                className="text-accent hover:underline text-sm"
              >
                Back to pipeline
              </Link>
            }
          />
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow={
          <Link
            href={`/jobs/${id}`}
            className="text-2xs uppercase tracking-wider text-fg-subtle hover:text-fg"
          >
            ← {job.title} · {job.company}
          </Link>
        }
        title="Resume"
        description="Preview, edit, download, and rate the build. Your edits override the agent draft on display while keeping the original in Storage for audit."
      />
      <div className="mt-6">
        <ResumeWorkspace
          jobId={job.id}
          jobTitle={job.title}
          company={job.company}
        />
      </div>
    </AppShell>
  )
}
