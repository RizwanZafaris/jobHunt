import { fetchTargetCompanies } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import TargetCompaniesManager from '@/components/TargetCompaniesManager'

export const dynamic = 'force-dynamic'

export default async function CompaniesPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchTargetCompanies()
  } catch (e: any) {
    error = e?.message || 'Failed to load target companies'
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
          <h1 className="text-xl font-bold text-white">🤖 Job Hunt AI</h1>
          <ProfileNav />
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
          <h1 className="text-2xl font-bold text-white">Target Companies</h1>
          <p className="text-sm text-gray-400 mt-1">
            Pipeline scans only these. Toggle is_target to include or exclude. Each scan
            checks the careers page for new openings, then runs the full agent flow on
            qualifying roles.
          </p>
        </section>

        {error && (
          <div className="bg-red-900/30 border border-red-800 rounded-xl p-4 text-sm text-red-300">
            {error}
          </div>
        )}

        {!error && data && (
          <TargetCompaniesManager initial={data} />
        )}
      </main>
    </div>
  )
}
