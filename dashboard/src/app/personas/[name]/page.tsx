import Link from 'next/link'
import { fetchPersonaDetail } from '@/lib/profile-api'
import RegeneratePersonaButton from '@/components/RegeneratePersonaButton'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Pill, type PillTone } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'

export const dynamic = 'force-dynamic'

interface PersonaDetail {
  id: string
  company_id: string | null
  company_name: string
  system_prompt_template: string
  ats_keyword_bank: {
    required?: string[]
    boost?: string[]
    banned?: string[]
    raw_signals?: string
  }
  success_patterns: Array<{ pattern?: string; example?: string; evidence?: string } | string>
  failure_patterns: Array<{ pattern?: string; example?: string; evidence?: string } | string>
  last_synthesized_at: string
  n_examples_used: number
  persona_version: number
  metadata: {
    persona_quality?: 'high' | 'medium' | 'low'
    unknown_sections?: number
    seeded_from?: string
    seeded_at?: string
    last_synthesizer_run?: string
    last_synthesizer_model?: string
    synthesis_summary?: string
    n_outcomes_used?: number
    n_transcripts_used?: number
  }
}

const QUALITY_TONE: Record<string, PillTone> = {
  high: 'success',
  medium: 'warning',
  low: 'danger',
  unknown: 'neutral',
}

export default async function PersonaDetailPage({
  params,
}: {
  params: { name: string }
}) {
  const decoded = decodeURIComponent(params.name)
  let persona: PersonaDetail | null = null
  let error: string | null = null

  try {
    persona = (await fetchPersonaDetail(decoded)) as PersonaDetail
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load persona'
  }

  if (error || !persona) {
    return (
      <AppShell>
        <EmptyState
          icon="alert-triangle"
          title="Persona not found"
          description={error || `No persona row exists for "${decoded}".`}
          action={
            <Link
              href="/personas"
              className="text-2xs text-info hover:underline inline-flex items-center gap-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
            >
              <Icon name="chevron-right" size={12} className="rotate-180" />
              Back to personas
            </Link>
          }
        />
      </AppShell>
    )
  }

  const quality = persona.metadata?.persona_quality || 'unknown'
  const qualityTone = QUALITY_TONE[quality]
  const required = persona.ats_keyword_bank?.required || []
  const boost = persona.ats_keyword_bank?.boost || []
  const banned = persona.ats_keyword_bank?.banned || []
  const successPatterns = persona.success_patterns || []
  const failurePatterns = persona.failure_patterns || []

  return (
    <AppShell>
      <nav aria-label="Breadcrumb" className="text-2xs text-fg-subtle">
        <Link
          href="/personas"
          className="hover:text-fg inline-flex items-center gap-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
        >
          <Icon name="chevron-right" size={12} className="rotate-180" />
          Personas
        </Link>
        <span className="text-fg-subtle/50 mx-1.5">/</span>
        <span className="text-fg">{persona.company_name}</span>
      </nav>

      <PageHeader
        eyebrow="Persona"
        title={persona.company_name}
        description={
          <>
            v{persona.persona_version} · last synthesized{' '}
            {new Date(persona.last_synthesized_at).toLocaleString('en-GB')}
          </>
        }
        actions={<RegeneratePersonaButton companyName={persona.company_name} size="md" showForce />}
      />

      <div className="flex flex-wrap gap-1.5 -mt-3">
        <Pill tone={qualityTone}>{quality} quality</Pill>
        {persona.metadata?.unknown_sections !== undefined && persona.metadata.unknown_sections > 0 && (
          <Pill title="Recruitment-intel sections that were 'Unknown — insufficient data' at synthesis time">
            {persona.metadata.unknown_sections}/5 sections sparse
          </Pill>
        )}
        <Pill>
          {persona.n_examples_used} outcome{persona.n_examples_used === 1 ? '' : 's'} used
        </Pill>
        {persona.metadata?.last_synthesizer_model && (
          <Pill tone="info" title="Model used by the last synthesizer run">
            {persona.metadata.last_synthesizer_model}
          </Pill>
        )}
      </div>

      {persona.metadata?.synthesis_summary && (
        <p className="text-sm text-fg-muted italic max-w-2xl leading-relaxed">
          &ldquo;{persona.metadata.synthesis_summary}&rdquo;
        </p>
      )}

      <Card title="ATS keyword bank">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          <KeywordList label="Required" sublabel="must appear in resume" items={required} tone="success" />
          <KeywordList label="Boost" sublabel="helpful but not required" items={boost} tone="info" />
          <KeywordList label="Banned" sublabel="avoid these phrases here" items={banned} tone="danger" />
        </div>
        {persona.ats_keyword_bank?.raw_signals && (
          <details className="mt-4 group">
            <summary className="cursor-pointer text-2xs text-fg-subtle hover:text-fg select-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded inline-flex items-center gap-1.5">
              <Icon name="chevron-right" size={12} className="transition-transform group-open:rotate-90" />
              Raw signals (unstructured prose from{' '}
              <code className="font-mono text-fg">company_knowledge.ats_signals</code>)
            </summary>
            <pre className="mt-2 text-2xs text-fg-muted leading-relaxed whitespace-pre-wrap font-mono bg-surface-raised border border-border rounded-md p-3 max-h-48 overflow-y-auto">
              {persona.ats_keyword_bank.raw_signals}
            </pre>
          </details>
        )}
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <PatternCard
          title="Success patterns"
          subtitle="Bullet structures from past resumes that got interviews"
          patterns={successPatterns}
          tone="success"
        />
        <PatternCard
          title="Failure patterns"
          subtitle="Bullet structures from past resumes that didn't"
          patterns={failurePatterns}
          tone="danger"
        />
      </div>

      <Card>
        <details open className="group">
          <summary className="cursor-pointer flex items-center justify-between mb-3 select-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded">
            <h2 className="text-sm font-semibold text-fg flex items-center gap-2">
              <Icon name="chevron-right" size={12} className="transition-transform group-open:rotate-90" />
              System prompt template
            </h2>
            <span className="text-2xs text-fg-subtle tnum">
              {persona.system_prompt_template?.length || 0} chars · used by G2 Insider Expert
            </span>
          </summary>
          <pre className="text-xs text-fg-muted leading-relaxed whitespace-pre-wrap font-mono bg-surface-raised border border-border rounded-md p-4 max-h-[600px] overflow-y-auto">
            {persona.system_prompt_template}
          </pre>
        </details>
      </Card>

      <Card padding="md">
        <dl className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Meta label="Seeded from" value={persona.metadata?.seeded_from || '—'} />
          <Meta label="Outcomes" value={String(persona.metadata?.n_outcomes_used ?? 0)} />
          <Meta label="Transcripts" value={String(persona.metadata?.n_transcripts_used ?? 0)} />
          <Meta
            label="Last synth run"
            value={
              persona.metadata?.last_synthesizer_run
                ? new Date(persona.metadata.last_synthesizer_run).toLocaleDateString('en-GB')
                : '—'
            }
          />
        </dl>
      </Card>
    </AppShell>
  )
}

function KeywordList({
  label,
  sublabel,
  items,
  tone,
}: {
  label: string
  sublabel: string
  items: string[]
  tone: PillTone
}) {
  return (
    <div>
      <h3 className="text-2xs uppercase tracking-wider text-fg-muted font-semibold">{label}</h3>
      <p className="text-2xs text-fg-subtle mt-0.5 mb-2">{sublabel}</p>
      {items.length === 0 ? (
        <p className="text-2xs text-fg-subtle italic">(empty — synthesizer hasn&apos;t extracted these yet)</p>
      ) : (
        <div className="flex flex-wrap gap-1">
          {items.map((kw, i) => (
            <Pill key={`${kw}-${i}`} tone={tone}>{kw}</Pill>
          ))}
        </div>
      )}
    </div>
  )
}

function PatternCard({
  title,
  subtitle,
  patterns,
  tone,
}: {
  title: string
  subtitle: string
  patterns: Array<{ pattern?: string; example?: string; evidence?: string } | string>
  tone: 'success' | 'danger'
}) {
  return (
    <Card title={title} description={subtitle}>
      {patterns.length === 0 ? (
        <p className="text-xs text-fg-subtle italic leading-relaxed">
          No patterns extracted yet. The synthesizer needs ≥3 outcomes for this company before it can confidently identify recurring patterns.
        </p>
      ) : (
        <ul className="space-y-3">
          {patterns.map((p, i) => {
            if (typeof p === 'string') {
              return (
                <li key={i} className="text-xs text-fg-muted leading-relaxed flex gap-2">
                  <span aria-hidden className={tone === 'success' ? 'text-success' : 'text-danger'}>•</span>
                  <span>{p}</span>
                </li>
              )
            }
            return (
              <li
                key={i}
                className="bg-surface-raised border border-border rounded-md p-3 text-xs space-y-1"
              >
                {p.pattern && <p className="text-fg font-medium">{p.pattern}</p>}
                {p.example && <p className="text-fg-muted italic">e.g. &ldquo;{p.example}&rdquo;</p>}
                {p.evidence && <p className="text-2xs text-fg-subtle">{p.evidence}</p>}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-2xs uppercase tracking-wider text-fg-subtle">{label}</dt>
      <dd className="text-xs text-fg mt-0.5 truncate" title={value}>{value}</dd>
    </div>
  )
}
