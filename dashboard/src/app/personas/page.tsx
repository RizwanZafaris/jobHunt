import {
  fetchPersonas,
  fetchConversionFunnel,
  type PersonasResponse,
  type ConversionFunnelRow,
} from '@/lib/profile-api'
import PersonasTable from '@/components/PersonasTable'
import ConversionFunnel from '@/components/ConversionFunnel'
import RegeneratePersonaButton from '@/components/RegeneratePersonaButton'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Pill } from '@/components/ui/Pill'

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
    const fAny = f as { funnel?: ConversionFunnelRow[]; warning?: string }
    funnel = fAny.funnel || []
    if (fAny.warning) funnelWarning = fAny.warning
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load personas'
  }

  const high = personas?.by_quality?.high ?? 0
  const medium = personas?.by_quality?.medium ?? 0
  const low = personas?.by_quality?.low ?? 0

  return (
    <AppShell>
      <PageHeader
        eyebrow="Knowledge"
        title="Company personas"
        description="Per-company system prompts the G2 Insider Expert loads. The synthesizer reads outcomes and transcripts and refreshes these weekly (Sundays 03:00 GST)."
        actions={
          personas ? (
            <div className="flex items-center gap-1.5 flex-wrap">
              <Pill tone="success"><span className="font-semibold tnum">{high}</span> high</Pill>
              <Pill tone="warning"><span className="font-semibold tnum">{medium}</span> medium</Pill>
              <Pill tone="danger"><span className="font-semibold tnum">{low}</span> low</Pill>
              {low > 0 && (
                <RegeneratePersonaButton
                  companyName=""
                  qualityFilter="low"
                  size="md"
                  label={`Regenerate low (${low})`}
                />
              )}
              {medium > 0 && (
                <RegeneratePersonaButton
                  companyName=""
                  qualityFilter="medium"
                  size="md"
                  label={`Regenerate medium (${medium})`}
                />
              )}
              <RegeneratePersonaButton companyName="" size="md" />
            </div>
          ) : null
        }
      />

      <ConversionFunnel funnel={funnel} warning={funnelWarning} />

      {error && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger">{error}</p>
        </Card>
      )}

      {personas && personas.personas.length === 0 && (
        <EmptyState
          icon="brain"
          title="No personas seeded yet"
          description={
            <>
              Run <code className="font-mono text-fg">db/seed_company_personas.sql</code> against your Supabase project to bootstrap from existing company_knowledge.
            </>
          }
        />
      )}

      {personas && personas.personas.length > 0 && (
        <PersonasTable personas={personas.personas} />
      )}

      <Card title="How personas mature" description="Three stages from seed to lived-in system prompt">
        <ol className="list-decimal list-outside ml-4 text-xs text-fg-muted space-y-1.5 leading-relaxed">
          <li>
            <span className="text-fg font-medium">v1 (seed)</span> — built from{' '}
            <code className="font-mono text-fg">company_knowledge</code> 13-section research, quality-graded by how much was &quot;Unknown — insufficient data&quot;.
          </li>
          <li>
            <span className="text-fg font-medium">v2+ (synthesized)</span> — Sunday cron pulls last 90 d of outcomes and transcripts, calls Gemini 2.5 Pro long-context to extract success_patterns and failure_patterns, increments persona_version.
          </li>
          <li>
            <span className="text-fg font-medium">G2 next build</span> — Insider Expert loads the persona&apos;s system_prompt_template at the start of every resume build for that company.
          </li>
        </ol>
      </Card>
    </AppShell>
  )
}
