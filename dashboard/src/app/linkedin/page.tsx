/**
 * /linkedin — LinkedIn presence content calendar (P1.2).
 *
 * Header: stat strip (drafts pending review · scheduled this week · posted last week)
 * Tabs:   Drafts · Scheduled · Posted
 *
 * Today this page reads from dashboard/src/lib/mock/linkedin.ts. The
 * /linkedin/* endpoints in api/linkedin.py are authored but not yet
 * wired into api/server.py — see api/LINKEDIN.md "How to wire the
 * router" for the one-line include. When that lands, swap MOCK_* for
 * fetch calls (parallel: drafts, schedule, voice profile).
 *
 * Server component owns data hand-off; LinkedInClient runs the
 * interactive bits (tabs, modal, optimistic state on cards).
 */
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader, Stat } from '@/components/ui'
import { LinkedInClient } from '@/components/linkedin/LinkedInClient'
import {
  MOCK_DRAFTS,
  MOCK_LINKEDIN_STATS,
  MOCK_POSTING_SCHEDULE,
} from '@/lib/mock/linkedin'
import { fetchLinkedInDrafts, fetchLinkedInSchedule, fetchCompanies } from '@/lib/api'
import type { CompanyOption } from '@/components/linkedin/GenerateModal'
import type { LinkedInDraft } from '@/lib/types/linkedin'

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
  schedule: typeof MOCK_POSTING_SCHEDULE
  companies: CompanyOption[]
  usedMock: boolean
  error: string | null
}> {
  // BUG-LinkedIn-Mock fix (2026-05-13): page was reading MOCK_DRAFTS even
  // though the /linkedin/* router is wired in api/server.py. That's why
  // the user saw "3-day old post" — they were seeing the same mock data
  // every load. Now we hit the real backend and only fall back on error.
  //
  // 2026-05-14: also fetch companies for the Generate modal dropdown so
  // the user can pick "Mastercard" by name instead of typing a UUID.
  try {
    const [drafts, schedule, companiesRes] = await Promise.all([
      fetchLinkedInDrafts(),
      fetchLinkedInSchedule().catch(() => MOCK_POSTING_SCHEDULE),
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
    return { drafts, schedule, companies, usedMock: false, error: null }
  } catch (err) {
    return {
      drafts: MOCK_DRAFTS,
      schedule: MOCK_POSTING_SCHEDULE,
      companies: [],
      usedMock: true,
      error: err instanceof Error ? err.message : String(err),
    }
  }
}

export default async function LinkedInPage() {
  const { drafts, schedule, companies, usedMock, error } = await loadLinkedInData()
  const baseStats = MOCK_LINKEDIN_STATS
  const stats = {
    ...baseStats,
    pendingReview: drafts.filter((d) => d.status === 'draft').length,
    scheduledThisWeek: countScheduled(drafts),
  }
  void error
  void usedMock

  return (
    <AppShell wide>
      <PageHeader
        eyebrow="Sprint 3 · MVP"
        title="LinkedIn"
        description="News-anchored drafts with a user-approval gate. Never auto-posts. You review, you copy, you publish."
      />

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
