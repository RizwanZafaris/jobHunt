import { fetchProfile } from '@/lib/profile-api'
import EditableProfileMaster from '@/components/EditableProfileMaster'
import EditableExperience from '@/components/EditableExperience'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Icon } from '@/components/ui/Icon'

export const dynamic = 'force-dynamic'

export default async function ProfilePage() {
  let data
  let error: string | null = null
  try {
    data = await fetchProfile()
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load profile'
  }

  if (error || !data?.master) {
    return (
      <AppShell>
        <PageHeader eyebrow="Profile" title="Profile" />
        <EmptyState
          icon="document"
          title="Profile not loaded yet"
          description={error ?? 'No profile_master row in Supabase. Run the seed script.'}
          hint={
            <code className="font-mono">python3 profile_build/05_seed_supabase.py</code>
          }
        />
      </AppShell>
    )
  }

  const m = data.master
  return (
    <AppShell>
      <PageHeader
        eyebrow="Profile"
        title="Master profile"
        description="Hover any field to edit — changes save to Supabase immediately."
      />

      <EditableProfileMaster initial={m} />

      <p className="text-2xs text-fg-subtle italic flex items-center gap-1.5">
        <Icon name="info" size={12} />
        Hover or focus any field to reveal the edit affordance.
      </p>

      {m.ai_solutions?.length > 0 && (
        <Card title="GenAI — production solutions">
          <div className="space-y-3">
            {m.ai_solutions.map((s) => (
              <div key={s.title} className="border-l-2 border-success pl-4">
                <h3 className="text-sm font-semibold text-fg">{s.title}</h3>
                <p className="text-xs text-fg-muted mt-1 leading-relaxed">{s.description}</p>
              </div>
            ))}
          </div>
        </Card>
      )}

      {data.experience.length > 0 && (
        <Card title="Professional experience">
          <div className="space-y-6">
            {data.experience.map((e) => (
              <EditableExperience key={e.id} initial={e} />
            ))}
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {data.education.length > 0 && (
          <Card title="Education">
            <div className="space-y-3">
              {data.education.map((e) => (
                <div key={e.id}>
                  <h4 className="text-sm font-medium text-fg">{e.title}</h4>
                  {e.details && <p className="text-xs text-fg-muted mt-0.5">{e.details}</p>}
                  {e.year && <p className="text-2xs text-fg-subtle mt-0.5">{e.year}</p>}
                  {e.notes && <p className="text-xs text-fg-muted mt-1 italic">{e.notes}</p>}
                </div>
              ))}
            </div>
          </Card>
        )}
        {data.certifications.length > 0 && (
          <Card title="Certifications">
            <div className="grid grid-cols-2 gap-2">
              {data.certifications.map((c) => (
                <div key={c.id} className="bg-surface-raised border border-border rounded-md p-2.5">
                  <div className="text-sm font-semibold text-fg">{c.name}</div>
                  {c.full_name && <div className="text-2xs text-fg-muted mt-0.5">{c.full_name}</div>}
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
                <span className="font-medium text-fg">{l.name}</span>
                <span className="text-xs text-fg-muted">{l.level}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </AppShell>
  )
}
