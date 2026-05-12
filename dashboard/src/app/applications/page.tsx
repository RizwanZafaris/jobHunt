import { fetchApplications } from '@/lib/profile-api'
import ApplicationsBoard from '@/components/ApplicationsBoard'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'

export const dynamic = 'force-dynamic'

export default async function ApplicationsPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchApplications()
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load applications'
  }

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Funnel"
        title="Applications pipeline"
        description="Track each application as it moves through the funnel. Status changes save to Supabase immediately."
        actions={
          <span
            className="text-2xs text-fg-subtle cursor-help"
            title="Distinct applications submitted (any status). Does not include resumes built but not applied — those live on /today as “Ready to apply”."
          >
            Total: <span className="text-fg font-semibold tnum">{data?.total ?? 0}</span>
          </span>
        }
      />

      {error && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger">{error}</p>
        </Card>
      )}

      {!error && data && <ApplicationsBoard initial={data} />}
    </AppShell>
  )
}
