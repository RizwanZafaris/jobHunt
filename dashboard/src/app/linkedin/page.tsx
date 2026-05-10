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

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'LinkedIn · Job Hunt',
  description:
    'News-anchored LinkedIn drafts with a user-approval gate. Never auto-posts.',
}

export default function LinkedInPage() {
  // TODO: replace with parallel fetch of /linkedin/drafts, /linkedin/posting-schedule
  //       once api/linkedin.py is wired into api/server.py — see api/LINKEDIN.md.
  const drafts = MOCK_DRAFTS
  const stats = MOCK_LINKEDIN_STATS
  const schedule = MOCK_POSTING_SCHEDULE

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

      <LinkedInClient initialDrafts={drafts} schedule={schedule} />
    </AppShell>
  )
}
