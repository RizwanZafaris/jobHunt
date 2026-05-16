/**
 * /linkedin — LinkedIn presence content calendar (P1.2).
 *
 * Header: stat strip (drafts pending review · scheduled this week · posted last week)
 * Tabs:   Drafts · Scheduled · Posted
 *
 * Reads from api/linkedin.py (already wired in api/server.py). On
 * fetch failure we surface an error banner — never fall back to mock
 * data (B10 / 2026-05-16) because that hid the B1 worker outage in prod.
 *
 * Server component owns data hand-off; LinkedInClient runs the
 * interactive bits (tabs, modal, optimistic state on cards).
 */
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader, Stat } from '@/components/ui'
import { LinkedInClient } from '@/components/linkedin/LinkedInClient'
import { fetchLinkedInDrafts, fetchLinkedInSchedule, fetchCompanies } from '@/lib/api'
import type { CompanyOption } from '@/components/linkedin/GenerateModal'
import type { LinkedInDraft, LinkedInPostingSchedule } from '@/lib/types/linkedin'

// B10: empty-state schedule used when the backend is unreachable. Must
// match LinkedInPostingSchedule so LinkedInClient never crashes on null.
const EMPTY_SCHEDULE: LinkedInPostingSchedule = {
  slots: [],
  postsPerWeek: 0,
  nextSlot: null,
}

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'LinkedIn · Job Hunt',
  description:
    'News-anchored LinkedIn drafts with a user-approval gate. Never auto-posts.',
}

// BUG-019: header "Scheduled this week" used a date-window filter while
// the tab badge counted drafts with status in {approved, scheduled}. The
// two could legitimately differ, but produced confusing drift (header
// said 2, the Scheduled tab said 4). Pick one definition — drafts with
// status approved|scheduled — and use it everywhere on this page.
function countScheduled(drafts: LinkedInDraft[]): number {
  return drafts.filter((d) => d.status === 'approved' || d.status === 'scheduled').length
}

async function loadLinkedInData(): Promise<{
  drafts: LinkedInDraft[]
  schedule: LinkedInPostingSchedule
  companies: CompanyOption[]
  error: string | null
}> {
  // B10: previously fell back to MOCK_DRAFTS / MOCK_POSTING_SCHEDULE on
  // any error, hiding real backend outages. Now surface the failure with
  // a real-shape empty schedule so LinkedInClient doesn't crash on null.
  try {
    const [drafts, schedule, companiesRes] = await Promise.all([
      fetchLinkedInDrafts(),
      fetchLinkedInSchedule().catch(() => EMPTY_SCHEDULE),
      fetchCompanies().catch(() => ({ companies: [] })),
    ])
    const rawCompanies = (companiesRes?.companies ?? []) as Array<{
      id: string
      name: string
      priority?: string | null
      is_target?: boolean
    }>
    const companies: CompanyOption[] = rawCompanies
      .filter((c) => c?.id && c?.name && c.is_target !== false)
      .map((c) => ({ id: c.id, name: c.name, priority: c.priority ?? null }))
    return { drafts, schedule, companies, error: null }
  } catch (err) {
    return {
      drafts: [],
      schedule: EMPTY_SCHEDULE,
      companies: [],
      error: err instanceof Error ? err.message : String(err),
    }
  }
}

export default async function LinkedInPage() {
  const { drafts, schedule, companies, error } = await loadLinkedInData()
  // B10: stats are derived directly from `drafts` now (no MOCK_LINKEDIN_STATS).
  // postedLastWeek counts drafts marked posted in the last 7 days.
  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000
  const stats = {
    pendingReview: drafts.filter((d) => d.status === 'draft').length,
    scheduledThisWeek: countScheduled(drafts),
    postedLastWeek: drafts.filter((d) => {
      if (d.status !== 'posted' || !d.postedAt) return false
      return new Date(d.postedAt).getTime() >= sevenDaysAgo
    }).length,
  }

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Sprint 3 · MVP"
        title="LinkedIn"
        description="News-anchored drafts with a user-approval gate. Never auto-posts. You review, you copy, you publish."
      />

      {error && (
        <div
          role="alert"
          className="rounded-md border border-danger/40 bg-danger/10 px-3 py-2 text-2xs text-fg-muted"
        >
          <span className="font-medium text-fg">Couldn&rsquo;t reach the backend</span>
          <span className="text-fg-subtle"> — refresh to retry.</span>
          <span className="block text-fg-subtle/80 mt-0.5">{error}</span>
        </div>
      )}

      <section
        aria-label="LinkedIn engine summary"
        className="grid grid-cols-2 sm:grid-cols-3 gap-6 rounded-lg border border-border bg-surface p-5"
      >
        <Stat
          label="Pending review"
          value={stats.pendingReview}
          hint={stats.pendingReview > 0 ? 'Approve, edit, or reject' : 'inbox zero'}
          tone={stats.pendingReview > 0 ? 'warning' : 'default'}
        />
        <Stat
          label="Scheduled this week"
          value={stats.scheduledThisWeek}
          hint={`Cadence target: ${schedule.postsPerWeek}/week`}
        />
        <Stat
          label="Posted last week"
          value={stats.postedLastWeek}
          hint={stats.postedLastWeek === 0 ? 'kick off cadence below' : 'engagement view soon'}
          tone={stats.postedLastWeek > 0 ? 'success' : 'default'}
        />
      </section>

      <LinkedInClient initialDrafts={drafts} schedule={schedule} companies={companies} />
    </AppShell>
  )
}
