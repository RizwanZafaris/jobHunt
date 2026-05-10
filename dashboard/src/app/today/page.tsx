// TODO: replace with /actions/today endpoint — see api/QUEUE.md (and _pending_endpoints.md)
import Link from 'next/link'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { EmptyState } from '@/components/ui/EmptyState'
import { TodayActionCard } from '@/components/today/TodayActionCard'
import { MOCK_TODAY_ACTIONS } from '@/lib/mock/today'
import type { TodayAction } from '@/lib/types/today'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Today · Job Hunt',
  description: 'A single ranked list answering: what should I do right now?',
}

const VISIBLE_LIMIT = 5

export default function TodayPage() {
  // TODO: replace with `await fetchTodayActions()` once the endpoint ships.
  const actions: TodayAction[] = MOCK_TODAY_ACTIONS
  const visible = actions.slice(0, VISIBLE_LIMIT)
  const overflow = Math.max(0, actions.length - visible.length)

  const today = new Date().toLocaleDateString('en-GB', {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  })

  return (
    <AppShell>
      <PageHeader
        eyebrow={today}
        title="Today"
        description="The shortest list of moves the agent thinks will move the needle. Top of the stack first."
      />

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
            href="/today/all"
            className="text-xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
          >
            View all ({overflow + visible.length})
          </Link>
        </div>
      )}
    </AppShell>
  )
}
