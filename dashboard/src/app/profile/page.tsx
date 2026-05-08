import { fetchProfile } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'

export const revalidate = 300

export default async function ProfilePage() {
  let data
  let error: string | null = null
  try {
    data = await fetchProfile()
  } catch (e: any) {
    error = e?.message || 'Failed to load profile'
  }

  if (error || !data?.master) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-200">
        <Header />
        <main className="max-w-5xl mx-auto px-4 py-12">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
            <h2 className="text-lg font-semibold text-white mb-2">Profile not loaded yet</h2>
            <p className="text-sm text-gray-400 mb-4">
              {error ? error : 'No profile_master row in Supabase. Run the seed script.'}
            </p>
            <code className="text-xs text-gray-500 bg-gray-950 px-2 py-1 rounded">
              python3 profile_build/05_seed_supabase.py
            </code>
          </div>
        </main>
      </div>
    )
  }

  const m = data.master
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <Header />
      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">
        {/* Identity card */}
        <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-8">
          <h1 className="text-3xl font-bold text-white tracking-tight">{m.name}</h1>
          <p className="text-base text-blue-400 mt-2">{m.headline}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-400 mt-4">
            {m.location && <span>📍 {m.location}</span>}
            {m.email && <span>✉ {m.email}</span>}
            {(m.phones || []).map((p) => (
              <span key={p}>📞 {p}</span>
            ))}
            {m.linkedin_url && (
              <a href={`https://${m.linkedin_url}`} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
                🔗 {m.linkedin_url}
              </a>
            )}
          </div>
          <p className="text-sm text-gray-300 leading-relaxed mt-6">{m.summary}</p>
          <div className="mt-6 flex gap-3 text-xs">
            <Stat label="Experience entries" value={data.experience.length} />
            <Stat label="Core competencies" value={(m.core_competencies || []).length} />
            <Stat label="Tech items" value={(m.technical_knowledge || []).length} />
            <Stat label="Tailored resumes" value={m.tailored_resumes_count} />
          </div>
        </section>

        {/* Core competencies */}
        {m.core_competencies?.length > 0 && (
          <Card title="Core Competencies">
            <div className="flex flex-wrap gap-2">
              {m.core_competencies.map((c) => (
                <span key={c} className="text-xs bg-gray-800 border border-gray-700 text-gray-300 px-2.5 py-1 rounded-full">
                  {c}
                </span>
              ))}
            </div>
          </Card>
        )}

        {/* AI Solutions */}
        {m.ai_solutions?.length > 0 && (
          <Card title="GenAI — Production Solutions">
            <div className="space-y-3">
              {m.ai_solutions.map((s) => (
                <div key={s.title} className="border-l-2 border-emerald-700 pl-4">
                  <h3 className="text-sm font-semibold text-emerald-400">{s.title}</h3>
                  <p className="text-xs text-gray-400 mt-1 leading-relaxed">{s.description}</p>
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* Experience */}
        {data.experience.length > 0 && (
          <Card title="Professional Experience">
            <div className="space-y-6">
              {data.experience.map((e) => (
                <div key={e.id} className="border-l-2 border-gray-700 pl-4">
                  <div className="flex items-baseline justify-between">
                    <h3 className="text-base font-semibold text-white">{e.title}</h3>
                    <span className="text-xs text-gray-500">{e.dates}</span>
                  </div>
                  <p className="text-xs text-blue-400 mt-1">
                    {e.company}
                    {e.location && <span className="text-gray-500"> · {e.location}</span>}
                    {e.scope && <span className="text-gray-500"> · {e.scope}</span>}
                  </p>
                  {e.summary && <p className="text-xs text-gray-400 mt-2 leading-relaxed">{e.summary}</p>}
                  {e.highlights?.length > 0 && (
                    <ul className="text-xs text-gray-300 mt-3 space-y-1.5">
                      {e.highlights.map((h, i) => (
                        <li key={i} className="flex gap-2">
                          <span className="text-gray-600">•</span>
                          <span className="leading-relaxed">{h}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {e.groups?.length > 0 && (
                    <div className="mt-3 space-y-3">
                      {e.groups.map((g, i) => (
                        <div key={i}>
                          <h4 className="text-xs font-semibold text-gray-200 mb-1">{g.label}</h4>
                          <ul className="text-xs text-gray-300 space-y-1.5 ml-2">
                            {g.bullets.map((b, j) => (
                              <li key={j} className="flex gap-2">
                                <span className="text-gray-600">•</span>
                                <span className="leading-relaxed">{b}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}

        {/* Education + Certifications + Tech + Languages — 2 cols */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {data.education.length > 0 && (
            <Card title="Education">
              <div className="space-y-3">
                {data.education.map((e) => (
                  <div key={e.id}>
                    <h4 className="text-sm font-medium text-white">{e.title}</h4>
                    {e.details && <p className="text-xs text-gray-400 mt-0.5">{e.details}</p>}
                    {e.year && <p className="text-xs text-gray-500 mt-0.5">{e.year}</p>}
                    {e.notes && <p className="text-xs text-gray-400 mt-1 italic">{e.notes}</p>}
                  </div>
                ))}
              </div>
            </Card>
          )}
          {data.certifications.length > 0 && (
            <Card title="Certifications">
              <div className="grid grid-cols-2 gap-2">
                {data.certifications.map((c) => (
                  <div key={c.id} className="bg-gray-800 border border-gray-700 rounded-lg p-2.5">
                    <div className="text-sm font-semibold text-white">{c.name}</div>
                    {c.full_name && <div className="text-xs text-gray-400 mt-0.5">{c.full_name}</div>}
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>

        {(m.technical_knowledge?.length > 0 || m.languages?.length > 0) && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {m.technical_knowledge?.length > 0 && (
              <Card title="Technical Knowledge">
                <div className="flex flex-wrap gap-1.5">
                  {m.technical_knowledge.map((t) => (
                    <span key={t} className="text-xs bg-blue-900/40 border border-blue-800 text-blue-300 px-2 py-0.5 rounded">
                      {t}
                    </span>
                  ))}
                </div>
              </Card>
            )}
            {m.languages?.length > 0 && (
              <Card title="Languages">
                <ul className="space-y-1.5">
                  {m.languages.map((l) => (
                    <li key={l.name} className="flex justify-between text-sm">
                      <span className="font-medium text-white">{l.name}</span>
                      <span className="text-xs text-gray-400">{l.level}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}
          </div>
        )}
      </main>
    </div>
  )
}

function Header() {
  return (
    <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">🤖 Job Hunt AI</h1>
        </div>
        <ProfileNav />
      </div>
    </header>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
      <h2 className="text-sm font-semibold text-white uppercase tracking-wider mb-4">{title}</h2>
      {children}
    </section>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-gray-800/60 border border-gray-700 rounded-lg px-3 py-1.5">
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-base font-bold text-white">{value}</div>
    </div>
  )
}
