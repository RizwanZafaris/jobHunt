import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { TabStrip } from '@/components/ui/TabStrip'
import { Personas } from '@/components/insights/Personas'
import { Costs } from '@/components/insights/Costs'
import { System } from '@/components/insights/System'
import { Analytics } from '@/components/insights/Analytics'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Insights · Job Hunt',
  description: 'Personas, cost telemetry, pattern analytics, and the strategic orchestrator — in one place.',
}

type InsightTab = 'personas' | 'costs' | 'analytics' | 'system'

const TABS: { id: InsightTab; label: string; icon: 'brain' | 'currency' | 'rocket' | 'bar-chart-3' }[] = [
  { id: 'personas',  label: 'Personas',  icon: 'brain' },
  { id: 'costs',     label: 'Costs',     icon: 'currency' },
  { id: 'analytics', label: 'Analytics', icon: 'bar-chart-3' },
  { id: 'system',    label: 'System',    icon: 'rocket' },
]

function isInsightTab(value: string | undefined): value is InsightTab {
  return value === 'personas' || value === 'costs' || value === 'analytics' || value === 'system'
}

interface InsightsPageProps {
  searchParams?: Promise<{ tab?: string | string[] }>
}

const HEADERS: Record<InsightTab, { eyebrow: string; title: string; description: string }> = {
  personas: {
    eyebrow: 'Knowledge',
    title: 'Company personas',
    description:
      'Per-company system prompts the G2 Insider Expert loads. Refreshed weekly (Sundays 03:00 GST).',
  },
  costs: {
    eyebrow: 'Telemetry',
    title: 'LLM cost observability',
    description:
      'Per-call cost and latency for every model invocation. Time windows are UTC.',
  },
  analytics: {
    eyebrow: 'Patterns',
    title: 'Pattern analytics',
    description:
      'Application funnel, rejection clusters, and per-company cost efficiency. Every number is live from a named SQL view — never a hardcoded placeholder.',
  },
  system: {
    eyebrow: 'Orchestration',
    title: 'System — Boss agent',
    description:
      'Chat with the strategic orchestrator. Live access to your top jobs, recent builds, and active applications.',
  },
}

export default async function InsightsPage(props: InsightsPageProps) {
  const searchParams = await props.searchParams;
  const raw = Array.isArray(searchParams?.tab) ? searchParams?.tab[0] : searchParams?.tab
  const active: InsightTab = isInsightTab(raw) ? raw : 'personas'
  const header = HEADERS[active]

  return (
    <AppShell wide={active === 'costs' || active === 'analytics'}>
      <PageHeader
        eyebrow={header.eyebrow}
        title={header.title}
        description={header.description}
      />

      <TabStrip
        items={TABS}
        active={active}
        basePath="/insights"
        ariaLabel="Insights sections"
      />

      <div className="pt-2">
        {active === 'personas' && <Personas />}
        {active === 'costs' && <Costs />}
        {active === 'analytics' && <Analytics />}
        {active === 'system' && <System />}
      </div>
    </AppShell>
  )
}
