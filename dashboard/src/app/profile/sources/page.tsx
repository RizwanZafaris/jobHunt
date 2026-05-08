import { fetchSources } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import SourcesTable from '@/components/SourcesTable'

export const revalidate = 300

export default async function SourcesPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchSources()
  } catch (e: any) {
    error = e?.message || 'Failed to load sources'
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-200">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-12">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
            <h2 className="text-lg font-semibold text-white mb-2">Sources not loaded</h2>
            <p className="text-sm text-gray-400">{error}</p>
          </div>
        </main>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <Header />
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
          <h1 className="text-2xl font-bold text-white">Source Documents</h1>
          <p className="text-sm text-gray-400 mt-1">
            All resume + LinkedIn + interview-prep files parsed into the profile.
          </p>
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            <Stat label="Total documents" value={data.total} />
            <Stat label="Document classes" value={Object.keys(data.by_class).length} />
          </div>
        </section>

        {/* Class breakdown */}
        <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {Object.entries(data.by_class)
            .sort((a, b) => b[1] - a[1])
            .map(([cls, n]) => (
              <div key={cls} className="bg-gray-900 border border-gray-800 rounded-xl p-3">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider">{cls.replace(/_/g, ' ')}</div>
                <div className="text-2xl font-bold text-white mt-1">{n}</div>
              </div>
            ))}
        </section>

        <SourcesTable documents={data.documents} />
      </main>
    </div>
  )
}

function Header() {
  return (
    <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
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
