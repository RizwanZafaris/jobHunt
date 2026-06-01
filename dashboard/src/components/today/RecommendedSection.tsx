/**
 * RecommendedSection — surfaces AI-curated jobs (user_recommendation_* DB cols).
 *
 * Pinned at the top of /today. Three tier groups:
 *   apply_now (score >= 85)  — red pill, "Apply this week"
 *   strong    (score 70-84)  — amber pill, "Worth applying"
 *   stretch   (score < 70)   — slate pill, "Consider"
 *
 * Each card shows: company • title • location • days_old • reasoning text •
 * an Apply button that links straight to the job URL (opens new tab).
 *
 * Added 2026-05-27 to surface the AI-scored recommendations from
 * /actions/today/recommended (api/actions.py::get_today_recommended).
 */
import Link from 'next/link'
import { Card } from '@/components/ui/Card'
import { Pill } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'
import { EmptyState } from '@/components/ui/EmptyState'
import type {
  RecommendationTier,
  RecommendedJob,
  TodayRecommendedResponse,
} from '@/lib/types/today'

const TIER_META: Record<
  RecommendationTier,
  { label: string; tone: 'danger' | 'warning' | 'neutral'; subtitle: string }
> = {
  apply_now: {
    label: 'Apply now',
    tone: 'danger',
    subtitle: 'Top picks scored ≥ 85. Apply this week.',
  },
  strong: {
    label: 'Strong match',
    tone: 'warning',
    subtitle: 'Scored 70-84. Worth applying if you have time.',
  },
  stretch: {
    label: 'Stretch',
    tone: 'neutral',
    subtitle: 'Scored < 70. Director-track or domain-adjacent.',
  },
}

const TIER_ORDER: RecommendationTier[] = ['apply_now', 'strong', 'stretch']

function freshnessPill(daysOld: number | null): string | null {
  if (daysOld === null) return null
  if (daysOld <= 1) return 'today'
  if (daysOld <= 3) return `${daysOld}d ago`
  if (daysOld <= 7) return `${daysOld}d`
  return `${daysOld}d stale`
}

function RecommendedCard({ job }: { job: RecommendedJob }) {
  const fresh = freshnessPill(job.discovered_at_days_old)
  return (
    <li className="border-b border-border last:border-b-0 px-4 py-3 hover:bg-surface-raised/40 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-semibold text-fg truncate">
              {job.company}
            </span>
            {job.archetype && (
              <Pill tone="neutral" size="xs">{job.archetype}</Pill>
            )}
            {job.recommendation_score !== null && (
              <span className="text-2xs font-mono tabular-nums text-fg-subtle">
                score {job.recommendation_score}
              </span>
            )}
            {fresh && (
              <span
                className={`text-2xs tabular-nums ${
                  job.discovered_at_days_old !== null && job.discovered_at_days_old > 7
                    ? 'text-warning'
                    : 'text-fg-subtle'
                }`}
              >
                · {fresh}
              </span>
            )}
          </div>
          <p className="mt-0.5 text-sm text-fg leading-snug truncate">
            {job.title}
          </p>
          {job.location && (
            <p className="text-2xs text-fg-subtle mt-0.5">
              <Icon name="map-pin" size={10} className="inline mr-1" />
              {job.location}
            </p>
          )}
          {job.recommendation_reasoning && (
            <p className="text-2xs text-fg-muted mt-1.5 leading-relaxed">
              {job.recommendation_reasoning}
            </p>
          )}
        </div>
        <div className="shrink-0">
          {job.url && (
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover transition-colors min-h-9 whitespace-nowrap"
            >
              Apply
              <Icon name="external-link" size={10} />
            </a>
          )}
        </div>
      </div>
    </li>
  )
}

function TierGroup({
  tier,
  jobs,
}: {
  tier: RecommendationTier
  jobs: RecommendedJob[]
}) {
  const meta = TIER_META[tier]
  if (jobs.length === 0) return null
  return (
    <section
      aria-labelledby={`rec-${tier}-title`}
      className="space-y-2"
    >
      <header className="flex items-baseline justify-between gap-3 flex-wrap">
        <div className="flex items-baseline gap-2">
          <h3
            id={`rec-${tier}-title`}
            className="text-sm font-semibold text-fg tracking-tight"
          >
            {meta.label}
          </h3>
          <Pill tone={meta.tone} size="xs">
            {jobs.length}
          </Pill>
        </div>
        <p className="text-2xs text-fg-subtle">{meta.subtitle}</p>
      </header>
      <Card padding="none">
        <ul aria-label={`${meta.label} jobs`}>
          {jobs.map((j) => (
            <RecommendedCard key={j.id} job={j} />
          ))}
        </ul>
      </Card>
    </section>
  )
}

export interface RecommendedSectionProps {
  data: TodayRecommendedResponse | null
  errorMessage?: string | null
}

export function RecommendedSection({ data, errorMessage }: RecommendedSectionProps) {
  // Hide the whole section if there are no recommendations and no error —
  // it's noise on a quiet day. If there IS an error, show a small banner
  // so the user knows the section is broken rather than empty.
  if (errorMessage) {
    return (
      <section className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-2xs">
        <p className="text-fg">
          <span className="font-medium">Recommended jobs unavailable</span>
          <span className="text-fg-subtle"> — {errorMessage}</span>
        </p>
      </section>
    )
  }
  if (!data || data.total === 0) {
    return null
  }

  return (
    <section
      aria-labelledby="recommended-section-title"
      className="space-y-4 rounded-lg border border-accent/30 bg-accent/5 p-4"
    >
      <header className="flex items-baseline justify-between gap-3 flex-wrap">
        <div>
          <h2
            id="recommended-section-title"
            className="text-base font-semibold text-fg tracking-tight"
          >
            Recommended for you
          </h2>
          <p className="text-2xs text-fg-subtle mt-0.5">
            {data.total} jobs scored against your resume · curated by AI
          </p>
        </div>
        <Link
          href="/applications"
          className="text-2xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline"
        >
          See all applications
        </Link>
      </header>
      <div className="space-y-4">
        {TIER_ORDER.map((tier) => (
          <TierGroup key={tier} tier={tier} jobs={data.by_tier[tier] ?? []} />
        ))}
      </div>
    </section>
  )
}

export default RecommendedSection
