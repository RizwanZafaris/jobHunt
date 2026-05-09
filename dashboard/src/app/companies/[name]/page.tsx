import Link from 'next/link'
import { fetchCompanyKnowledge } from '@/lib/profile-api'
import CompanyResearchButton from '@/components/CompanyResearchButton'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Pill } from '@/components/ui/Pill'
import { Icon, IconName } from '@/components/ui/Icon'

export const dynamic = 'force-dynamic'

interface SectionMeta {
  label: string
  icon: IconName
}

const SECTION_META: Record<string, SectionMeta> = {
  overview:        { label: 'Overview',          icon: 'building' },
  news:            { label: 'News',              icon: 'note' },
  funding:         { label: 'Funding',           icon: 'currency' },
  culture:         { label: 'Culture',           icon: 'coffee' },
  tech_stack:      { label: 'Tech stack',        icon: 'sliders' },
  strategy:        { label: 'Strategy',          icon: 'target' },
  challenges:      { label: 'Challenges',        icon: 'alert-triangle' },
  competitors:     { label: 'Competitors',       icon: 'graph' },
  recruitment_process: { label: 'Recruitment process', icon: 'document' },
  resume_dos_donts:    { label: "Resume do's & don'ts", icon: 'pencil' },
  ats_signals:         { label: 'ATS signals',          icon: 'tag' },
  interview_format:    { label: 'Interview format',     icon: 'note' },
  hiring_signals:      { label: 'What they value',      icon: 'star' },
}

const RECRUITMENT_SECTIONS = [
  'recruitment_process',
  'resume_dos_donts',
  'ats_signals',
  'interview_format',
  'hiring_signals',
]

interface CompanyData {
  company?: {
    category?: string | null
    priority?: string | null
    careers_url?: string | null
    last_scanned_at?: string | null
  } | null
  knowledge?: Array<{ section: string; content: string; source_url: string | null }>
}

export default async function CompanyDetailPage({ params }: { params: { name: string } }) {
  const name = decodeURIComponent(params.name)
  let data: CompanyData | undefined
  let error: string | null = null
  try {
    data = await fetchCompanyKnowledge(name)
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load'
  }

  const company = data?.company
  const knowledge = data?.knowledge || []
  const knowledgeBySection: Record<string, { content: string; source_url: string | null }> = {}
  for (const k of knowledge) knowledgeBySection[k.section] = k

  const priorityTone = company?.priority === 'high' ? 'success' : company?.priority === 'medium' ? 'info' : 'neutral'

  return (
    <AppShell>
      <nav aria-label="Breadcrumb" className="text-2xs text-fg-subtle">
        <Link href="/companies" className="hover:text-fg inline-flex items-center gap-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded">
          <Icon name="chevron-right" size={12} className="rotate-180" />
          All target companies
        </Link>
      </nav>

      <PageHeader
        eyebrow="Company"
        title={name}
        actions={<CompanyResearchButton companyName={name} />}
      />

      {company && (
        <div className="flex flex-wrap gap-1.5 items-center -mt-3">
          {company.category && <Pill>{company.category}</Pill>}
          {company.priority && (
            <Pill tone={priorityTone as 'success' | 'info' | 'neutral'}>
              {company.priority} priority
            </Pill>
          )}
          {company.careers_url && (
            <a
              href={company.careers_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-2xs text-info hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
            >
              Careers <Icon name="arrow-up-right" size={11} />
            </a>
          )}
          {company.last_scanned_at && (
            <span className="text-2xs text-fg-subtle">
              last scanned {new Date(company.last_scanned_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
            </span>
          )}
        </div>
      )}

      {error && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger">{error}</p>
        </Card>
      )}

      {!error && knowledge.length === 0 && (
        <EmptyState
          icon="search"
          title="No research yet"
          description="Click 'Research' above to have CompanyAgent build a deep intel pack: business overview, recent news, culture, recruitment process, ATS signals, resume do's and don'ts, interview format, and what they actually value in candidates."
          hint="Takes 30–90s per company. Runs in background."
        />
      )}

      {knowledge.length > 0 && (
        <>
          <section aria-labelledby="recruitment-intel">
            <h2 id="recruitment-intel" className="text-base font-semibold text-fg mb-3 flex items-center gap-2">
              <Icon name="target" size={14} className="text-fg-muted" />
              Recruitment intelligence
              <span className="text-2xs text-fg-subtle font-normal">— how to apply here</span>
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {RECRUITMENT_SECTIONS.map((s) => {
                const k = knowledgeBySection[s]
                if (!k) return null
                return <KnowledgeCard key={s} section={s} content={k.content} sourceUrl={k.source_url} />
              })}
            </div>
          </section>

          <section aria-labelledby="company-intel">
            <h2 id="company-intel" className="text-base font-semibold text-fg mb-3 flex items-center gap-2">
              <Icon name="building" size={14} className="text-fg-muted" />
              Company intelligence
              <span className="text-2xs text-fg-subtle font-normal">— context for tailoring</span>
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {Object.keys(SECTION_META)
                .filter((s) => !RECRUITMENT_SECTIONS.includes(s))
                .map((s) => {
                  const k = knowledgeBySection[s]
                  if (!k) return null
                  return <KnowledgeCard key={s} section={s} content={k.content} sourceUrl={k.source_url} />
                })}
            </div>
          </section>
        </>
      )}
    </AppShell>
  )
}

function KnowledgeCard({
  section,
  content,
  sourceUrl,
}: {
  section: string
  content: string
  sourceUrl: string | null
}) {
  const meta = SECTION_META[section] || { label: section, icon: 'document' as IconName }
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <Icon name={meta.icon} size={14} className="text-fg-muted" />
          {meta.label}
        </span>
      }
      actions={
        sourceUrl ? (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-2xs text-info hover:underline inline-flex items-center gap-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
          >
            Source <Icon name="arrow-up-right" size={10} />
          </a>
        ) : null
      }
    >
      <p className="text-xs text-fg-muted leading-relaxed whitespace-pre-wrap">{content}</p>
    </Card>
  )
}
