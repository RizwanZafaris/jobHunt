import { fetchKeywords } from '@/lib/profile-api'
import KeywordExplorer from '@/components/KeywordExplorer'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { Stat } from '@/components/ui/Stat'
import { Pill } from '@/components/ui/Pill'
import { EmptyState } from '@/components/ui/EmptyState'

export const revalidate = 300

// Single accent for all categories — relevance is conveyed by the bar size,
// not a rainbow of saturated chips. Caller can still pass any custom map.
const FLAT_CATEGORY_CLS = 'bg-surface-raised text-fg-muted border-border'

export default async function KeywordsPage() {
  let data
  let error: string | null = null
  try {
    data = await fetchKeywords()
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load keywords'
  }

  if (error || !data) {
    return (
      <AppShell wide>
        <PageHeader eyebrow="Profile" title="Keyword intelligence" />
        <EmptyState
          icon="tag"
          title="Keywords not loaded"
          description={error ?? 'No keyword data yet.'}
        />
      </AppShell>
    )
  }

  const totalOccurrences = data.categories.reduce((sum, c) => sum + c.total_occurrences, 0)
  const colorMap: Record<string, string> = {}
  for (const c of data.categories) colorMap[c.category] = FLAT_CATEGORY_CLS

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Profile"
        title="Keyword intelligence"
        description={`ATS-relevance map across ${data.keywords.length.toLocaleString()} keywords from your resume corpus.`}
      />

      <Card padding="lg">
        <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-5">
          <Stat label="Categories" value={data.categories.length} />
          <Stat label="Keywords" value={data.keywords.length.toLocaleString()} />
          <Stat label="Occurrences" value={totalOccurrences.toLocaleString()} />
        </dl>
      </Card>

      <section aria-label="Categories" className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
        {data.categories.map((c) => (
          <Card key={c.category} padding="sm">
            <div className="text-2xs uppercase tracking-wider text-fg-subtle">{c.category}</div>
            <div className="text-2xl font-semibold text-fg mt-1 tnum leading-none">{c.keyword_count}</div>
            <div className="text-2xs text-fg-muted mt-1">
              {c.total_occurrences.toLocaleString()} occurrences · avg {c.avg_strength}
            </div>
          </Card>
        ))}
      </section>

      <section className="space-y-3">
        {data.categories.map((c) => (
          <Card key={c.category} title={c.category} description={`${c.keyword_count} keywords · ${c.total_occurrences.toLocaleString()} occurrences`}>
            <div className="flex flex-wrap gap-1.5">
              {c.top_keywords.map((kw) => (
                <Pill key={kw}>{kw}</Pill>
              ))}
            </div>
          </Card>
        ))}
      </section>

      <KeywordExplorer keywords={data.keywords} categoryColors={colorMap} />
    </AppShell>
  )
}
