import { fetchKeywords } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import KeywordExplorer from '@/components/KeywordExplorer'

export const revalidate = 300

const CATEGORY_COLORS: Record<string, string> = {
  Payments: 'bg-emerald-900/40 text-emerald-300 border-emerald-800',
  Compliance: 'bg-amber-900/40 text-amber-300 border-amber-800',
  Leadership: 'bg-purple-900/40 text-purple-300 border-purple-800',
  Agile: 'bg-cyan-900/40 text-cyan-300 border-cyan-800',
  'Product Management': 'bg-blue-900/40 text-blue-300 border-blue-800',
  Fintech: 'bg-rose-900/40 text-rose-300 border-rose-800',
  PMO: 'bg-indigo-900/40 text-indigo-300 border-indigo-800',
  Technical: 'bg-sky-900/40 text-sky-300 border-sky-800',
  Analytics: 'bg-fuchsia-900/40 text-fuchsia-300 border-fuchsia-800',
  AI: 'bg-violet-900/40 text-violet-300 border-violet-800',
  'Risk & Security': 'bg-red-900/40 text-red-300 border-red-800',
}

export default async function KeywordsPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchKeywords()
  } catch (e: any) {
    error = e?.message || 'Failed to load keywords'
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-200">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-12">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
            <h2 className="text-lg font-semibold text-white mb-2">Keywords not loaded</h2>
            <p className="text-sm text-gray-400">{error}</p>
          </div>
        </main>
      </div>
    )
  }

  const totalOccurrences = data.categories.reduce((sum, c) => sum + c.total_occurrences, 0)

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <Header />
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
          <h1 className="text-2xl font-bold text-white">Keyword Intelligence</h1>
          <p className="text-sm text-gray-400 mt-1">
            ATS-relevance map across {data.keywords.length} keywords from your resume corpus.
          </p>
          <div className="mt-4 flex flex-wrap gap-2 text-xs">
            <Stat label="Categories" value={data.categories.length} />
            <Stat label="Keywords found" value={data.keywords.length} />
            <Stat label="Total occurrences" value={totalOccurrences} />
          </div>
        </section>

        {/* Category overview cards */}
        <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {data.categories.map((c) => {
            const color = CATEGORY_COLORS[c.category] || 'bg-gray-800 text-gray-300 border-gray-700'
            return (
              <div key={c.category} className={`rounded-xl border p-4 ${color}`}>
                <div className="text-sm font-semibold">{c.category}</div>
                <div className="text-2xl font-bold mt-1">{c.keyword_count}</div>
                <div className="text-xs opacity-80 mt-0.5">{c.total_occurrences.toLocaleString()} occurrences</div>
                <div className="text-xs opacity-70 mt-2">
                  Avg strength {c.avg_strength}
                </div>
              </div>
            )
          })}
        </section>

        {/* Top keywords by category */}
        <section className="space-y-4">
          {data.categories.map((c) => {
            const colorClass = CATEGORY_COLORS[c.category] || 'bg-gray-800 text-gray-300 border-gray-700'
            return (
              <div key={c.category} className="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <div className="flex items-center justify-between mb-3">
                  <h3 className={`text-sm font-semibold uppercase tracking-wider ${colorClass.includes('text-') ? colorClass.split(' ').find((s) => s.startsWith('text-')) : 'text-white'}`}>
                    {c.category}
                  </h3>
                  <span className="text-xs text-gray-500">
                    {c.keyword_count} keywords · {c.total_occurrences.toLocaleString()} occurrences
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {c.top_keywords.map((kw) => (
                    <span key={kw} className={`text-xs border px-2 py-0.5 rounded ${colorClass}`}>
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            )
          })}
        </section>

        {/* Searchable explorer */}
        <KeywordExplorer keywords={data.keywords} categoryColors={CATEGORY_COLORS} />
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
