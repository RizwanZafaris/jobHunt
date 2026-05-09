import { fetchStats, fetchJobs, fetchDigest, fetchCompanies } from '@/lib/api'
import JobsTable from '@/components/JobsTable'
import StatsCards from '@/components/StatsCards'
import DigestPanel from '@/components/DigestPanel'
import PipelineActions from '@/components/PipelineActions'
import ScoreChart from '@/components/ScoreChart'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'

export const revalidate = 60

export default async function Dashboard() {
  // Fetch all data in parallel; allSettled so one failure doesn't blank the page.
  const [stats, jobsData, digest, companiesData] = await Promise.allSettled([
    fetchStats(),
    fetchJobs({ limit: 50, min_score: 0 }),
    fetchDigest(),
    fetchCompanies(),
  ])

  const statsValue = stats.status === 'fulfilled' ? stats.value : null
  const jobs = jobsData.status === 'fulfilled' ? jobsData.value.jobs : []
  const digestValue = digest.status === 'fulfilled' ? digest.value : null
  const companies = companiesData.status === 'fulfilled' ? companiesData.value.companies : []

  return (
    <AppShell wide actions={<PipelineActions />}>
      <PageHeader
        eyebrow="Pipeline"
        title="Today's job hunt"
        description="Live view of every job the agent network has discovered, scored, and prioritized."
      />

      <StatsCards stats={statsValue} companiesCount={companies.length} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <ScoreChart stats={statsValue} />
        </div>
        <div>
          <DigestPanel digest={digestValue} />
        </div>
      </div>

      <JobsTable jobs={jobs} />
    </AppShell>
  )
}
