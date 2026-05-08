import {
  fetchPersonas,
  fetchConversionFunnel,
  type PersonasResponse,
  type ConversionFunnelRow,
} from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import PersonasTable from '@/components/PersonasTable'
import ConversionFunnel from '@/components/ConversionFunnel'
import RegeneratePersonaButton from '@/components/RegeneratePersonaButton'

export const dynamic = 'force-dynamic'

export default async function PersonasPage() {
  let personas: PersonasResponse | null = null
  let funnel: ConversionFunnelRow[] = []
  let funnelWarning: string | undefined
  let error: string | null = null

  try {
    const [p, f] = await Promise.all([
      fetchPersonas(),
      fetchConversionFunnel().catch((e) => {
        funnelWarning = e?.message || 'funnel unavailable'
        return { funnel: [] as ConversionFunnelRow[] }
      }),
    ])
    personas = p
    funnel = (f as any).funnel || []
    if ((f as any).warning) funnelWarning = (f as any).warning
  } catch (e: any) {
    error = e?.message || 'Failed to load personas'
  }

  return (
    <Shell>
      {/* Header */}
      <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold text-white">🧠 Company Personas</h1>
            <p className="text-sm text-gray-400 mt-1">
              Per-company system prompts the G2 Insider Expert loads.{' '}
              <span className="text-gray-500">
                Synthesizer reads outcomes + transcripts and refreshes these
                weekly (Sundays 03:00 GST).
              </span>
            </p>
          </div>
          {personas && (
            <div className="flex items-center gap-2 flex-wrap">
              <QualityBadge label="High" count={personas.by_quality?.high || 0} color="emerald" />
              <QualityBadge label="Medium" count={personas.by_quality?.medium || 0} color="amber" />
              <QualityBadge label="Low" count={personas.by_quality?.low || 0} color="red" />
              {/* Phase 1.13: bulk-regenerate the low-quality tier in one
                  click instead of clicking Regenerate on each row. Order
                  is Low → Medium → All so the most-actionable button is
                  closest to the badges. Tiers with count==0 are hidden. */}
              {(personas.by_quality?.low || 0) > 0 && (
                <RegeneratePersonaButton
                  companyName=""
                  qualityFilter="low"
                  size="md"
                  label={`Regenerate Low (${personas.by_quality.low})`}
                />
              )}
              {(personas.by_quality?.medium || 0) > 0 && (
                <RegeneratePersonaButton
                  companyName=""
                  qualityFilter="medium"
                  size="md"
                  label={`Regenerate Medium (${personas.by_quality.medium})`}
                />
              )}
              <RegenerateAllButton />
            </div>
          )}
        </div>
      </section>

      {/* Conversion funnel */}
      <ConversionFunnel funnel={funnel} warning={funnelWarning} />

      {/* Personas list */}
      {error && (
        <div className="bg-red-900/30 border border-red-900/60 rounded-xl p-4 text-sm text-red-300">
          {error}
        </div>
      )}
      {personas && personas.personas.length === 0 && (
        <div className="bg-gray-900 border border-dashed border-gray-800 rounded-xl p-8 text-center">
          <p className="text-sm text-gray-400">No personas seeded yet.</p>
          <p className="text-xs text-gray-500 mt-1">
            Run <code className="text-gray-300">db/seed_company_personas.sql</code>{' '}
            against your Supabase project to bootstrap from existing
            company_knowledge.
          </p>
        </div>
      )}
      {personas && personas.personas.length > 0 && (
        <PersonasTable personas={personas.personas} />
      )}

      {/* Footer help */}
      <section className="bg-gray-900/50 border border-gray-800 rounded-xl p-4 text-[11px] text-gray-500 leading-relaxed">
        <p className="font-semibold text-gray-400 mb-1">How personas mature</p>
        <ol className="list-decimal list-inside space-y-0.5">
          <li>
            <span className="text-gray-300">v1 (seed)</span> — built from{' '}
            <code className="text-gray-400">company_knowledge</code> 13-section research, quality-graded by how much was "Unknown — insufficient data".
          </li>
          <li>
            <span className="text-gray-300">v2+ (synthesized)</span> — Sunday cron pulls last 90d of outcomes + transcripts, calls Gemini 2.5 Pro long-context to extract success_patterns + failure_patterns, increments persona_version.
          </li>
          <li>
            <span className="text-gray-300">G2 next build</span> — Insider Expert loads the persona's system_prompt_template at the start of every resume build for that company.
          </li>
        </ol>
      </section>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
          <h1 className="text-xl font-bold text-white">🤖 Job Hunt AI</h1>
          <ProfileNav />
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">{children}</main>
    </div>
  )
}

function QualityBadge({
  label,
  count,
  color,
}: {
  label: string
  count: number
  color: 'emerald' | 'amber' | 'red'
}) {
  const cls =
    color === 'emerald'
      ? 'bg-emerald-900/40 border-emerald-800 text-emerald-300'
      : color === 'amber'
      ? 'bg-amber-900/40 border-amber-800 text-amber-300'
      : 'bg-red-900/30 border-red-900/60 text-red-400'
  return (
    <span className={`text-[11px] border px-2 py-0.5 rounded ${cls}`}>
      <span className="font-bold">{count}</span> {label}
    </span>
  )
}

// Client-component button — keep outside Shell (which is a server tree)
function RegenerateAllButton() {
  // We rely on the typed helper from profile-api but POST without a
  // company_name so it iterates everything. Inline-defined client component
  // would require 'use client' at top of file; this is a Server Component
  // page, so we use the existing component with a sentinel value.
  return <RegeneratePersonaButton companyName="" size="md" />
}
