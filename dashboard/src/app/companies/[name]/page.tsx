import Link from 'next/link'
import { fetchCompanyKnowledge } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import CompanyResearchButton from '@/components/CompanyResearchButton'

export const dynamic = 'force-dynamic'

const SECTION_META: Record<string, { label: string; emoji: string; color: string }> = {
  overview: { label: 'Overview', emoji: '🏢', color: 'border-blue-700 bg-blue-900/10' },
  news: { label: 'News', emoji: '📰', color: 'border-cyan-700 bg-cyan-900/10' },
  funding: { label: 'Funding', emoji: '💰', color: 'border-emerald-700 bg-emerald-900/10' },
  culture: { label: 'Culture', emoji: '🤝', color: 'border-purple-700 bg-purple-900/10' },
  tech_stack: { label: 'Tech Stack', emoji: '⚙️', color: 'border-sky-700 bg-sky-900/10' },
  strategy: { label: 'Strategy', emoji: '🎯', color: 'border-indigo-700 bg-indigo-900/10' },
  challenges: { label: 'Challenges', emoji: '⚠️', color: 'border-amber-700 bg-amber-900/10' },
  competitors: { label: 'Competitors', emoji: '🥊', color: 'border-rose-700 bg-rose-900/10' },
  // Recruitment intelligence
  recruitment_process: { label: 'Recruitment Process', emoji: '📋', color: 'border-emerald-600 bg-emerald-900/20' },
  resume_dos_donts: { label: 'Resume Do\'s & Don\'ts', emoji: '✏️', color: 'border-blue-600 bg-blue-900/20' },
  ats_signals: { label: 'ATS Signals', emoji: '🔑', color: 'border-violet-600 bg-violet-900/20' },
  interview_format: { label: 'Interview Format', emoji: '🎤', color: 'border-amber-600 bg-amber-900/20' },
  hiring_signals: { label: 'What They Value', emoji: '⭐', color: 'border-fuchsia-600 bg-fuchsia-900/20' },
}

const RECRUITMENT_SECTIONS = [
  'recruitment_process',
  'resume_dos_donts',
  'ats_signals',
  'interview_format',
  'hiring_signals',
]

export default async function CompanyDetailPage({ params }: { params: { name: string } }) {
  const name = decodeURIComponent(params.name)
  let data
  let error: string | null = null
  try {
    data = await fetchCompanyKnowledge(name)
  } catch (e: any) {
    error = e?.message || 'Failed to load'
  }

  const company = data?.company
  const knowledge = data?.knowledge || []
  const knowledgeBySection: Record<string, any> = {}
  for (const k of knowledge) knowledgeBySection[k.section] = k

  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
          <h1 className="text-xl font-bold text-white">🤖 Job Hunt AI</h1>
          <ProfileNav />
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        <div className="flex items-center gap-3 text-xs text-gray-500">
          <Link href="/companies" className="hover:text-white">← All target companies</Link>
        </div>

        <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6 flex items-start justify-between gap-4 flex-wrap">
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-white">{name}</h1>
            {company && (
              <div className="flex flex-wrap gap-2 mt-2 text-xs">
                {company.category && (
                  <span className="bg-gray-800 border border-gray-700 text-gray-300 px-2 py-0.5 rounded">
                    {company.category}
                  </span>
                )}
                {company.priority && (
                  <span className={`border px-2 py-0.5 rounded ${
                    company.priority === 'high'
                      ? 'bg-emerald-900/40 border-emerald-800 text-emerald-300'
                      : company.priority === 'medium'
                      ? 'bg-blue-900/40 border-blue-800 text-blue-300'
                      : 'bg-gray-800 border-gray-700 text-gray-400'
                  }`}>
                    {company.priority} priority
                  </span>
                )}
                {company.careers_url && (
                  <a
                    href={company.careers_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:underline"
                  >
                    careers ↗
                  </a>
                )}
                {company.last_scanned_at && (
                  <span className="text-gray-500">
                    last scanned {new Date(company.last_scanned_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
                  </span>
                )}
              </div>
            )}
          </div>
          <CompanyResearchButton companyName={name} />
        </section>

        {error && (
          <div className="bg-red-900/30 border border-red-800 rounded-xl p-4 text-sm text-red-300">
            {error}
          </div>
        )}

        {!error && knowledge.length === 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
            <h2 className="text-lg font-semibold text-white mb-2">No research yet</h2>
            <p className="text-sm text-gray-400 mb-4">
              Click "Research" above to have CompanyAgent build a deep intel pack:
              business overview, recent news, culture, recruitment process, ATS signals,
              resume do's and don'ts, interview format, and what they actually value in candidates.
            </p>
            <p className="text-xs text-gray-500">Takes 30-90s per company. Runs in background.</p>
          </div>
        )}

        {/* Recruitment intelligence — surface at top */}
        {knowledge.length > 0 && (
          <>
            <section>
              <h2 className="text-base font-bold text-white mb-3 flex items-center gap-2">
                🎯 Recruitment Intelligence
                <span className="text-xs text-gray-500 font-normal">— how to apply here</span>
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {RECRUITMENT_SECTIONS.map((s) => {
                  const k = knowledgeBySection[s]
                  if (!k) return null
                  const meta = SECTION_META[s]
                  return <KnowledgeCard key={s} section={s} content={k.content} sourceUrl={k.source_url} meta={meta} />
                })}
              </div>
            </section>

            <section>
              <h2 className="text-base font-bold text-white mb-3 flex items-center gap-2">
                🏢 Company Intelligence
                <span className="text-xs text-gray-500 font-normal">— context for tailoring</span>
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {Object.keys(SECTION_META)
                  .filter((s) => !RECRUITMENT_SECTIONS.includes(s))
                  .map((s) => {
                    const k = knowledgeBySection[s]
                    if (!k) return null
                    const meta = SECTION_META[s]
                    return <KnowledgeCard key={s} section={s} content={k.content} sourceUrl={k.source_url} meta={meta} />
                  })}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  )
}

function KnowledgeCard({
  section,
  content,
  sourceUrl,
  meta,
}: {
  section: string
  content: string
  sourceUrl: string | null
  meta?: { label: string; emoji: string; color: string }
}) {
  const m = meta || { label: section, emoji: '📄', color: 'border-gray-700 bg-gray-900' }
  return (
    <article className={`border-t-2 ${m.color} bg-gray-900 rounded-xl p-4`}>
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <span>{m.emoji}</span> {m.label}
        </h3>
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-blue-400 hover:underline"
          >
            source ↗
          </a>
        )}
      </div>
      <p className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap">{content}</p>
    </article>
  )
}
