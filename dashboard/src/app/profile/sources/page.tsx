import { fetchSources } from '@/lib/profile-api'
import SourcesTable from '@/components/SourcesTable'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { Stat } from '@/components/ui/Stat'
import { EmptyState } from '@/components/ui/EmptyState'

export const revalidate = 300

export default async function SourcesPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchSources()
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load sources'
  }

  if (error || !data) {
    return (
      <AppShell wide>
        <PageHeader eyebrow="Profile" title="Source documents" />
        <EmptyState
          icon="document"
          title="Sources not loaded"
          description={error ?? 'No source documents available.'}
        />
      </AppShell>
    )
  }

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Profile"
        title="Source documents"
        description="All resume, LinkedIn, and interview-prep files parsed into the profile."
      />

      <Card padding="lg">
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-5">
          <Stat label="Documents" value={data.total.toLocaleString()} />
          <Stat label="Classes" value={Object.keys(data.by_class).length} />
        </dl>
      </Card>

      <section aria-label="By document class" className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
        {Object.entries(data.by_class)
          .sort((a, b) => b[1] - a[1])
          .map(([cls, n]) => (
            <Card key={cls} padding="sm">
              <div className="text-2xs text-fg-subtle uppercase tracking-wider">{cls.replace(/_/g, ' ')}</div>
              <div className="text-xl font-semibold text-fg mt-1 tnum">{n}</div>
            </Card>
          ))}
      </section>

      <SourcesTable documents={data.documents} />
    </AppShell>
  )
}
