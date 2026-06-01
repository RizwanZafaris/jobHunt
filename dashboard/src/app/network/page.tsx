/**
 * /network — referral graph surface (B9: real fetchers).
 *
 * Top: search + LinkedIn CSV import
 * Middle: best warm intros (top 5 ranked paths) + target coverage
 * Bottom: your network grouped by current company
 *
 * Server Component owns the data hand-off; NetworkClient runs the
 * interactive bits (modal, filtering, file input).
 */
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { NetworkClient } from '@/components/network/NetworkClient'
import { fetchNetworkPeople, fetchTargetCoverage } from '@/lib/api'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Network · Job Hunt',
  description:
    'Referral graph that finds the warmest path into your dream company.',
}

export default async function NetworkPage() {
  let topPaths: any[] = []
  let coverage: any[] = []
  let people: any[] = []
  let error: string | null = null

  try {
    const [coverageRes, peopleRes] = await Promise.all([
      fetchTargetCoverage(),
      fetchNetworkPeople(),
    ])
    coverage = Array.isArray(coverageRes) ? coverageRes : []
    people = Array.isArray(peopleRes) ? peopleRes : []
    topPaths = coverage
      .filter((c: any) => c.top_path)
      .sort(
        (a: any, b: any) =>
          (b.paths_count_1hop + b.paths_count_2hop) -
          (a.paths_count_1hop + a.paths_count_2hop),
      )
      .slice(0, 5)
      .map((c: any) => c.top_path)
  } catch (err) {
    error = err instanceof Error ? err.message : String(err)
  }

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Sprint 2 · MVP"
        title="Network"
        description="Find the warmest path into your dream company. Top 1- and 2-hop intros across your target list."
      />

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 p-4 text-red-700">
          Couldn&apos;t load network data: {error}
        </div>
      )}

      {people.length === 0 && !error && (
        <div className="mb-4 rounded-lg border border-accent/30 bg-accent/10 p-4 text-sm text-fg">
          No people yet — use <span className="font-medium">“Find people”</span> to add contacts at your target companies.
        </div>
      )}

      <NetworkClient topPaths={topPaths} coverage={coverage} people={people} />
    </AppShell>
  )
}
