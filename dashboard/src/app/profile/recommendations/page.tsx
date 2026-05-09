import { fetchRecommendations } from '@/lib/profile-api'
import RecommendationsList from '@/components/RecommendationsList'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { Stat } from '@/components/ui/Stat'

export const dynamic = 'force-dynamic'

interface Severity { high?: number; medium?: number; low?: number }

export default async function RecommendationsPage() {
  let data: { total?: number; recommendations?: unknown[]; by_severity?: Severity } | undefined
  let error: string | null = null
  try {
    data = await fetchRecommendations()
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load recommendations'
  }

  const sev = (data?.by_severity || {}) as Severity

  return (
    <AppShell>
      <PageHeader
        eyebrow="Profile"
        title="Recommendations"
        description="AI-generated improvements: missing keywords, weak categories, unquantified bullets, and cross-document conflicts."
      />

      <Card padding="lg">
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-5">
          <Stat label="Total open" value={(data?.total ?? 0).toLocaleString()} />
          <Stat label="High severity" value={(sev.high ?? 0).toLocaleString()} tone={sev.high ? 'danger' : 'default'} />
          <Stat label="Medium" value={(sev.medium ?? 0).toLocaleString()} tone={sev.medium ? 'warning' : 'default'} />
          <Stat label="Low" value={(sev.low ?? 0).toLocaleString()} />
        </dl>
      </Card>

      {error && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger">{error}</p>
        </Card>
      )}

      {!error && (
        <RecommendationsList initial={(data?.recommendations as never[]) ?? []} />
      )}
    </AppShell>
  )
}
