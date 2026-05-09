import { fetchTargetCompanies } from '@/lib/profile-api'
import TargetCompaniesManager from '@/components/TargetCompaniesManager'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'

export const dynamic = 'force-dynamic'

export default async function CompaniesPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchTargetCompanies()
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load target companies'
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="Targets"
        title="Target companies"
        description="The pipeline scans only these. Toggle is_target to include or exclude. Each scan checks the careers page for new openings, then runs the full agent flow on qualifying roles."
      />

      {error && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger">{error}</p>
        </Card>
      )}

      {!error && data && <TargetCompaniesManager initial={data} />}
    </AppShell>
  )
}
