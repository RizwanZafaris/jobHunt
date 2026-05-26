/**
 * /today — the new home.
 *
 * 2026-05-26 redesign: replaces the single flat ranked list with one
 * dedicated section per card kind. Data comes from /actions/today/sections
 * (api/actions.py::get_today_sections). Each section is independently
 * collapsible + shows count + has its own empty state.
 *
 * Old flat-list response is still served by /actions/today for the few
 * dashboards that haven't migrated yet (legacy mobile bookmark, etc).
 */
import Link from 'next/link'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { EmptyState } from '@/components/ui/EmptyState'
import { TodayActionSection } from '@/components/today/TodayActionSection'
import { fetchTodaySections, fetchPostOfTheDay, type PostOfTheDay } from '@/lib/api'
import { PostOfTheDayCard } from '@/components/linkedin/PostOfTheDayCard'
import type { TodaySection, TodaySectionsResponse } from '@/lib/types/today'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Today · Job Hunt',
  description: 'Your prioritised action queue, grouped by kind.',
}

// Per-section visible default. Bumped to 5 in the sectioned redesign
// so each region shows enough to be useful without dominating the page.
// Tunable per /today render via ?per_kind=N (backend caps at 20).
const PER_KIND = 5

interface SectionsOutcome {
  ok: boolean
  sections: TodaySection[]
  total: number
  errorMessage: string | null
}

async function loadTodaySections(): Promise<SectionsOutcome> {
  try {
    const data: TodaySectionsResponse = await fetchTodaySections(PER_KIND, {
      include_empty: true,  // always render the 8 sections; empty ones show empty_state
    })
    return {
      ok: true,
      sections: data.sections ?? [],
      total: data.total ?? 0,
      errorMessage: null,
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    return {
      ok: false,
      sections: [],
      total: 0,
      errorMessage: message,
    }
  }
}

export default async function TodayPage() {
  const [{ sections, total, errorMessage, ok }, post] = await Promise.all([
    loadTodaySections(),
    fetchPostOfTheDay().catch(() => null as PostOfTheDay | null),
  ])
  const showError = !ok && errorMessage !== null

  // Tiny stat strip — derived from the sections themselves.
  // We surface only kinds with at least 1 card so the strip doesn't lie
  // (a "0 Hot leads" pill would be noise; the empty section itself
  // already conveys that).
  const strip = sections
    .filter((s) => s.count_total > 0)
    .map((s) => ({ kind: s.kind, label: s.label, n: s.count_total }))

  const today = new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
    timeZone: 'UTC',
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow={today}
        title="Today"
        description="Your day, organised by what's most perishable first."
      />

      {showError && (
        <div
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-2xs text-fg-muted"
        >
          <span className="font-medium text-fg">Couldn&rsquo;t reach the backend</span>
          <span className="text-fg-subtle"> — refresh to retry.</span>
          <span className="block text-fg-subtle/80 mt-0.5">{errorMessage}</span>
        </div>
      )}

      {/* Pinned: today's LinkedIn post (preserves Stream D placement). */}
      <PostOfTheDayCard post={post} emptyCta="generate" />

      {/* Stat strip — counts per kind, derived from sections. */}
      {strip.length > 0 && (
        <ul
          className="flex flex-wrap gap-x-4 gap-y-1 text-2xs text-fg-subtle"
          aria-label="Today's queue counts"
        >
          {strip.map(({ kind, label, n }) => (
            <li key={kind} className="flex items-center gap-1">
              <span className="font-mono tabular-nums text-fg">{n}</span>
              <span>{label}</span>
            </li>
          ))}
        </ul>
      )}

      {/* All 8 sections rendered in fixed perishability order. Empty
          sections show their empty_state copy so the layout stays
          stable across days — fewer surprises for muscle memory. */}
      {sections.length === 0 && !showError ? (
        <EmptyState
          icon="check"
          title="Inbox zero"
          description="Nothing pressing. Run a discovery pass or check tomorrow's queue."
          action={
            <Link
              href="/companies"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover transition-colors min-h-9"
            >
              Open targets
            </Link>
          }
        />
      ) : (
        <div className="flex flex-col gap-3">
          {sections.map((s) => (
            <TodayActionSection key={s.kind} section={s} />
          ))}
        </div>
      )}

      {total > 0 && (
        <div className="pt-2">
          <Link
            href="/applications"
            className="text-xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
          >
            View all applications
          </Link>
        </div>
      )}
    </AppShell>
  )
}
