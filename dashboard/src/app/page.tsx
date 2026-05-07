import { fetchStats, fetchJobs, fetchDigest, fetchCompanies } from '@/lib/api'
import JobsTable from '@/components/JobsTable'
import StatsCards from '@/components/StatsCards'
import DigestPanel from '@/components/DigestPanel'
import PipelineActions from '@/components/PipelineActions'
import ScoreChart from '@/components/ScoreChart'

export const revalidate = 60

export default async function Dashboard() {
  // Fetch all data in parallel
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
    <div className="min-h-screen bg-gray-950">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-white">
              🤖 Job Hunt AI
            </h1>
            <p className="text-xs text-gray-400 mt-0.5">
              Rizwan Zafar · Dubai, UAE · {new Date().toLocaleDateString('en-GB', { dateStyle: 'medium' })}
            </p>
          </div>
          <PipelineActions />
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* Stats row */}
        <StatsCards stats={statsValue} companiesCount={companies.length} />

        {/* Charts + Digest row */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <ScoreChart stats={statsValue} />
          </div>
          <div>
            <DigestPanel digest={digestValue} />
          </div>
        </div>

        {/* Jobs table */}
        <JobsTable jobs={jobs} />
      </main>
    </div>
  )
}
