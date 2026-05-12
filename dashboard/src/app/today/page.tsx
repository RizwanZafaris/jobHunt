/**
 * /today — the new home.
 *
 * One ranked list answering: what should I do right now?
 * Data comes from /actions/today (api/actions.py). The endpoint returns
 * a stable shape with actions[], total, counts; on failure we surface a
 * clean error state rather than crashing the route.
 */
import Link from 'next/link'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { EmptyState } from '@/components/ui/EmptyState'
import { TodayActionCard } from '@/components/today/TodayActionCard'
import { fetchTodayActions, type FetchTodayResponse } from '@/lib/api'
import { MOCK_TODAY_ACTIONS } from '@/lib/mock/today'
import type { TodayAction, TodayActionKind } from '@/lib/types/today'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Today · Job Hunt',
  description: 'A single ranked list answering: what should I do right now?',
}

// Was 5 — bumped to 10 (2026-05-11) after production data showed users had
// 6 resume_ready cards in the top 6 (Adyen ×3, Adecco, Finkraft, Ventula),
// pushing high-score targets like Visa (rank 10) and Marqeta (rank 13)
// below the visible fold. 10 covers the score≥85 band comfortably.
const VISIBLE_LIMIT = 10

interface FetchOutcome {
  ok: boolean
  actions: TodayAction[]
  total: number
  counts: Record<string, number>
  errorMessage: string | null
  isMock: boolean
}

async function loadTodayActions(): Promise<FetchOutcome> {
  try {
    const data: FetchTodayResponse = await fetchTodayActions(VISIBLE_LIMIT * 2)
    return {
      ok: true,
      actions: data.actions ?? [],
      total: data.total ?? 0,
      counts: data.counts ?? {},
      errorMessage: null,
      isMock: false,
    }
  } catch (err) {
    // Backend unreachable in dev / first deploy — render the mock so the page
    // is still useful, but surface a small banner so the user knows.
    const message = err instanceof Error ? err.message : String(err)
    return {
      ok: false,
      actions: MOCK_TODAY_ACTIONS,
      total: MOCK_TODAY_ACTIONS.length,
      counts: {},
      errorMessage: message,
      isMock: true,
    }
  }
}

export default async function TodayPage() {
  const { actions, total, errorMessage, isMock } = await loadTodayActions()
  const visible = actions.slice(0, VISIBLE_LIMIT)
  const overflow = Math.max(0, total - visible.length)

  // BUG-010 fix: derive the strip counts from the VISIBLE cards, not from
  // the API's `counts` summary. Previously the strip said e.g. "1 Ready to
  // apply" while the page rendered 4 green Ready cards because the API
  // counts queue-total but the page is truncated to VISIBLE_LIMIT and the
  // user has no way to reconcile the two. Counting locally guarantees the
  // strip is always the index of the list directly underneath it.
  const visibleCounts = visible.reduce<Record<TodayActionKind, number>>(
    (acc, a) => {
      acc[a.kind] = (acc[a.kind] ?? 0) + 1
      return acc
    },
    {} as Record<TodayActionKind, number>,
  )

  // Use Date in render but force UTC for deterministic SSR output —
  // see BUG-017. The eyebrow ("Tuesday, 12 May") is purely informational
  // so a fixed locale + timezone is safe.
  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    timeZone: 'UTC',
  })

  // Tiny stat strip — counts derived from the cards visible on this page.
  const interestingCounts = (
    [
      ['linkedin_post_due',     'Visibility post',  'Approved LinkedIn drafts scheduled for today'],
      ['resume_ready',          'Ready to apply',    'Cards below with a tailored resume already built'],
      ['score_high_no_resume',  'Resumes to build',  'Cards below where the score is ≥85 but no resume yet'],
      ['stale_application',     'Stale apps',        'Cards below — applied 7+ days ago, no outcome logged'],
      ['persona_stale',         'Stale personas',    'Cards below — persona not refreshed in 14+ days'],
    ] as const
  )
    .map(([k, label, tip]) => ({ k, label, tip, n: visibleCounts[k] ?? 0 }))
    .filter((row) => row.n > 0)

  return (
    <AppShell>
      <PageHeader
        eyebrow={today}
        title="Today"
        description="The shortest list of moves the agent thinks will move the needle. Top of the stack first."
      />

      {isMock && errorMessage && (
        <div
          role="alert"
          className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-2xs text-fg-muted"
        >
          <span className="font-medium text-fg">Live data unavailable</span>
          <span className="text-fg-subtle"> — showing example actions.</span>
          <span className="block text-fg-subtle/80 mt-0.5">{errorMessage}</span>
        </div>
      )}

      {interestingCounts.length > 0 && (
        <ul
          className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-fg-subtle"
          aria-label="Today's queue counts"
        >
          {interestingCounts.map(({ k, label, tip, n }) => (
            <li
              key={k}
              className="flex items-center gap-1"
              title={tip}
            >
              <span className="font-mono tabular-nums text-fg">{n}</span>
              <span>{label}</span>
            </li>
          ))}
        </ul>
      )}

      {visible.length === 0 ? (
        <EmptyState
          icon="check"
          title="Inbox zero"
          description="Nothing pressing. Run a discovery pass or check tomorrow's queue."
          action={
            <Link
              href="/targets"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover transition-colors min-h-9"
            >
              Open targets
            </Link>
          }
        />
      ) : (
        <ol className="flex flex-col gap-3" aria-label="Today's ranked actions">
          {visible.map((a) => (
            <li key={a.id}>
              <TodayActionCard action={a} />
            </li>
          ))}
        </ol>
      )}

      {overflow > 0 && (
        <div className="pt-2">
          <Link
            href="/applications"
            className="text-xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
          >
            View all ({total})
          </Link>
        </div>
      )}
    </AppShell>
  )
}
