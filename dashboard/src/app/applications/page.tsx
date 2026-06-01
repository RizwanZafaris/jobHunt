import Link from 'next/link'
import { fetchApplications } from '@/lib/profile-api'
import { fetchIncomingJobs, fetchPostOfTheDay } from '@/lib/api'
import ApplicationsBoard from '@/components/ApplicationsBoard'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { PostOfTheDayCard } from '@/components/linkedin/PostOfTheDayCard'

export const dynamic = 'force-dynamic'

export default async function ApplicationsPage() {
  // Stream D (2026-05-13): /applications page is the user's tracking
  // "board". Now embeds (1) incoming-jobs lane on the left so they
  // can see new high-score discoveries inline, and (2) today's
  // LinkedIn post-of-day card so the board doubles as a single
  // place to take action.
  let data
  let error: string | null = null
  try {
    data = await fetchApplications()
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load applications'
  }

  const [incoming, post] = await Promise.all([
    fetchIncomingJobs(10).catch(() => []),
    fetchPostOfTheDay().catch(() => null),
  ])

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Funnel"
        title="Applications pipeline"
        description="Track each application as it moves through the funnel. Status changes save to Supabase immediately."
        actions={
          <span
            className="text-2xs text-fg-subtle cursor-help"
            title="Distinct applications submitted (any status). Does not include resumes built but not applied — those live on /today as “Ready to apply”."
          >
            Total: <span className="text-fg font-semibold tnum">{data?.total ?? 0}</span>
          </span>
        }
      />

      {/* Stream D: today's LinkedIn post lives here too — this page is
       * the user's tracking board, so seeing today's draft alongside
       * the pipeline closes the loop. */}
      <PostOfTheDayCard post={post} emptyCta="hide" />

      {/* Stream D: Incoming lane — high-score jobs without an application
       * row yet. User feedback: "Application page need to add all new
       * job come in so I can take action". */}
      {incoming.length > 0 && (
        <section className="rounded-lg border border-emerald-500/30 bg-emerald-500/[0.04] p-4">
          <div className="flex items-baseline justify-between gap-2">
            <h2 className="text-sm font-semibold text-fg">
              Incoming · {incoming.length} new high-score job{incoming.length === 1 ? '' : 's'}
            </h2>
            <span className="text-3xs text-fg-subtle">
              Score ≥ 85, not yet in pipeline
            </span>
          </div>
          <ul className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {incoming.map((j) => (
              <li
                key={j.job_id}
                className="rounded-md border border-border bg-surface p-3"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-3xs uppercase text-fg-subtle tracking-wider">
                    {j.company || `Job ${j.job_id}`}
                  </span>
                  <span className="font-mono tabular-nums text-2xs font-semibold text-emerald-300">
                    {j.score}
                    {j.letter_grade ? ` · ${j.letter_grade}` : ''}
                  </span>
                </div>
                <p className="mt-1 text-xs text-fg line-clamp-2">{j.title}</p>
                <div className="mt-2 flex items-center gap-2">
                  <Link
                    href={`/applications/${j.job_id}/workspace`}
                    className="inline-flex items-center gap-1 rounded-md bg-accent px-2 py-1 text-3xs font-semibold text-accent-fg hover:bg-accent-hover"
                  >
                    Open workspace →
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {error && (
        <Card tone="danger" padding="sm">
          <p className="text-xs text-danger">{error}</p>
        </Card>
      )}

      {!error && data && <ApplicationsBoard initial={data} />}
    </AppShell>
  )
}
