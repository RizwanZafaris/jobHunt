import Link from 'next/link'
import { fetchPersonaDetail } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import RegeneratePersonaButton from '@/components/RegeneratePersonaButton'

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

const QUALITY_COLOR: Record<string, string> = {
  high: 'bg-emerald-900/40 border-emerald-800 text-emerald-300',
  medium: 'bg-amber-900/40 border-amber-800 text-amber-300',
  low: 'bg-red-900/30 border-red-900/60 text-red-400',
  unknown: 'bg-gray-800 border-gray-700 text-gray-400',
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
  } catch (e: any) {
    error = e?.message || 'Failed to load persona'
  }

  if (error || !persona) {
    return (
      <Shell>
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
          <h2 className="text-lg font-semibold text-white">Persona not found</h2>
          <p className="text-sm text-gray-400 mt-2">
            {error || `No persona row exists for "${decoded}".`}
          </p>
          <p className="text-xs text-gray-500 mt-3">
            <Link href="/personas" className="text-blue-400 hover:text-blue-300">
              ← Back to personas
            </Link>
          </p>
        </div>
      </Shell>
    )
  }

  const quality = persona.metadata?.persona_quality || 'unknown'
  const required = persona.ats_keyword_bank?.required || []
  const boost = persona.ats_keyword_bank?.boost || []
  const banned = persona.ats_keyword_bank?.banned || []
  const successPatterns = persona.success_patterns || []
  const failurePatterns = persona.failure_patterns || []

  return (
    <Shell>
      {/* Breadcrumb */}
      <nav className="text-xs text-gray-500">
        <Link href="/personas" className="hover:text-gray-300">
          Personas
        </Link>{' '}
        <span className="text-gray-700">/</span>{' '}
        <span className="text-gray-300">{persona.company_name}</span>
      </nav>

      {/* Header */}
      <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-2xl font-bold text-white">{persona.company_name}</h1>
            <p className="text-sm text-gray-400 mt-1">
              Persona v{persona.persona_version} · last synthesized{' '}
              {new Date(persona.last_synthesized_at).toLocaleString('en-GB')}
            </p>
            <div className="flex flex-wrap gap-2 mt-3">
              <span
                className={`text-xs border px-2 py-0.5 rounded uppercase ${QUALITY_COLOR[quality]}`}
              >
                {quality} quality
              </span>
              {persona.metadata?.unknown_sections !== undefined &&
                persona.metadata.unknown_sections > 0 && (
                  <span
                    className="text-xs bg-gray-800 border border-gray-700 text-gray-400 px-2 py-0.5 rounded"
                    title="Number of recruitment-intel sections that were 'Unknown — insufficient data' at synthesis time"
                  >
                    {persona.metadata.unknown_sections}/5 sections sparse
                  </span>
                )}
              <span className="text-xs bg-gray-800 border border-gray-700 text-gray-300 px-2 py-0.5 rounded">
                {persona.n_examples_used} outcome{persona.n_examples_used === 1 ? '' : 's'} used
              </span>
              {persona.metadata?.last_synthesizer_model && (
                <span
                  className="text-xs bg-violet-900/30 border border-violet-900/60 text-violet-300 px-2 py-0.5 rounded"
                  title="Model used by the last synthesizer run"
                >
                  ⚡ {persona.metadata.last_synthesizer_model}
                </span>
              )}
            </div>
            {persona.metadata?.synthesis_summary && (
              <p className="text-xs text-gray-400 italic mt-3 max-w-2xl">
                "{persona.metadata.synthesis_summary}"
              </p>
            )}
          </div>
          <RegeneratePersonaButton companyName={persona.company_name} size="md" showForce />
        </div>
      </section>

      {/* ATS Keyword Bank — the structural part */}
      <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <h2 className="text-sm font-semibold text-white uppercase tracking-wider mb-4">
          ATS Keyword Bank
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <KeywordList
            label="Required"
            sublabel="must appear in resume"
            items={required}
            color="emerald"
          />
          <KeywordList
            label="Boost"
            sublabel="helpful but not required"
            items={boost}
            color="blue"
          />
          <KeywordList
            label="Banned"
            sublabel="avoid these phrases here"
            items={banned}
            color="red"
          />
        </div>
        {persona.ats_keyword_bank?.raw_signals && (
          <details className="mt-4">
            <summary className="cursor-pointer text-[11px] text-gray-500 hover:text-gray-300 select-none">
              Raw signals (unstructured prose from{' '}
              <code className="text-gray-400">company_knowledge.ats_signals</code>)
            </summary>
            <pre className="mt-2 text-[11px] text-gray-400 leading-relaxed whitespace-pre-wrap bg-gray-950 border border-gray-800 rounded p-3 max-h-48 overflow-y-auto">
              {persona.ats_keyword_bank.raw_signals}
            </pre>
          </details>
        )}
      </section>

      {/* Success / failure patterns side-by-side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <PatternCard
          title="✓ Success Patterns"
          subtitle="Bullet structures from past resumes that got interviews"
          patterns={successPatterns}
          tone="emerald"
        />
        <PatternCard
          title="✗ Failure Patterns"
          subtitle="Bullet structures from past resumes that didn't"
          patterns={failurePatterns}
          tone="red"
        />
      </div>

      {/* The big system prompt — collapsible */}
      <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
        <details open>
          <summary className="cursor-pointer flex items-center justify-between mb-3 select-none">
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">
              System Prompt Template
            </h2>
            <span className="text-[11px] text-gray-500">
              {persona.system_prompt_template?.length || 0} chars · used by G2 Insider Expert
            </span>
          </summary>
          <pre className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap font-sans bg-gray-950 border border-gray-800 rounded p-4 max-h-[600px] overflow-y-auto">
            {persona.system_prompt_template}
          </pre>
        </details>
      </section>

      {/* Metadata footer */}
      <section className="bg-gray-900/50 border border-gray-800 rounded-xl p-4 text-[11px] text-gray-500 grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Meta label="Seeded from" value={persona.metadata?.seeded_from || '—'} />
        <Meta
          label="Outcomes (last synth)"
          value={String(persona.metadata?.n_outcomes_used ?? 0)}
        />
        <Meta
          label="Transcripts (last synth)"
          value={String(persona.metadata?.n_transcripts_used ?? 0)}
        />
        <Meta
          label="Last synthesizer run"
          value={
            persona.metadata?.last_synthesizer_run
              ? new Date(persona.metadata.last_synthesizer_run).toLocaleDateString('en-GB')
              : '—'
          }
        />
      </section>
    </Shell>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────

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

function KeywordList({
  label,
  sublabel,
  items,
  color,
}: {
  label: string
  sublabel: string
  items: string[]
  color: 'emerald' | 'blue' | 'red'
}) {
  const pillCls =
    color === 'emerald'
      ? 'bg-emerald-900/40 border-emerald-800 text-emerald-300'
      : color === 'blue'
      ? 'bg-blue-900/40 border-blue-800 text-blue-300'
      : 'bg-red-900/30 border-red-900/60 text-red-400'
  return (
    <div>
      <h3 className="text-[11px] uppercase tracking-wider text-gray-400 mb-1">{label}</h3>
      <p className="text-[10px] text-gray-500 mb-2">{sublabel}</p>
      {items.length === 0 ? (
        <p className="text-xs text-gray-600 italic">
          (empty — synthesizer hasn't extracted these yet)
        </p>
      ) : (
        <div className="flex flex-wrap gap-1">
          {items.map((kw, i) => (
            <span
              key={i}
              className={`text-[11px] border px-1.5 py-0.5 rounded ${pillCls}`}
            >
              {kw}
            </span>
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
  tone: 'emerald' | 'red'
}) {
  const borderCls =
    tone === 'emerald'
      ? 'border-emerald-900/60'
      : 'border-red-900/60'
  const titleCls =
    tone === 'emerald' ? 'text-emerald-300' : 'text-red-400'

  return (
    <section className={`bg-gray-900 border ${borderCls} rounded-xl p-5`}>
      <h2 className={`text-sm font-semibold uppercase tracking-wider mb-1 ${titleCls}`}>
        {title}
      </h2>
      <p className="text-[11px] text-gray-500 mb-4">{subtitle}</p>
      {patterns.length === 0 ? (
        <p className="text-xs text-gray-500 italic leading-relaxed">
          No patterns extracted yet. The synthesizer needs ≥3 outcomes for this
          company before it can confidently identify recurring patterns.
        </p>
      ) : (
        <ul className="space-y-3">
          {patterns.map((p, i) => {
            // Tolerate both shape: stringified rules OR structured objects
            if (typeof p === 'string') {
              return (
                <li key={i} className="text-xs text-gray-300 leading-relaxed">
                  • {p}
                </li>
              )
            }
            return (
              <li
                key={i}
                className="bg-gray-950/40 border border-gray-800 rounded-lg p-3 text-xs space-y-1"
              >
                {p.pattern && (
                  <p className="text-gray-200 font-medium">{p.pattern}</p>
                )}
                {p.example && (
                  <p className="text-gray-400 italic">e.g. "{p.example}"</p>
                )}
                {p.evidence && (
                  <p className="text-[10px] text-gray-500">{p.evidence}</p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-gray-600">{label}</div>
      <div className="text-xs text-gray-300 truncate" title={value}>
        {value}
      </div>
    </div>
  )
}
