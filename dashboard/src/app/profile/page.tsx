import { fetchProfile } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import EditableProfileMaster from '@/components/EditableProfileMaster'
import EditableExperience from '@/components/EditableExperience'

export const dynamic = 'force-dynamic'

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
        {/* Identity + competencies + tech (editable) */}
        <EditableProfileMaster initial={m} />

        <div className="text-xs text-gray-500 italic">
          💡 Hover any field to see "edit" — click to inline-edit. Changes save to Supabase immediately.
        </div>

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

        {/* Experience (editable) */}
        {data.experience.length > 0 && (
          <Card title="Professional Experience">
            <div className="space-y-6">
              {data.experience.map((e) => (
                <EditableExperience key={e.id} initial={e} />
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
