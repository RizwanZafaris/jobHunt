import { fetchRecommendations } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import RecommendationsList from '@/components/RecommendationsList'

export const dynamic = 'force-dynamic'

export default async function RecommendationsPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchRecommendations()
  } catch (e: any) {
    error = e?.message || 'Failed to load recommendations'
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <Header />
      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
          <h1 className="text-2xl font-bold text-white">Profile Recommendations</h1>
          <p className="text-sm text-gray-400 mt-1">
            AI-generated improvements: missing keywords, weak categories,
            unquantified bullets, and cross-document conflicts.
          </p>
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            <Stat label="Total open" value={data?.total ?? 0} />
            <Stat label="High severity" value={(data?.by_severity as any)?.high ?? 0} />
            <Stat label="Medium" value={(data?.by_severity as any)?.medium ?? 0} />
            <Stat label="Low" value={(data?.by_severity as any)?.low ?? 0} />
          </div>
        </section>

        {error && (
          <div className="bg-red-900/30 border border-red-800 rounded-xl p-4 text-sm text-red-300">
            {error}
          </div>
        )}

        {!error && (
          <RecommendationsList initial={data?.recommendations ?? []} />
        )}
      </main>
    </div>
  )
}

function Header() {
  return (
    <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">🤖 Job Hunt AI</h1>
        <ProfileNav />
      </div>
    </header>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-gray-800/60 border border-gray-700 rounded-lg px-3 py-1.5">
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-base font-bold text-white">{value.toLocaleString()}</div>
    </div>
  )
}
