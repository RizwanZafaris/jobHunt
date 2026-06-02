/**
 * HighFitJourneysSection — FRD-16 "High-fit — prepped for you" feed.
 *
 * Pinned at the very top of /today. When a job scores >= 90, the backend
 * auto-fires a journey that builds the resume (G2), interview prep (G3), and
 * network intros (people-finder). This section surfaces those journeys as
 * launch cards: at-a-glance per-leg status + deep-links straight into the
 * relevant workspace tab, so the user just reviews → downloads → applies →
 * preps → asks for a referral.
 *
 * Server-rendered (data fetched in /today/page.tsx). Point-in-time status;
 * /today is force-dynamic so each visit re-reads. Auto-hides on quiet days.
 */
import Link from 'next/link'
import { Card } from '@/components/ui/Card'
import { Pill } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'
import type { IconName } from '@/components/ui/Icon'
import type {
  Journey,
  JourneyLeg,
  JourneyLegStatus,
  JourneyStatus,
  JourneysResponse,
} from '@/lib/types/today'

const STATUS_META: Record<
  JourneyStatus,
  { label: string; tone: 'success' | 'warning' | 'info' | 'danger' }
> = {
  converged: { label: 'Ready', tone: 'success' },
  partial: { label: 'Partly ready', tone: 'warning' },
  running: { label: 'Building…', tone: 'info' },
  failed: { label: 'Failed', tone: 'danger' },
}

type LegKey = 'resume' | 'prep' | 'network'

const LEG_META: Record<LegKey, { label: string; icon: IconName; tab: string }> = {
  resume: { label: 'Resume', icon: 'document', tab: 'resume' },
  prep: { label: 'Interview prep', icon: 'brain', tab: 'interview' },
  network: { label: 'Intros', icon: 'users', tab: 'network' },
}

const LEG_ORDER: LegKey[] = ['resume', 'prep', 'network']

function legView(status: JourneyLegStatus): { text: string; cls: string } {
  switch (status) {
    case 'succeeded':
      return { text: 'Ready', cls: 'text-success' }
    case 'running':
    case 'queued':
      return { text: 'Building…', cls: 'text-fg-muted' }
    case 'failed':
    case 'cancelled':
      return { text: 'Failed', cls: 'text-warning' }
    default:
      return { text: '—', cls: 'text-fg-subtle' }
  }
}

function LegChip({
  jobId,
  meta,
  leg,
}: {
  jobId: number
  meta: { label: string; icon: IconName; tab: string }
  leg: JourneyLeg
}) {
  const v = legView(leg.status)
  return (
    <Link
      href={`/applications/${jobId}/workspace?tab=${meta.tab}`}
      className="flex items-center gap-1.5 rounded-md border border-border bg-surface px-2 py-1 hover:bg-surface-raised transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      aria-label={`${meta.label}: ${v.text}`}
    >
      <Icon name={meta.icon} size={11} className="text-fg-subtle shrink-0" />
      <span className="text-2xs font-medium text-fg">{meta.label}</span>
      <span className={`text-2xs tabular-nums ${v.cls}`}>· {v.text}</span>
    </Link>
  )
}

function JourneyCard({ j }: { j: Journey }) {
  const meta = STATUS_META[j.status] ?? STATUS_META.running
  return (
    <li className="border-b border-border last:border-b-0 px-4 py-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-semibold text-fg truncate">
              {j.company ?? 'Unknown company'}
            </span>
            {j.trigger_score !== null && (
              <Pill tone="success" size="xs">
                {j.trigger_score} fit
              </Pill>
            )}
            <Pill tone={meta.tone} size="xs">
              {meta.label}
            </Pill>
          </div>
          <p className="mt-0.5 text-sm text-fg leading-snug truncate">
            {j.title ?? `Job #${j.job_id}`}
          </p>
          <div className="mt-2 flex items-center gap-1.5 flex-wrap">
            {LEG_ORDER.map((key) => (
              <LegChip key={key} jobId={j.job_id} meta={LEG_META[key]} leg={j.legs[key]} />
            ))}
          </div>
        </div>
        <div className="shrink-0">
          <Link
            href={`/applications/${j.job_id}/workspace`}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover transition-colors min-h-9 whitespace-nowrap focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            Review
            <Icon name="arrow-right" size={11} />
          </Link>
        </div>
      </div>
    </li>
  )
}

export interface HighFitJourneysSectionProps {
  data: JourneysResponse | null
  errorMessage?: string | null
}

export function HighFitJourneysSection({
  data,
  errorMessage,
}: HighFitJourneysSectionProps) {
  if (errorMessage) {
    return (
      <section className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-2xs">
        <p className="text-fg">
          <span className="font-medium">High-fit prep unavailable</span>
          <span className="text-fg-subtle"> — {errorMessage}</span>
        </p>
      </section>
    )
  }
  const journeys = data?.journeys ?? []
  if (journeys.length === 0) {
    return null // quiet day — no high-fit journeys to surface
  }

  return (
    <section
      aria-labelledby="highfit-section-title"
      className="space-y-3 rounded-lg border border-accent/40 bg-accent/5 p-4"
    >
      <header className="flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <h2
            id="highfit-section-title"
            className="text-base font-semibold text-fg tracking-tight flex items-center gap-1.5"
          >
            <Icon name="sparkles" size={15} className="text-accent" />
            High-fit — prepped for you
          </h2>
          <p className="text-2xs text-fg-subtle mt-0.5">
            {journeys.length} job{journeys.length === 1 ? '' : 's'} scored ≥ 90 ·
            resume, interview prep &amp; intros auto-built
          </p>
        </div>
      </header>
      <Card padding="none">
        <ul aria-label="High-fit prepped jobs">
          {journeys.map((j) => (
            <JourneyCard key={j.journey_id} j={j} />
          ))}
        </ul>
      </Card>
    </section>
  )
}

export default HighFitJourneysSection
